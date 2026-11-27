import csv
import decimal
import io
from datetime import date, datetime, timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
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
    AttendanceLocation,
    AttendancePolicy,
)
from apps.attendance.reporting_service import (
    BD_TZ,
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
        OfficeSchedule.objects.update_or_create(
            branch=cls.branch,
            defaults={
                "office_start_time": datetime.strptime("09:00", "%H:%M").time(),
                "office_end_time": datetime.strptime("18:00", "%H:%M").time(),
                "late_after_minutes": 15,
                "working_days": ["saturday", "sunday", "monday", "tuesday", "wednesday", "thursday"],
            },
        )
        AttendancePolicy.objects.create(
            branch=cls.branch,
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
        cls.emp2_user = User.objects.create_user(
            email="emp2_ret@example.com",
            password="password123",
            role="staff",
        )
        cls.emp2_master = Employee.objects.create(
            user=cls.emp2_user,
            employee_number="EMP-RET-02",
            first_name="Second",
            last_name="Staff",
            joined_date=date(2025, 1, 1),
        )
        cls.emp2_profile = EmployeeProfile.objects.create(
            user=cls.emp2_user,
            master_employee=cls.emp2_master,
            employee_id="EMP-RET-02",
            full_name="Second Staff",
            phone="01700003002",
            branch=cls.branch,
            is_active=True,
            joined_date=date(2025, 1, 1),
        )
        cls.factory = RequestFactory()

    def _bd_dt(self, d, h, m=0, s=0):
        return datetime(d.year, d.month, d.day, h, m, s, tzinfo=BD_TZ)

    def test_1_retention_only_archived_attendance_preserved(self):
        """Retention-only archived completed attendance retains verified values with zero historical writes."""
        d = date(2026, 6, 15)  # Monday, >3 months before expired_at
        ci = self._bd_dt(d, 9, 0, 0)
        co = self._bd_dt(d, 18, 30, 0)
        exp_at = self._bd_dt(date(2026, 10, 1), 2, 0, 0)

        att = Attendance.objects.create(
            employee=self.emp_profile,
            date=d,
            attendance_type="check_in",
            type="office",
            status="on_time",
            check_in_time=ci,
            check_out_time=co,
            total_hours=decimal.Decimal("9.50"),
            overtime_minutes=30,
            is_expired=True,
            expired_at=exp_at,
        )
        AttendanceLocation.objects.create(
            attendance=att,
            event="check_out",
            latitude=23.8103,
            longitude=90.4125,
            accuracy=10.0,
            timestamp=co,
            is_expired=True,
        )

        daily = get_daily_report_data(report_date=d, employee_id=self.emp_profile.id)
        row = daily["rows"][0]
        self.assertTrue(row["is_present"])
        self.assertFalse(row["is_absent"])
        self.assertFalse(row["needs_review"])
        self.assertEqual(row["check_out_time"], co)
        self.assertEqual(row["check_out_time_str"], "18:30:00")
        self.assertEqual(row["total_hours"], 9.5)
        self.assertEqual(row["day_calc"]["overtime_minutes"], 30)

        monthly = get_monthly_report_data(year=2026, month=6, employee_id=self.emp_profile.id)
        m_row = monthly["rows"][0]
        dc = m_row["day_calcs"][d]
        self.assertTrue(dc["is_present"])
        self.assertFalse(dc["is_absent"])
        self.assertEqual(dc["total_hours"], 9.5)
        self.assertEqual(m_row["present"], 1)

        att.refresh_from_db()
        self.assertTrue(att.is_expired)
        self.assertEqual(att.expired_at, exp_at)
        self.assertEqual(att.total_hours, decimal.Decimal("9.50"))

    def test_2_ambiguous_expiry_fails_closed_no_absent_or_deduction(self):
        """Expired attendance with unknown expiry reason becomes history_unavailable/needs_review, never Absent or deducted."""
        LeaveType.objects.create(name="Default Casual", category="casual", is_default=True, default_days_per_year=10)
        d = date(2026, 8, 10)  # Monday
        att = Attendance.objects.create(
            employee=self.emp_profile,
            date=d,
            attendance_type="check_in",
            type="office",
            status="on_time",
            check_in_time=self._bd_dt(d, 9, 0, 0),
            check_out_time=self._bd_dt(d, 18, 0, 0),
            total_hours=decimal.Decimal("9.00"),
            is_expired=True,
            expired_at=None,  # Missing retention timestamp -> unknown expiry reason
        )

        daily = get_daily_report_data(report_date=d, employee_id=self.emp_profile.id)
        row = daily["rows"][0]
        self.assertEqual(row["status"], "history_unavailable")
        self.assertEqual(row["status_display"], "History Unavailable")
        self.assertFalse(row["is_present"])
        self.assertFalse(row["is_absent"])
        self.assertTrue(row["needs_review"])
        self.assertTrue(row["history_unavailable"])
        self.assertEqual(row["total_hours"], 0.0)

        out = StringIO()
        call_command("mark_daily_absences", date=d.isoformat(), stdout=out)
        self.assertEqual(AttendanceAbsentLog.objects.filter(employee=self.emp_profile, date=d).count(), 0)
        self.assertEqual(LeaveBalance.objects.filter(employee=self.emp_profile).count(), 0)

        pr = PayrollRun.objects.create(
            name="Aug 2026 History Check",
            period_start=d,
            period_end=d,
            status=PayrollRunStatus.DRAFT,
        )
        with self.assertRaises(ValidationError) as ctx:
            PayrollService.sync_payroll_inputs(pr)
        self.assertIn("unavailable or expired attendance history", str(ctx.exception).lower())

        att.refresh_from_db()
        self.assertIsNone(att.expired_at)

    def test_3_explicitly_invalidated_alongside_valid_session(self):
        """Explicitly invalidated attendance alongside a separate valid session preserves valid session only."""
        d = date(2026, 9, 14)  # Monday
        valid_ci = self._bd_dt(d, 9, 10, 0)
        valid_co = self._bd_dt(d, 13, 10, 0)

        Attendance.objects.create(
            employee=self.emp_profile,
            date=d,
            attendance_type="check_in",
            type="office",
            status="on_time",
            check_in_time=valid_ci,
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
            note="Invalidated duplicate punch by admin",
        )

        daily = get_daily_report_data(report_date=d, employee_id=self.emp_profile.id)
        row = daily["rows"][0]
        self.assertTrue(row["is_present"])
        self.assertFalse(row["is_absent"])
        self.assertFalse(row["is_late"])
        self.assertFalse(row["needs_review"])
        self.assertEqual(row["check_out_time"], valid_co)
        self.assertEqual(row["total_hours"], 4.0)

    def test_4_retention_deleted_missing_history_handling(self):
        """Deleted historical records with activity/retention metadata expose history_unavailable unless confirmed by AbsentLog."""
        d_deleted_activity = date(2026, 5, 11)  # Monday, deleted after retention
        d_confirmed_absent = date(2026, 5, 12)  # Tuesday, confirmed absent log

        # Newer retention-archived record establishes retention cutoff on 2026-06-01
        Attendance.objects.create(
            employee=self.emp2_profile,
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

        # Retained activity log for emp_profile on 2026-05-11 (Attendance row was purged)
        act = AttendanceActivityLog.objects.create(
            employee=self.emp_profile,
            action="check_in",
            description="Checked In at 09:05 AM",
        )
        AttendanceActivityLog.objects.filter(pk=act.pk).update(
            timestamp=self._bd_dt(d_deleted_activity, 9, 5, 0)
        )

        # Confirmed absence log for emp_profile on 2026-05-12
        AttendanceAbsentLog.objects.create(
            employee=self.emp_profile,
            date=d_confirmed_absent,
        )

        d1_report = get_daily_report_data(report_date=d_deleted_activity, employee_id=self.emp_profile.id)
        r1 = d1_report["rows"][0]
        self.assertEqual(r1["status"], "history_unavailable")
        self.assertFalse(r1["is_absent"])
        self.assertTrue(r1["needs_review"])
        self.assertTrue(r1["history_unavailable"])

        d2_report = get_daily_report_data(report_date=d_confirmed_absent, employee_id=self.emp_profile.id)
        r2 = d2_report["rows"][0]
        self.assertEqual(r2["status"], "absent")
        self.assertTrue(r2["is_absent"])
        self.assertFalse(r2["needs_review"])

    def test_5_report_export_payroll_parity_and_bounded_queries(self):
        """Daily/Monthly/HTML/CSV/XLSX/PDF/Payroll agree on retention_archived vs history_unavailable with bounded queries."""
        d = date(2026, 6, 16)  # Tuesday
        exp_at = self._bd_dt(date(2026, 10, 1), 2, 0, 0)
        co = self._bd_dt(d, 18, 0, 0)

        # emp_profile: retention-archived completed attendance -> Present 9.00h
        Attendance.objects.create(
            employee=self.emp_profile,
            date=d,
            attendance_type="check_in",
            type="office",
            status="on_time",
            check_in_time=self._bd_dt(d, 9, 0, 0),
            check_out_time=co,
            total_hours=decimal.Decimal("9.00"),
            is_expired=True,
            expired_at=exp_at,
        )
        # emp2_profile: unknown-reason expired attendance -> History Unavailable
        Attendance.objects.create(
            employee=self.emp2_profile,
            date=d,
            attendance_type="check_in",
            type="office",
            status="on_time",
            check_in_time=self._bd_dt(d, 9, 0, 0),
            check_out_time=co,
            total_hours=decimal.Decimal("9.00"),
            is_expired=True,
            expired_at=None,
        )

        # HTML Daily & Day Detail
        req_html = self.factory.get(f"/admin-panel/reports/daily/?date={d.isoformat()}")
        req_html.user = self.admin_user
        html_resp = DailyReportView.as_view()(req_html)
        self.assertEqual(html_resp.status_code, 200)
        self.assertIn("History Unavailable", html_resp.content.decode("utf-8"))

        req_detail = self.factory.get(f"/admin-panel/reports/day-detail/{self.emp2_profile.id}/{d.isoformat()}/")
        req_detail.user = self.admin_user
        detail_resp = EmployeeDayDetailView.as_view()(req_detail, pk=self.emp2_profile.id, date_str=d.isoformat())
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
                total_hours=decimal.Decimal("9.00"), is_expired=True, expired_at=exp_at,
            )

        with CaptureQueriesContext(connection) as ctx_large:
            get_daily_report_data(report_date=d)

        self.assertEqual(len(ctx_small), len(ctx_large))
