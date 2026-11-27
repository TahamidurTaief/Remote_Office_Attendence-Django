import csv
import decimal
import io
from datetime import date, datetime
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.test import RequestFactory, TestCase
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
    classify_attendance_expiry,
    get_daily_report_data,
    get_monthly_report_data,
)
from apps.branches.models import Branch, OfficeSchedule
from apps.employees.models import Employee, EmployeeProfile
from apps.leave.models import LeaveBalance, LeaveType
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

    def test_1_misleading_notes_do_not_prove_invalidation_or_retention(self):
        """Free-text note keywords never prove invalidation or retention; valid independent session is preserved."""
        d = date(2026, 9, 14)  # Monday
        exp_at = self._bd_dt(date(2026, 12, 20), 0, 0, 0)

        # Standalone expired record with misleading note text -> must fail closed to ambiguous_expired / History Unavailable
        att_only = Attendance.objects.create(
            employee=self.other_profile,
            date=d,
            attendance_type="check_in",
            type="office",
            status="on_time",
            check_in_time=self._bd_dt(d, 9, 0, 0),
            check_out_time=self._bd_dt(d, 18, 0, 0),
            total_hours=decimal.Decimal("9.00"),
            is_expired=True,
            expired_at=exp_at,
            note="Invalid card reader at gate; retention archived",
        )
        self.assertEqual(classify_attendance_expiry(att_only), "ambiguous_expired")

        other_daily = get_daily_report_data(report_date=d, employee_id=self.other_profile.id)
        other_row = other_daily["rows"][0]
        self.assertEqual(other_row["status"], "history_unavailable")
        self.assertFalse(other_row["is_present"])
        self.assertFalse(other_row["is_absent"])
        self.assertTrue(other_row["needs_review"])

        # Valid independent session alongside expired session with misleading note -> valid session preserved, expired flagged
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
        Attendance.objects.create(
            employee=self.emp_profile,
            date=d,
            attendance_type="check_in",
            type="office",
            status="late",
            check_in_time=self._bd_dt(d, 14, 0, 0),
            check_out_time=self._bd_dt(d, 19, 0, 0),
            total_hours=decimal.Decimal("5.00"),
            is_expired=True,
            note="Invalidated duplicate punch",
        )

        daily = get_daily_report_data(report_date=d, employee_id=self.emp_profile.id)
        row = daily["rows"][0]
        self.assertTrue(row["is_present"])
        self.assertFalse(row["is_absent"])
        self.assertFalse(row["is_late"])
        self.assertTrue(row["needs_review"])
        self.assertEqual(row["check_out_time"], valid_co)
        self.assertEqual(row["total_hours"], 4.0)

    def test_2_ambiguous_expiry_and_exact_cutoff_boundary_fail_closed(self):
        """Record age + expired_at (including exact 3-month cutoff) is not sufficient retention proof -> History Unavailable."""
        LeaveType.objects.create(name="Default Casual", category="casual", is_default=True, default_days_per_year=10)
        d_exact_cutoff = date(2026, 7, 1)   # Exactly 3 months before 2026-10-01
        d_older = date(2026, 6, 15)         # >3 months before 2026-10-01
        exp_at = self._bd_dt(date(2026, 10, 1), 2, 0, 0)

        for d in (d_exact_cutoff, d_older):
            att = Attendance.objects.create(
                employee=self.emp_profile,
                date=d,
                attendance_type="check_in",
                type="office",
                status="on_time",
                check_in_time=self._bd_dt(d, 9, 0, 0),
                check_out_time=self._bd_dt(d, 18, 0, 0),
                total_hours=decimal.Decimal("9.00"),
                overtime_minutes=45,
                is_expired=True,
                expired_at=exp_at,
            )
            self.assertEqual(classify_attendance_expiry(att), "ambiguous_expired")

            daily = get_daily_report_data(report_date=d, employee_id=self.emp_profile.id)
            row = daily["rows"][0]
            self.assertEqual(row["status"], "history_unavailable")
            self.assertEqual(row["status_display"], "History Unavailable")
            self.assertFalse(row["is_present"])
            self.assertFalse(row["is_absent"])
            self.assertTrue(row["needs_review"])
            self.assertEqual(row["total_hours"], 0.0)
            self.assertEqual(row["day_calc"]["overtime_minutes"], 0)

            call_command("mark_daily_absences", date=d.isoformat(), stdout=StringIO())
            self.assertEqual(AttendanceAbsentLog.objects.filter(employee=self.emp_profile, date=d).count(), 0)
            self.assertEqual(LeaveBalance.objects.filter(employee=self.emp_profile).count(), 0)

            att.refresh_from_db()
            self.assertEqual(att.total_hours, decimal.Decimal("9.00"))
            self.assertEqual(att.overtime_minutes, 45)

        pr = PayrollRun.objects.create(
            name="Jul 2026 History Check",
            period_start=d_exact_cutoff,
            period_end=d_exact_cutoff,
            status=PayrollRunStatus.DRAFT,
        )
        with self.assertRaises(ValidationError) as ctx:
            PayrollService.sync_payroll_inputs(pr)
        self.assertIn("unavailable or expired attendance history", str(ctx.exception).lower())

    def test_3_cross_branch_isolation_no_global_min_date_leakage(self):
        """Unrelated employee/branch expired records never alter another employee's historical day result."""
        d_target = date(2026, 5, 11)  # Monday

        # Unrelated employee in other_branch has expired record on a later date (2026-06-01)
        Attendance.objects.create(
            employee=self.other_profile,
            date=date(2026, 6, 1),
            attendance_type="check_in",
            type="office",
            status="on_time",
            check_in_time=self._bd_dt(date(2026, 6, 1), 9, 0, 0),
            check_out_time=self._bd_dt(date(2026, 6, 1), 18, 0, 0),
            total_hours=decimal.Decimal("9.00"),
            is_expired=True,
            expired_at=self._bd_dt(date(2026, 10, 1), 0, 0, 0),
        )

        # emp_profile has no attendance and no activity log on 2026-05-11 -> unaffected by other_branch
        daily_before = get_daily_report_data(report_date=d_target, employee_id=self.emp_profile.id)
        r_before = daily_before["rows"][0]
        self.assertEqual(r_before["status"], "absent")
        self.assertTrue(r_before["is_absent"])
        self.assertFalse(r_before["needs_review"])

    def test_4_fully_purged_history_vs_employee_scoped_activity(self):
        """Missing rows alone do not prove deletion; employee-scoped AttendanceActivityLog triggers History Unavailable."""
        d_purged_no_meta = date(2026, 4, 6)   # Monday, fully purged with zero surviving metadata
        d_purged_with_act = date(2026, 4, 7)  # Tuesday, Attendance purged but AttendanceActivityLog survives

        act = AttendanceActivityLog.objects.create(
            employee=self.emp_profile,
            action="check_in",
            description="Checked In at 09:05 AM",
        )
        AttendanceActivityLog.objects.filter(pk=act.pk).update(
            timestamp=self._bd_dt(d_purged_with_act, 9, 5, 0)
        )

        # Employee-scoped activity log with missing Attendance -> History Unavailable + needs_review=True
        rep_act = get_daily_report_data(report_date=d_purged_with_act, employee_id=self.emp_profile.id)
        row_act = rep_act["rows"][0]
        self.assertEqual(row_act["status"], "history_unavailable")
        self.assertFalse(row_act["is_absent"])
        self.assertFalse(row_act["is_present"])
        self.assertTrue(row_act["needs_review"])

        # Fully purged day with zero surviving metadata -> does not invent Present or infer global deletion
        rep_none = get_daily_report_data(report_date=d_purged_no_meta, employee_id=self.emp_profile.id)
        row_none = rep_none["rows"][0]
        self.assertEqual(row_none["status"], "absent")
        self.assertFalse(row_none["is_present"])

    def test_5_report_export_payroll_parity_and_bounded_queries(self):
        """HTML/popup/CSV/XLSX/PDF/Monthly/Payroll agree on History Unavailable vs active, with bounded queries."""
        d = date(2026, 6, 16)  # Tuesday
        co = self._bd_dt(d, 18, 0, 0)

        # emp_profile: active completed attendance -> Present 9.00h
        Attendance.objects.create(
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
        # other_profile: expired attendance -> History Unavailable
        Attendance.objects.create(
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

        # Monthly parity
        monthly = get_monthly_report_data(year=2026, month=6, employee_id=self.other_profile.id)
        dc = monthly["rows"][0]["day_calcs"][d]
        self.assertEqual(dc["status"], "history_unavailable")
        self.assertFalse(dc["is_absent"])
        self.assertTrue(dc["needs_review"])

        # HTML Daily & Day Detail
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

        # CSV
        req_csv = self.factory.get(f"/admin-panel/reports/export/csv/?date={d.isoformat()}")
        req_csv.user = self.admin_user
        csv_rows = list(csv.reader(io.StringIO(ExportReportCSVView.as_view()(req_csv).content.decode("utf-8-sig"))))
        by_id_csv = {r[2]: r for r in csv_rows[1:]}
        self.assertEqual(by_id_csv["EMP-RET-01"][5], "18:00:00")
        self.assertEqual(by_id_csv["EMP-RET-01"][6], "9.00")
        self.assertEqual(by_id_csv["EMP-RET-02"][8], "History Unavailable")
        self.assertEqual(by_id_csv["EMP-RET-02"][6], "")

        # XLSX
        req_xl = self.factory.get(f"/admin-panel/reports/export/excel/?date={d.isoformat()}")
        req_xl.user = self.admin_user
        wb = openpyxl.load_workbook(io.BytesIO(ExportReportExcelView.as_view()(req_xl).content))
        ws = wb.active
        xl_by_id = {ws.cell(row=r_idx, column=3).value: r_idx for r_idx in range(5, ws.max_row + 1)}
        self.assertEqual(ws.cell(row=xl_by_id["EMP-RET-01"], column=6).value, "18:00:00")
        self.assertEqual(ws.cell(row=xl_by_id["EMP-RET-02"], column=9).value, "History Unavailable")

        # PDF
        req_pdf = self.factory.get(f"/admin-panel/reports/export/pdf/?date={d.isoformat()}")
        req_pdf.user = self.admin_user
        pdf_text = " ".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(ExportReportPDFView.as_view()(req_pdf).content)).pages)
        self.assertIn("History Unavailable", pdf_text)

        # Bounded query count across 2 vs 20 employees
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
                total_hours=decimal.Decimal("9.00"), is_expired=True,
                expired_at=self._bd_dt(date(2026, 10, 1), 2, 0, 0),
            )

        with CaptureQueriesContext(connection) as ctx_large:
            get_daily_report_data(report_date=d)

        self.assertEqual(len(ctx_small), len(ctx_large))
