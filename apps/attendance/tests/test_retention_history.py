import csv
import decimal
import io
from datetime import date, datetime, timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
import openpyxl
from pypdf import PdfReader

from apps.admin_panel.views import (
    DailyReportView,
    EmployeeDayDetailView,
    ExportReportCSVView,
    ExportReportExcelView,
    ExportReportPDFView,
)
from apps.attendance.models import (
    Attendance,
    AttendanceAbsentLog,
    AttendanceActivityLog,
    AttendancePolicy,
)
from apps.attendance.reporting_service import (
    BD_TZ,
    OptimizedSchedule,
    calculate_employee_day,
    classify_attendance_expiry,
    get_daily_report_data,
    get_monthly_report_data,
    resolve_employee_day_evidence,
)
from apps.branches.models import Branch, OfficeSchedule
from apps.employees.models import Employee, EmployeeProfile
from apps.leave.models import LeaveBalance, LeaveRequest, LeaveType
from apps.payroll.models import PayrollRun, PayrollRunStatus
from apps.payroll.services import PayrollService

User = get_user_model()


class RetentionHistoryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch = Branch.objects.create(
            name="HQ Retention",
            latitude=decimal.Decimal("23.810300"),
            longitude=decimal.Decimal("90.412500"),
            radius_meters=200,
        )
        cls.other_branch = Branch.objects.create(
            name="Other Branch",
            latitude=decimal.Decimal("22.356900"),
            longitude=decimal.Decimal("91.783200"),
            radius_meters=200,
        )
        for br in (cls.branch, cls.other_branch):
            OfficeSchedule.objects.update_or_create(
                branch=br,
                defaults={
                    "office_start_time": datetime.strptime("09:00", "%H:%M").time(),
                    "office_end_time": datetime.strptime("18:00", "%H:%M").time(),
                    "late_after_minutes": 15,
                    "working_days": ["saturday", "sunday", "monday", "tuesday", "wednesday", "thursday"],
                },
            )
            AttendancePolicy.objects.create(
                branch=br,
                geofencing_policy="warning",
                allow_outside_geofence=True,
                photo_required=False,
            )

        cls.admin_user = User.objects.create_superuser(
            email="admin_ret@example.com",
            password="password123",
        )
        cls.emp_user = User.objects.create_user(
            email="emp_ret@example.com",
            password="password123",
            role="staff",
        )
        cls.emp_master = Employee.objects.create(
            user=cls.emp_user,
            employee_number="EMP-RET-01",
            first_name="Retention",
            last_name="Staff",
            joined_date=date(2025, 1, 1),
        )
        cls.emp_profile = EmployeeProfile.objects.create(
            user=cls.emp_user,
            master_employee=cls.emp_master,
            employee_id="EMP-RET-01",
            full_name="Retention Staff",
            phone="01700003001",
            branch=cls.branch,
            is_active=True,
            joined_date=date(2025, 1, 1),
        )

        cls.other_user = User.objects.create_user(
            email="emp2_ret@example.com",
            password="password123",
            role="staff",
        )
        cls.other_master = Employee.objects.create(
            user=cls.other_user,
            employee_number="EMP-RET-02",
            first_name="Other",
            last_name="BranchStaff",
            joined_date=date(2025, 1, 1),
        )
        cls.other_profile = EmployeeProfile.objects.create(
            user=cls.other_user,
            master_employee=cls.other_master,
            employee_id="EMP-RET-02",
            full_name="Other BranchStaff",
            phone="01700003002",
            branch=cls.other_branch,
            is_active=True,
            joined_date=date(2025, 1, 1),
        )
        cls.factory = RequestFactory()

    def _bd_dt(self, d, h, m=0, s=0):
        return datetime(d.year, d.month, d.day, h, m, s, tzinfo=BD_TZ)

    def test_1_fully_purged_no_metadata_cannot_become_absent(self):
        """Fully purged working day with no surviving metadata or coverage ledger -> History Unavailable, zero deduction."""
        LeaveType.objects.create(name="Default Casual", category="casual", is_default=True, default_days_per_year=10)
        d = date(2026, 4, 6)  # Fixed Monday
        frozen_now = self._bd_dt(date(2026, 11, 10), 12, 0, 0)

        with patch("django.utils.timezone.now", return_value=frozen_now), patch(
            "django.utils.timezone.localdate", return_value=date(2026, 11, 10)
        ):
            daily = get_daily_report_data(report_date=d, employee_id=self.emp_profile.id)
            row = daily["rows"][0]
            self.assertEqual(row["status"], "history_unavailable")
            self.assertEqual(row["status_display"], "History Unavailable")
            self.assertFalse(row["is_present"])
            self.assertFalse(row["is_absent"])
            self.assertTrue(row["needs_review"])
            self.assertTrue(row["history_unavailable"])
            self.assertEqual(row["day_calc"]["coverage_state"], "unknown")
            self.assertEqual(row["day_calc"]["reason_code"], "coverage_unknown")
            self.assertEqual(daily["unknown_days"], 1)
            self.assertFalse(daily["coverage_complete"])

            monthly = get_monthly_report_data(year=2026, month=4, employee_id=self.emp_profile.id)
            m_row = monthly["rows"][0]
            self.assertIsNone(m_row["att_pct"])
            self.assertEqual(m_row["att_pct_display"], "Unavailable (Incomplete)")
            self.assertFalse(m_row["coverage_complete"])
            self.assertGreater(m_row["unknown_days"], 0)
            self.assertIsNone(monthly["avg_att_pct"])

            call_command("mark_daily_absences", date=d.isoformat(), stdout=StringIO())
            self.assertEqual(AttendanceAbsentLog.objects.filter(employee=self.emp_profile, date=d).count(), 0)
            self.assertEqual(LeaveBalance.objects.count(), 0)

    @override_settings(ATTENDANCE_SYNC_GRACE_MINUTES=30)
    def test_2_explicit_complete_vs_unknown_coverage_and_shift_grace(self):
        """Explicit complete coverage produces Absent only after shift+grace; unknown coverage produces History Unavailable."""
        d = date(2026, 10, 12)  # Fixed Monday
        sched = OptimizedSchedule(self.emp_profile)

        # Before shift + 30m grace ends (18:15 < 18:30) -> Pending
        before_grace = self._bd_dt(d, 18, 15, 0)
        with patch("django.utils.timezone.now", return_value=before_grace):
            ev_pending = resolve_employee_day_evidence(
                self.emp_profile, d, [], schedule=sched, today=d, now=before_grace,
                coverage={"state": "complete", "source": "test_ledger"},
            )
            self.assertEqual(ev_pending["schedule_state"], "pending_shift")
            self.assertEqual(ev_pending["reason_code"], "shift_pending")
            res_pending = calculate_employee_day(
                self.emp_profile, d, [], schedule=sched, today=d,
                coverage={"state": "complete", "source": "test_ledger"},
            )
            self.assertEqual(res_pending["status"], "scheduled")
            self.assertEqual(res_pending["status_display"], "Pending")
            self.assertFalse(res_pending["is_absent"])

        # After shift + 30m grace ends (18:35 > 18:30) with complete coverage -> Absent
        after_grace = self._bd_dt(d, 18, 35, 0)
        with patch("django.utils.timezone.now", return_value=after_grace):
            res_absent = calculate_employee_day(
                self.emp_profile, d, [], schedule=sched, today=d,
                coverage={"state": "complete", "source": "test_ledger"},
            )
            self.assertEqual(res_absent["status"], "absent")
            self.assertTrue(res_absent["is_absent"])
            self.assertFalse(res_absent["needs_review"])
            self.assertEqual(res_absent["coverage_state"], "complete")
            self.assertEqual(res_absent["reason_code"], "confirmed_absent")

            # Same ended shift+grace with unknown coverage -> History Unavailable
            res_unknown = calculate_employee_day(
                self.emp_profile, d, [], schedule=sched, today=d, coverage=None,
            )
            self.assertEqual(res_unknown["status"], "history_unavailable")
            self.assertFalse(res_unknown["is_absent"])
            self.assertTrue(res_unknown["needs_review"])
            self.assertEqual(res_unknown["coverage_state"], "unknown")
            self.assertEqual(res_unknown["reason_code"], "coverage_unknown")

    def test_3_ambiguous_expiry_and_valid_independent_session(self):
        """Expired records with misleading notes remain ambiguous; valid independent sessions retain Present/values."""
        d = date(2026, 9, 14)  # Fixed Monday
        exp_at = self._bd_dt(date(2026, 12, 20), 0, 0, 0)
        frozen_now = self._bd_dt(date(2026, 11, 10), 12, 0, 0)

        valid_co = self._bd_dt(d, 13, 10, 0)
        Attendance.objects.create(
            employee=self.emp_profile,
            date=d,
            attendance_type="check_in",
            type="office",
            status="on_time",
            check_in_time=self._bd_dt(d, 9, 10, 0),
            check_out_time=valid_co,
            total_hours=decimal.Decimal("4.00"),
            is_expired=False,
        )
        expired_att = Attendance.objects.create(
            employee=self.emp_profile,
            date=d,
            attendance_type="check_in",
            type="office",
            status="late",
            check_in_time=self._bd_dt(d, 14, 0, 0),
            check_out_time=self._bd_dt(d, 19, 0, 0),
            total_hours=decimal.Decimal("5.00"),
            is_expired=True,
            expired_at=exp_at,
            note="Invalidated duplicate punch; retention archived",
        )
        self.assertEqual(classify_attendance_expiry(expired_att), "ambiguous_expired")

        with patch("django.utils.timezone.now", return_value=frozen_now), patch(
            "django.utils.timezone.localdate", return_value=date(2026, 11, 10)
        ):
            daily = get_daily_report_data(report_date=d, employee_id=self.emp_profile.id)
            row = daily["rows"][0]
            self.assertTrue(row["is_present"])
            self.assertFalse(row["is_absent"])
            self.assertFalse(row["is_late"])
            self.assertTrue(row["needs_review"])
            self.assertEqual(row["check_out_time"], valid_co)
            self.assertEqual(row["total_hours"], 4.0)
            self.assertEqual(row["day_calc"]["reason_code"], "present_with_ambiguous_expired")

    def test_4_scope_isolation_and_offline_overnight_date_semantics(self):
        """Other branch's expired records have zero effect; offline/overnight and contradicting AbsentLog handled cleanly."""
        d_prev = date(2026, 5, 11)   # Monday
        d_next = date(2026, 5, 12)   # Tuesday
        frozen_now = self._bd_dt(date(2026, 11, 10), 12, 0, 0)

        # Other branch has expired record on d_prev; baseline authorized employee result must not change when deleted
        other_exp = Attendance.objects.create(
            employee=self.other_profile,
            date=d_prev,
            attendance_type="check_in",
            type="office",
            status="on_time",
            check_in_time=self._bd_dt(d_prev, 9, 0, 0),
            check_out_time=self._bd_dt(d_prev, 18, 0, 0),
            total_hours=decimal.Decimal("9.00"),
            is_expired=True,
            expired_at=self._bd_dt(date(2026, 10, 1), 0, 0, 0),
        )

        # Overnight/offline session on d_prev synced/checked out on d_next, plus stale AbsentLog on d_prev
        AttendanceAbsentLog.objects.create(employee=self.emp_profile, date=d_prev)
        ov_att = Attendance.objects.create(
            employee=self.emp_profile,
            date=d_prev,
            attendance_type="check_in",
            type="office",
            status="on_time",
            check_in_time=self._bd_dt(d_prev, 22, 0, 0),
            check_out_time=self._bd_dt(d_next, 6, 0, 0),
            total_hours=decimal.Decimal("8.00"),
            client_event_time=self._bd_dt(d_prev, 22, 0, 0),
            synced_at=self._bd_dt(d_next, 7, 0, 0),
            is_expired=False,
        )
        act = AttendanceActivityLog.objects.create(
            employee=self.emp_profile,
            action="check_in",
            description="Offline check-in synced next morning",
        )
        AttendanceActivityLog.objects.filter(pk=act.pk).update(
            timestamp=self._bd_dt(d_next, 7, 0, 0)
        )

        with patch("django.utils.timezone.now", return_value=frozen_now), patch(
            "django.utils.timezone.localdate", return_value=date(2026, 11, 10)
        ):
            r_prev_1 = get_daily_report_data(report_date=d_prev, employee_id=self.emp_profile.id)["rows"][0]
            r_next_1 = get_daily_report_data(report_date=d_next, employee_id=self.emp_profile.id)["rows"][0]

            # Delete other branch's expired record -> authorized employee results remain identical
            other_exp.delete()
            r_prev_2 = get_daily_report_data(report_date=d_prev, employee_id=self.emp_profile.id)["rows"][0]
            r_next_2 = get_daily_report_data(report_date=d_next, employee_id=self.emp_profile.id)["rows"][0]

            # Later valid overnight Attendance supersedes stale AbsentLog on d_prev
            self.assertTrue(r_prev_1["is_present"])
            self.assertFalse(r_prev_1["is_absent"])
            self.assertEqual(r_prev_1["total_hours"], 8.0)
            self.assertEqual(r_prev_1["status"], r_prev_2["status"])
            self.assertEqual(r_next_1["status"], r_next_2["status"])
            # d_next was only the sync/upload date of d_prev's session -> coverage_unknown, not missing_attendance_for_activity
            self.assertEqual(r_next_2["day_calc"]["reason_code"], "coverage_unknown")

    def test_5_export_payroll_parity_zero_writes_and_bounded_queries(self):
        """HTML/popup/CSV/XLSX/PDF/payroll parity on History Unavailable vs active, zero writes, bounded queries."""
        d = date(2026, 6, 16)  # Fixed Tuesday
        co = self._bd_dt(d, 18, 0, 0)
        frozen_now = self._bd_dt(date(2026, 11, 10), 12, 0, 0)

        att_active = Attendance.objects.create(
            employee=self.emp_profile,
            date=d,
            attendance_type="check_in",
            type="office",
            status="on_time",
            check_in_time=self._bd_dt(d, 9, 0, 0),
            check_out_time=co,
            total_hours=decimal.Decimal("9.00"),
            is_expired=False,
        )
        att_exp = Attendance.objects.create(
            employee=self.other_profile,
            date=d,
            attendance_type="check_in",
            type="office",
            status="on_time",
            check_in_time=self._bd_dt(d, 9, 0, 0),
            check_out_time=co,
            total_hours=decimal.Decimal("9.00"),
            is_expired=True,
            expired_at=self._bd_dt(date(2026, 10, 1), 2, 0, 0),
        )

        with patch("django.utils.timezone.now", return_value=frozen_now), patch(
            "django.utils.timezone.localdate", return_value=date(2026, 11, 10)
        ):
            req_html = self.factory.get(f"/admin-panel/reports/daily/?date={d.isoformat()}")
            req_html.user = self.admin_user
            html_resp = DailyReportView.as_view()(req_html)
            self.assertEqual(html_resp.status_code, 200)
            self.assertIn("History Unavailable", html_resp.content.decode("utf-8"))

            req_detail = self.factory.get(f"/admin-panel/reports/day-detail/{self.other_profile.id}/{d.isoformat()}/")
            req_detail.user = self.admin_user
            detail_resp = EmployeeDayDetailView.as_view()(req_detail, pk=self.other_profile.id, date_str=d.isoformat())
            self.assertEqual(detail_resp.status_code, 200)
            self.assertNotIn("18:00:00", detail_resp.content.decode("utf-8"))

            req_csv = self.factory.get(f"/admin-panel/reports/export/csv/?date={d.isoformat()}")
            req_csv.user = self.admin_user
            csv_rows = list(csv.reader(io.StringIO(ExportReportCSVView.as_view()(req_csv).content.decode("utf-8-sig"))))
            by_id_csv = {r[2]: r for r in csv_rows[1:]}
            self.assertEqual(by_id_csv["EMP-RET-01"][5], "18:00:00")
            self.assertEqual(by_id_csv["EMP-RET-01"][6], "9.00")
            self.assertEqual(by_id_csv["EMP-RET-02"][8], "History Unavailable")
            self.assertEqual(by_id_csv["EMP-RET-02"][6], "")

            req_xl = self.factory.get(f"/admin-panel/reports/export/excel/?date={d.isoformat()}")
            req_xl.user = self.admin_user
            wb = openpyxl.load_workbook(io.BytesIO(ExportReportExcelView.as_view()(req_xl).content))
            ws = wb.active
            xl_by_id = {ws.cell(row=r_idx, column=3).value: r_idx for r_idx in range(5, ws.max_row + 1)}
            self.assertEqual(ws.cell(row=xl_by_id["EMP-RET-01"], column=6).value, "18:00:00")
            self.assertEqual(ws.cell(row=xl_by_id["EMP-RET-02"], column=9).value, "History Unavailable")

            req_pdf = self.factory.get(f"/admin-panel/reports/export/pdf/?date={d.isoformat()}")
            req_pdf.user = self.admin_user
            pdf_text = " ".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(ExportReportPDFView.as_view()(req_pdf).content)).pages)
            self.assertIn("History Unavailable", pdf_text)

            pr = PayrollRun.objects.create(
                name="Jun 2026 Coverage Check",
                period_start=d,
                period_end=d,
                status=PayrollRunStatus.DRAFT,
            )
            with self.assertRaises(ValidationError) as ctx:
                PayrollService.sync_payroll_inputs(pr)
            self.assertIn("unavailable or expired attendance history", str(ctx.exception).lower())

            att_active.refresh_from_db()
            att_exp.refresh_from_db()
            self.assertFalse(att_active.is_expired)
            self.assertTrue(att_exp.is_expired)

            with CaptureQueriesContext(connection) as ctx_small:
                get_daily_report_data(report_date=d)

            for i in range(18):
                u = User.objects.create_user(email=f"ret_bulk_{i}@example.com", password="password123", role="staff")
                m = Employee.objects.create(
                    user=u, employee_number=f"EMP-RET-B{i:02d}", first_name="Bulk", last_name=f"R{i}", joined_date=date(2025, 1, 1)
                )
                ep = EmployeeProfile.objects.create(
                    user=u, master_employee=m, employee_id=f"EMP-RET-B{i:02d}", full_name=f"Bulk R{i}",
                    phone=f"0173000{i:04d}", branch=self.branch, is_active=True, joined_date=date(2025, 1, 1),
                )
                Attendance.objects.create(
                    employee=ep, date=d, attendance_type="check_in", type="office", status="on_time",
                    check_in_time=self._bd_dt(d, 9, 0, 0), check_out_time=co,
                    total_hours=decimal.Decimal("9.00"), is_expired=False,
                )

            with CaptureQueriesContext(connection) as ctx_large:
                get_daily_report_data(report_date=d)

            self.assertEqual(len(ctx_small), len(ctx_large))
