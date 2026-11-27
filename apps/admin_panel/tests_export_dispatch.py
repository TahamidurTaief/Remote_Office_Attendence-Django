from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.attendance.models import Attendance
from apps.branches.models import Branch, OfficeSchedule
from apps.employees.models import Department, Designation, Employee, EmployeeProfile

User = get_user_model()
BD_TZ = ZoneInfo("Asia/Dhaka")
FROZEN_NOW = datetime(2026, 11, 25, 14, 0, 0, tzinfo=BD_TZ)


class ExportDispatchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch = Branch.objects.create(name="Export HQ", latitude=23.81, longitude=90.41, radius_meters=200)
        OfficeSchedule.objects.update_or_create(
            branch=cls.branch,
            defaults={
                "office_start_time": datetime.strptime("09:00", "%H:%M").time(),
                "office_end_time": datetime.strptime("18:00", "%H:%M").time(),
                "late_after_minutes": 15,
                "working_days": ["saturday", "sunday", "monday", "tuesday", "wednesday", "thursday"],
            },
        )
        cls.dept = Department.objects.create(name="Ops")
        cls.desig = Designation.objects.create(name="Staff")
        cls.admin = User.objects.create_superuser(email="exp_adm@example.com", password="pass", role="admin")

        u = User.objects.create_user(email="exp_emp@example.com", password="pass", role="staff")
        m = Employee.objects.create(
            user=u, employee_number="EXP-01", first_name="Exp", last_name="One",
            department=cls.dept, designation=cls.desig, branch=cls.branch, joined_date=date(2026, 1, 1),
        )
        cls.emp = EmployeeProfile.objects.create(
            user=u, master_employee=m, employee_id="EXP-01", full_name="Exp One",
            department="Ops", phone="01766666666", branch=cls.branch, is_active=True, joined_date=date(2026, 1, 1),
        )
        Attendance.objects.create(
            employee=cls.emp, date=date(2026, 11, 24), attendance_type="check_in", type="office", status="late",
            check_in_time=datetime(2026, 11, 24, 9, 30, tzinfo=BD_TZ),
            check_out_time=datetime(2026, 11, 24, 18, 0, tzinfo=BD_TZ),
            total_hours=Decimal("8.50"),
        )

    def test_1_empty_employee_and_branch_pdf_dispatch_succeeds(self):
        """Real URL dispatch: format=pdf&year=2026&month=11&employee=&branch= returns private PDF."""
        self.client.force_login(self.admin)
        with patch("django.utils.timezone.now", return_value=FROZEN_NOW):
            resp = self.client.get("/admin-panel/reports/export/?format=pdf&year=2026&month=11&employee=&branch=")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")
        self.assertIn("no-store", resp.get("Cache-Control", ""))
        self.assertTrue(resp.content.startswith(b"%PDF-"))

    def test_2_invalid_month_format_reversed_dates_and_bad_employee_return_400_without_render(self):
        """month=13, bad format, reversed dates, and non-int employee return 400 before PDF rendering."""
        self.client.force_login(self.admin)
        bad_urls = [
            "/admin-panel/reports/export/?format=pdf&year=2026&month=13&employee=&branch=",
            "/admin-panel/reports/export/?format=badfmt&year=2026&month=11",
            "/admin-panel/reports/export/?format=csv&date_from=2026-11-25&date_to=2026-11-20",
            "/admin-panel/reports/export/?format=pdf&year=2026&month=11&employee=abc",
        ]
        with patch("apps.attendance.pdf_export.render_monthly_attendance_pdf") as mock_pdf:
            for url in bad_urls:
                resp = self.client.get(url)
                self.assertEqual(resp.status_code, 400, f"Expected 400 for {url}")
            mock_pdf.assert_not_called()

    def test_3_filtered_monthly_ui_preserves_status_and_type_in_export_links_and_dispatch(self):
        """Monthly UI export links carry status & type, and export_attendance dispatches CSV with them."""
        self.client.force_login(self.admin)
        with patch("django.utils.timezone.now", return_value=FROZEN_NOW):
            ui_resp = self.client.get("/admin-panel/reports/monthly/?year=2026&month=11&status=late&type=office")
            self.assertEqual(ui_resp.status_code, 200)
            self.assertContains(ui_resp, "status=late&type=office")

            csv_resp = self.client.get(
                "/admin-panel/reports/export/?format=csv&year=2026&month=11&employee=&branch=&status=late&type=office"
            )
        self.assertEqual(csv_resp.status_code, 200)
        self.assertIn("text/csv", csv_resp["Content-Type"])
        self.assertIn("Exp One", csv_resp.content.decode("utf-8-sig"))

    def test_4_renderer_exception_returns_controlled_500_without_leaking_secrets(self):
        """Renderer failure after valid input returns controlled 500 without leaking exception internals."""
        self.client.force_login(self.admin)
        with patch("apps.attendance.pdf_export.render_monthly_attendance_pdf", side_effect=RuntimeError("SECRET_INTERNAL_TRACE")):
            resp = self.client.get("/admin-panel/reports/export/?format=pdf&year=2026&month=11&employee=&branch=")
        self.assertEqual(resp.status_code, 500)
        self.assertNotIn(b"SECRET_INTERNAL_TRACE", resp.content)

    def test_5_bounded_query_count_small_vs_larger_monthly_pdf_export(self):
        """Valid monthly PDF export stays bounded in query count as employee count grows."""
        for i in range(25):
            u = User.objects.create_user(email=f"expm_{i}@example.com", password="pass", role="staff")
            m = Employee.objects.create(
                user=u, employee_number=f"EXPM-{i}", first_name="E", last_name=str(i),
                department=self.dept, designation=self.desig, branch=self.branch, joined_date=date(2026, 1, 1),
            )
            EmployeeProfile.objects.create(
                user=u, master_employee=m, employee_id=f"EXPM-{i}", full_name=f"E {i}",
                department="Ops", phone=f"012{i:08d}", branch=self.branch, is_active=True, joined_date=date(2026, 1, 1),
            )

        self.client.force_login(self.admin)
        with patch("django.utils.timezone.now", return_value=FROZEN_NOW), CaptureQueriesContext(connection) as ctx:
            resp = self.client.get("/admin-panel/reports/export/?format=pdf&year=2026&month=11&employee=&branch=")
        self.assertEqual(resp.status_code, 200)
        self.assertLessEqual(len(ctx), 25)
