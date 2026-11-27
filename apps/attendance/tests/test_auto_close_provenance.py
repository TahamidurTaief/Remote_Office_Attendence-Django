import csv
import io
import decimal
from datetime import date, datetime, timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
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
    AttendanceAuditLog,
    AttendanceCorrectionRequest,
    AttendanceLocation,
    AttendancePolicy,
)
from apps.attendance.pdf_export import render_individual_attendance_pdf
from apps.attendance.reporting_service import (
    BD_TZ,
    get_daily_report_data,
    get_monthly_report_data,
    resolve_session_checkout,
)
from apps.branches.models import Branch, OfficeSchedule
from apps.employees.models import Employee, EmployeeProfile
from apps.payroll.models import PayrollRun, PayrollRunStatus
from apps.payroll.services import PayrollService

User = get_user_model()


class AutoCloseProvenanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch = Branch.objects.create(
            name="HQ Provenance",
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
            },
        )
        cls.policy = AttendancePolicy.objects.create(
            branch=cls.branch,
            geofencing_policy="warning",
            allow_outside_geofence=True,
            photo_required=False,
        )

        cls.admin_user = User.objects.create_superuser(
            email="admin_prov@example.com",
            password="password123",
        )
        cls.hr_user = User.objects.create_user(
            email="hr_prov@example.com",
            password="password123",
            role="hr",
        )
        cls.emp_user = User.objects.create_user(
            email="emp_prov@example.com",
            password="password123",
            role="staff",
        )
        cls.emp_master = Employee.objects.create(
            user=cls.emp_user,
            employee_number="EMP-PROV-01",
            first_name="Provenance",
            last_name="Staff",
            joined_date=date(2025, 1, 1),
        )
        cls.emp_profile = EmployeeProfile.objects.create(
            user=cls.emp_user,
            master_employee=cls.emp_master,
            employee_id="EMP-PROV-01",
            full_name="Provenance Staff",
            phone="01700001001",
            branch=cls.branch,
            is_active=True,
            joined_date=date(2025, 1, 1),
        )

        cls.genuine_user = User.objects.create_user(
            email="gen_prov@example.com",
            password="password123",
            role="staff",
        )
        cls.genuine_master = Employee.objects.create(
            user=cls.genuine_user,
            employee_number="EMP-PROV-02",
            first_name="Genuine",
            last_name="Staff",
            joined_date=date(2025, 1, 1),
        )
        cls.genuine_profile = EmployeeProfile.objects.create(
            user=cls.genuine_user,
            master_employee=cls.genuine_master,
            employee_id="EMP-PROV-02",
            full_name="Genuine Staff",
            phone="01700001002",
            branch=cls.branch,
            is_active=True,
            joined_date=date(2025, 1, 1),
        )
        cls.factory = RequestFactory()

    def _bd_dt(self, d, h, m=0, s=0):
        return datetime(d.year, d.month, d.day, h, m, s, tzinfo=BD_TZ)

    def test_1_auto_closed_only_provenance_and_zero_historical_writes(self):
        """Proven auto-only closure -> Present + Missing Checkout, empty Out, zero invented hours/OT, zero DB writes."""
        d = date(2026, 10, 12)
        fabricated_out = self._bd_dt(d, 18, 0, 0)
        original_note = f"[Auto-closed: unclosed session from {d.strftime('%d/%m/%Y')}]"

        att = Attendance.objects.create(
            employee=self.emp_profile,
            date=d,
            attendance_type="check_in",
            type="office",
            status="on_time",
            check_in_time=self._bd_dt(d, 9, 5, 0),
            check_out_time=fabricated_out,
            total_hours=decimal.Decimal("8.92"),
            overtime_minutes=45,
            note=original_note,
        )

        daily = get_daily_report_data(report_date=d, employee_id=self.emp_profile.id)
        row = daily["rows"][0]
        self.assertTrue(row["is_present"])
        self.assertTrue(row["is_open_session"])
        self.assertTrue(row["is_missing_checkout"])
        self.assertIn("Missing Checkout", row["status_display"])
        self.assertIsNone(row["check_out_time"])
        self.assertEqual(row["check_out_time_str"], "")
        self.assertEqual(row["total_hours"], 0.0)
        self.assertEqual(row["day_calc"]["overtime_minutes"], 0)
        self.assertFalse(row["needs_review"])

        monthly = get_monthly_report_data(year=2026, month=10, employee_id=self.emp_profile.id)
        m_row = monthly["rows"][0]
        dc = m_row["day_calcs"][d]
        self.assertTrue(dc["is_present"])
        self.assertTrue(dc["is_missing_checkout"])
        self.assertIsNone(dc["check_out_time"])
        self.assertEqual(dc["total_hours"], 0.0)
        self.assertEqual(dc["overtime_minutes"], 0)

        # Stored historical values must remain completely untouched
        att.refresh_from_db()
        self.assertEqual(att.check_out_time, fabricated_out)
        self.assertEqual(att.total_hours, decimal.Decimal("8.92"))
        self.assertEqual(att.overtime_minutes, 45)
        self.assertEqual(att.note, original_note)

    def test_2_actual_1800_preserved_and_ambiguous_flagged_for_payroll_review(self):
        """Genuine 18:00 checkout is preserved; ambiguous provenance is flagged and blocks payroll sync."""
        d = date(2026, 10, 13)
        genuine_out = self._bd_dt(d, 18, 0, 0)
        att_gen = Attendance.objects.create(
            employee=self.genuine_profile,
            date=d,
            attendance_type="check_in",
            type="office",
            status="on_time",
            check_in_time=self._bd_dt(d, 9, 0, 0),
            check_out_time=genuine_out,
            total_hours=decimal.Decimal("9.00"),
        )
        AttendanceLocation.objects.create(
            attendance=att_gen,
            event="check_out",
            latitude=23.8103,
            longitude=90.4125,
            accuracy=10.0,
            timestamp=genuine_out,
        )

        # Ambiguous: Auto-closed marker at 18:00, but conflicting checkout location at 17:15
        att_amb = Attendance.objects.create(
            employee=self.emp_profile,
            date=d,
            attendance_type="check_in",
            type="office",
            status="on_time",
            check_in_time=self._bd_dt(d, 9, 0, 0),
            check_out_time=genuine_out,
            total_hours=decimal.Decimal("9.00"),
            note=f"[Auto-closed: unclosed session from {d.strftime('%d/%m/%Y')}]",
        )
        AttendanceLocation.objects.create(
            attendance=att_amb,
            event="check_out",
            latitude=23.8103,
            longitude=90.4125,
            accuracy=10.0,
            timestamp=self._bd_dt(d, 17, 15, 0),
        )

        daily = get_daily_report_data(report_date=d)
        by_emp = {r["employee"].id: r for r in daily["rows"]}

        gen_row = by_emp[self.genuine_profile.id]
        self.assertFalse(gen_row["is_missing_checkout"])
        self.assertEqual(gen_row["check_out_time"], genuine_out)
        self.assertEqual(gen_row["check_out_time_str"], "18:00:00")
        self.assertEqual(gen_row["total_hours"], 9.0)
        self.assertFalse(gen_row["needs_review"])

        amb_row = by_emp[self.emp_profile.id]
        self.assertTrue(amb_row["needs_review"])
        self.assertTrue(amb_row["checkout_needs_review"])
        self.assertIsNone(amb_row["check_out_time"])
        self.assertEqual(amb_row["total_hours"], 0.0)
        self.assertEqual(daily["review_needed"], 1)

        # Payroll sync flags ambiguous checkout for review instead of guessing
        pr = PayrollRun.objects.create(
            name="Oct 2026 Review Check",
            period_start=d,
            period_end=d,
            status=PayrollRunStatus.DRAFT,
        )
        with self.assertRaises(ValidationError) as ctx:
            PayrollService.sync_payroll_inputs(pr)
        self.assertIn("ambiguous checkout", str(ctx.exception).lower())

    def test_3_later_authorized_correction_overrides_older_auto_close(self):
        """Old Auto-closed note followed by approved correction overrides auto-close evidence."""
        d = date(2026, 10, 14)
        old_fabricated = self._bd_dt(d, 18, 0, 0)
        corrected_out = self._bd_dt(d, 19, 30, 0)

        att = Attendance.objects.create(
            employee=self.emp_profile,
            date=d,
            attendance_type="check_in",
            type="office",
            status="on_time",
            check_in_time=self._bd_dt(d, 9, 0, 0),
            check_out_time=corrected_out,
            total_hours=decimal.Decimal("10.50"),
            note=f"[Auto-closed: unclosed session from {d.strftime('%d/%m/%Y')}]",
        )
        AttendanceCorrectionRequest.objects.create(
            attendance=att,
            reason="Actual late departure verified",
            status="approved",
            check_out_time=corrected_out,
            note="",  # Blank note leaves historical Auto-closed text in Attendance.note
            reviewed_by=self.hr_user,
            reviewed_at=timezone.now(),
        )
        AttendanceAuditLog.objects.create(
            attendance=att,
            action="correction",
            old_check_out_time=old_fabricated,
            new_check_out_time=corrected_out,
            reason="Actual late departure verified",
            changed_by=self.hr_user,
        )

        daily = get_daily_report_data(report_date=d, employee_id=self.emp_profile.id)
        row = daily["rows"][0]
        self.assertFalse(row["is_open_session"])
        self.assertFalse(row["is_missing_checkout"])
        self.assertFalse(row["needs_review"])
        self.assertEqual(row["check_out_time"], corrected_out)
        self.assertEqual(row["check_out_time_str"], "19:30:00")
        self.assertEqual(row["total_hours"], 10.5)

        res = resolve_session_checkout(att)
        self.assertEqual(res["provenance"], "corrected")

    def test_4_mixed_fabricated_and_verified_sessions_same_day(self):
        """Earlier fabricated session + later genuinely completed session on same day."""
        d = date(2026, 10, 15)
        # Session 1: Fabricated auto-close at 18:00 (no checkout evidence)
        Attendance.objects.create(
            employee=self.emp_profile,
            date=d,
            attendance_type="check_in",
            type="office",
            status="on_time",
            check_in_time=self._bd_dt(d, 9, 0, 0),
            check_out_time=self._bd_dt(d, 18, 0, 0),
            total_hours=decimal.Decimal("9.00"),
            overtime_minutes=60,
            note=f"[Auto-closed: unclosed session from {d.strftime('%d/%m/%Y')}]",
        )
        # Session 2: Independently verified completed session (14:00 - 17:00 = 3.00h)
        s2_out = self._bd_dt(d, 17, 0, 0)
        s2 = Attendance.objects.create(
            employee=self.emp_profile,
            date=d,
            attendance_type="check_in",
            type="office",
            status="on_time",
            check_in_time=self._bd_dt(d, 14, 0, 0),
            check_out_time=s2_out,
            total_hours=decimal.Decimal("3.00"),
            overtime_minutes=15,
        )
        AttendanceLocation.objects.create(
            attendance=s2,
            event="check_out",
            latitude=23.8103,
            longitude=90.4125,
            accuracy=10.0,
            timestamp=s2_out,
        )

        daily = get_daily_report_data(report_date=d, employee_id=self.emp_profile.id)
        row = daily["rows"][0]
        self.assertTrue(row["is_present"])
        self.assertTrue(row["is_open_session"])
        self.assertTrue(row["is_missing_checkout"])
        self.assertIsNone(row["check_out_time"])
        self.assertEqual(row["check_out_time_str"], "")
        self.assertEqual(row["total_hours"], 3.0)
        self.assertEqual(row["day_calc"]["overtime_minutes"], 15)

    def test_5_html_csv_xlsx_pdf_consistency_and_bounded_queries(self):
        """HTML, CSV, XLSX, and PDF exports all hide fabricated 18:00 and keep genuine 18:00 with bounded queries."""
        d = date(2026, 10, 16)
        fabricated_out = self._bd_dt(d, 18, 0, 0)
        genuine_out = self._bd_dt(d, 18, 0, 0)

        # Employee 1: Proven auto-closed at 18:00:00
        Attendance.objects.create(
            employee=self.emp_profile,
            date=d,
            attendance_type="check_in",
            type="office",
            status="on_time",
            check_in_time=self._bd_dt(d, 9, 0, 0),
            check_out_time=fabricated_out,
            total_hours=decimal.Decimal("9.00"),
            note=f"[Auto-closed: unclosed session from {d.strftime('%d/%m/%Y')}]",
        )
        # Employee 2: Genuine checkout at 18:00:00
        att_gen = Attendance.objects.create(
            employee=self.genuine_profile,
            date=d,
            attendance_type="check_in",
            type="office",
            status="on_time",
            check_in_time=self._bd_dt(d, 9, 0, 0),
            check_out_time=genuine_out,
            total_hours=decimal.Decimal("9.00"),
        )
        AttendanceLocation.objects.create(
            attendance=att_gen,
            event="check_out",
            latitude=23.8103,
            longitude=90.4125,
            accuracy=10.0,
            timestamp=genuine_out,
        )

        # 1. HTML Daily Report
        req_html = self.factory.get(f"/admin-panel/reports/daily/?date={d.isoformat()}")
        req_html.user = self.admin_user
        html_resp = DailyReportView.as_view()(req_html)
        self.assertEqual(html_resp.status_code, 200)
        html_text = html_resp.content.decode("utf-8")
        self.assertIn("Missing Checkout", html_text)

        # 2. HTML Employee Day Detail
        req_detail = self.factory.get(f"/admin-panel/reports/day-detail/{self.emp_profile.id}/{d.isoformat()}/")
        req_detail.user = self.admin_user
        detail_resp = EmployeeDayDetailView.as_view()(req_detail, pk=self.emp_profile.id, date_str=d.isoformat())
        self.assertEqual(detail_resp.status_code, 200)
        self.assertNotIn("18:00:00", detail_resp.content.decode("utf-8"))

        # 3. CSV Export (auto-closed employee -> empty Out, 0 hours; genuine -> 18:00:00)
        req_csv = self.factory.get(f"/admin-panel/reports/export/csv/?date={d.isoformat()}")
        req_csv.user = self.admin_user
        csv_resp = ExportReportCSVView.as_view()(req_csv)
        csv_rows = list(csv.reader(io.StringIO(csv_resp.content.decode("utf-8-sig"))))
        by_id_csv = {r[2]: r for r in csv_rows[1:]}
        self.assertEqual(by_id_csv["EMP-PROV-01"][5], "")
        self.assertEqual(by_id_csv["EMP-PROV-01"][6], "")
        self.assertEqual(by_id_csv["EMP-PROV-02"][5], "18:00:00")
        self.assertEqual(by_id_csv["EMP-PROV-02"][6], "9.00")

        # 4. XLSX Export
        req_xl = self.factory.get(f"/admin-panel/reports/export/excel/?date={d.isoformat()}")
        req_xl.user = self.admin_user
        xl_resp = ExportReportExcelView.as_view()(req_xl)
        wb = openpyxl.load_workbook(io.BytesIO(xl_resp.content))
        ws = wb.active
        xl_by_id = {ws.cell(row=r_idx, column=3).value: r_idx for r_idx in range(5, ws.max_row + 1)}
        self.assertEqual(ws.cell(row=xl_by_id["EMP-PROV-01"], column=6).value, "—")
        self.assertEqual(ws.cell(row=xl_by_id["EMP-PROV-01"], column=7).value, "0.00h")
        self.assertEqual(ws.cell(row=xl_by_id["EMP-PROV-02"], column=6).value, "18:00:00")

        # 5. Daily PDF & Individual Monthly PDF
        req_pdf = self.factory.get(f"/admin-panel/reports/export/pdf/?date={d.isoformat()}&employee={self.emp_profile.id}")
        req_pdf.user = self.admin_user
        pdf_resp = ExportReportPDFView.as_view()(req_pdf)
        pdf_text = " ".join((page.extract_text() or "") for page in PdfReader(io.BytesIO(pdf_resp.content)).pages)
        self.assertIn("Missing Checkout", pdf_text)
        self.assertNotIn("18:00:00", pdf_text)

        indiv_data = get_monthly_report_data(year=2026, month=10, employee_id=self.emp_profile.id)
        indiv_pdf = render_individual_attendance_pdf(self.emp_profile, 2026, 10, indiv_data)
        indiv_text = " ".join((page.extract_text() or "") for page in PdfReader(io.BytesIO(indiv_pdf)).pages)
        self.assertNotIn("18:00:00", indiv_text)

        # 6. Bounded bulk provenance queries: 2 vs 20 auto-closed employees must have identical query count
        with CaptureQueriesContext(connection) as ctx_small:
            get_daily_report_data(report_date=d)

        for i in range(18):
            u = User.objects.create_user(email=f"bulk_prov_{i}@example.com", password="password123", role="staff")
            m = Employee.objects.create(
                user=u, employee_number=f"EMP-BULK-{i:02d}", first_name="Bulk", last_name=f"U{i}", joined_date=date(2025, 1, 1)
            )
            ep = EmployeeProfile.objects.create(
                user=u, master_employee=m, employee_id=f"EMP-BULK-{i:02d}", full_name=f"Bulk U{i}",
                phone=f"0171000{i:04d}", branch=self.branch, is_active=True, joined_date=date(2025, 1, 1),
            )
            Attendance.objects.create(
                employee=ep, date=d, attendance_type="check_in", type="office", status="on_time",
                check_in_time=self._bd_dt(d, 9, 0, 0), check_out_time=fabricated_out,
                total_hours=decimal.Decimal("9.00"),
                note=f"[Auto-closed: unclosed session from {d.strftime('%d/%m/%Y')}]",
            )

        with CaptureQueriesContext(connection) as ctx_large:
            get_daily_report_data(report_date=d)

        self.assertEqual(len(ctx_small), len(ctx_large))
