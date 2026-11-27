from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext

from apps.admin_panel.analytics_service import get_attendance_analytics
from apps.admin_panel.views import ReportsMainView
from apps.attendance.models import Attendance
from apps.attendance.reporting_service import get_daily_report_data
from apps.branches.models import Branch, OfficeSchedule
from apps.employees.models import Department, Designation, Employee, EmployeeProfile

User = get_user_model()
BD_TZ = ZoneInfo("Asia/Dhaka")
FROZEN_NOW = datetime(2026, 11, 25, 14, 0, 0, tzinfo=BD_TZ)


class DashboardReportParityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch = Branch.objects.create(name="Parity HQ", latitude=23.81, longitude=90.41, radius_meters=200)
        OfficeSchedule.objects.update_or_create(
            branch=cls.branch,
            defaults={
                "office_start_time": datetime.strptime("09:00", "%H:%M").time(),
                "office_end_time": datetime.strptime("18:00", "%H:%M").time(),
                "late_after_minutes": 15,
                "working_days": ["saturday", "sunday", "monday", "tuesday", "wednesday", "thursday"],
            },
        )
        cls.dept = Department.objects.create(name="Engineering")
        cls.desig = Designation.objects.create(name="Engineer")
        cls.admin = User.objects.create_superuser(email="parity_adm@example.com", password="pass", role="admin")
        cls.factory = RequestFactory()

    def _make_emp(self, code, joined=date(2026, 1, 1)):
        u = User.objects.create_user(email=f"{code.lower()}@example.com", password="pass", role="staff")
        m = Employee.objects.create(
            user=u,
            employee_number=code,
            first_name="Emp",
            last_name=code,
            department=self.dept,
            designation=self.desig,
            branch=self.branch,
            joined_date=joined,
        )
        p = EmployeeProfile.objects.create(
            user=u,
            master_employee=m,
            employee_id=code,
            full_name=f"Emp {code}",
            department="Engineering",
            phone=f"015{abs(hash(code)) % 100000000:08d}",
            branch=self.branch,
            is_active=True,
            joined_date=joined,
        )
        return m, p

    def test_1_two_sessions_and_field_only_count_as_one_present_day(self):
        """Two office sessions for one employee/day -> 1 present day; field-only -> 1 present day."""
        _, p1 = self._make_emp("PAR-1")
        _, p2 = self._make_emp("PAR-2")
        d = date(2026, 11, 24)
        Attendance.objects.create(
            employee=p1, date=d, attendance_type="check_in", type="office", status="on_time",
            check_in_time=datetime(2026, 11, 24, 9, 0, tzinfo=BD_TZ),
            check_out_time=datetime(2026, 11, 24, 12, 0, tzinfo=BD_TZ),
            total_hours=Decimal("3.00"),
        )
        Attendance.objects.create(
            employee=p1, date=d, attendance_type="check_in", type="office", status="on_time",
            check_in_time=datetime(2026, 11, 24, 13, 0, tzinfo=BD_TZ),
            check_out_time=datetime(2026, 11, 24, 18, 0, tzinfo=BD_TZ),
            total_hours=Decimal("5.00"),
        )
        Attendance.objects.create(
            employee=p2, date=d, attendance_type="field_visit", type="field", status="on_time",
            check_in_time=datetime(2026, 11, 24, 10, 0, tzinfo=BD_TZ),
        )

        with patch("django.utils.timezone.now", return_value=FROZEN_NOW):
            analytics = get_attendance_analytics(
                self.admin,
                {"range": "custom", "start_date": d.isoformat(), "end_date": d.isoformat()},
            )
            daily = get_daily_report_data(report_date=d)

        self.assertEqual(analytics["analytics_summary"]["total_present"], 2)
        self.assertEqual(daily["present"], 2)
        self.assertEqual(analytics["analytics_summary"]["field_only_days"], 1)
        self.assertEqual(analytics["analytics_summary"]["office_only_days"], 1)
        self.assertEqual(analytics["analytics_summary"]["avg_work_hours"], 8.0)

    def test_2_auto_closed_session_shows_missing_checkout_no_fabricated_hours(self):
        """Auto-closed session without provenance -> dashboard & report show missing checkout and None avg hours."""
        _, p1 = self._make_emp("PAR-AUTO")
        d = date(2026, 11, 24)
        Attendance.objects.create(
            employee=p1, date=d, attendance_type="check_in", type="office", status="on_time",
            check_in_time=datetime(2026, 11, 24, 9, 0, tzinfo=BD_TZ),
            check_out_time=datetime(2026, 11, 24, 18, 0, tzinfo=BD_TZ),
            total_hours=Decimal("9.00"),
            note="[AUTO-CLOSED: midnight]",
        )

        with patch("django.utils.timezone.now", return_value=FROZEN_NOW):
            analytics = get_attendance_analytics(
                self.admin,
                {"range": "custom", "start_date": d.isoformat(), "end_date": d.isoformat()},
            )
            daily = get_daily_report_data(report_date=d, employee_id=p1.id)
            req = self.factory.get("/admin-panel/reports/")
            req.user = self.admin
            view = ReportsMainView()
            view.setup(req)
            ctx = view.get_context_data()

        self.assertEqual(analytics["analytics_summary"]["total_missing_checkout"], 1)
        self.assertIsNone(analytics["analytics_summary"]["avg_work_hours"])
        self.assertTrue(daily["rows"][0]["is_missing_checkout"])
        self.assertEqual(daily["rows"][0]["total_hours"], 0.0)
        self.assertIsNone(ctx["avg_work_hours"])

    def test_3_expired_only_evidence_propagated_not_dropped(self):
        """Expired-only attendance evidence is flagged as review/history_unavailable, never silently dropped."""
        _, p1 = self._make_emp("PAR-EXP")
        d = date(2026, 11, 24)
        Attendance.objects.create(
            employee=p1, date=d, attendance_type="check_in", type="office", status="on_time",
            check_in_time=datetime(2026, 11, 24, 9, 0, tzinfo=BD_TZ),
            is_expired=True,
        )

        with patch("django.utils.timezone.now", return_value=FROZEN_NOW):
            analytics = get_attendance_analytics(
                self.admin,
                {"range": "custom", "start_date": d.isoformat(), "end_date": d.isoformat()},
            )

        self.assertEqual(analytics["analytics_summary"]["total_present"], 0)
        self.assertEqual(analytics["analytics_summary"]["total_review_needed"], 1)
        self.assertEqual(analytics["analytics_summary"]["total_history_unavailable"], 1)

    def test_4_no_complete_eligible_days_returns_none_not_zero(self):
        """When no complete eligible days exist (e.g. holiday-only period), rates stay None."""
        self._make_emp("PAR-HOL")
        friday = date(2026, 11, 20)

        with patch("django.utils.timezone.now", return_value=FROZEN_NOW):
            analytics = get_attendance_analytics(
                self.admin,
                {"range": "custom", "start_date": friday.isoformat(), "end_date": friday.isoformat()},
            )

        summary = analytics["analytics_summary"]
        self.assertEqual(summary["eligible_working_days"], 0)
        self.assertIsNone(summary["attendance_rate"])
        self.assertIsNone(summary["avg_work_hours"])
        self.assertIsNone(summary["on_time_rate"])

    def test_5_bounded_query_count_50_vs_500_employees(self):
        """Query count remains bounded and constant between 50 and 500 employees."""
        d = date(2026, 11, 24)
        users = []
        for i in range(50):
            users.append(User(email=f"q50_{i}@example.com", password="x", role="staff"))
        User.objects.bulk_create(users)
        masters = [
            Employee(
                user=u, employee_number=f"Q50-{i}", first_name="E", last_name=str(i),
                department=self.dept, designation=self.desig, branch=self.branch, joined_date=date(2026, 1, 1),
            )
            for i, u in enumerate(users)
        ]
        Employee.objects.bulk_create(masters)
        EmployeeProfile.objects.bulk_create([
            EmployeeProfile(
                user=m.user, master_employee=m, employee_id=m.employee_number,
                full_name=f"Emp {m.employee_number}", department="Engineering",
                phone=f"019{i:08d}", branch=self.branch, is_active=True, joined_date=date(2026, 1, 1),
            )
            for i, m in enumerate(masters)
        ])

        with patch("django.utils.timezone.now", return_value=FROZEN_NOW), CaptureQueriesContext(connection) as ctx50:
            get_attendance_analytics(self.admin, {"range": "custom", "start_date": d.isoformat(), "end_date": d.isoformat()})

        users500 = [User(email=f"q500_{i}@example.com", password="x", role="staff") for i in range(450)]
        User.objects.bulk_create(users500)
        masters500 = [
            Employee(
                user=u, employee_number=f"Q500-{i}", first_name="E", last_name=str(i),
                department=self.dept, designation=self.desig, branch=self.branch, joined_date=date(2026, 1, 1),
            )
            for i, u in enumerate(users500)
        ]
        Employee.objects.bulk_create(masters500)
        EmployeeProfile.objects.bulk_create([
            EmployeeProfile(
                user=m.user, master_employee=m, employee_id=m.employee_number,
                full_name=f"Emp {m.employee_number}", department="Engineering",
                phone=f"018{i:08d}", branch=self.branch, is_active=True, joined_date=date(2026, 1, 1),
            )
            for i, m in enumerate(masters500)
        ])

        with patch("django.utils.timezone.now", return_value=FROZEN_NOW), CaptureQueriesContext(connection) as ctx500:
            get_attendance_analytics(self.admin, {"range": "custom", "start_date": d.isoformat(), "end_date": d.isoformat()})

        self.assertLessEqual(len(ctx50), 20)
        self.assertEqual(len(ctx500), len(ctx50))
