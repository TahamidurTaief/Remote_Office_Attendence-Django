from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from apps.admin_panel.analytics_service import get_attendance_analytics
from apps.admin_panel.views import MonthlyReportView
from apps.attendance.models import Attendance
from apps.attendance.reporting_service import get_monthly_report_data
from apps.branches.models import Branch, OfficeSchedule
from apps.employees.models import Department, Designation, Employee, EmployeeProfile
from apps.leave.models import LeaveRequest, LeaveType

User = get_user_model()
BD_TZ = ZoneInfo("Asia/Dhaka")
FROZEN_NOW = datetime(2026, 11, 30, 14, 0, 0, tzinfo=BD_TZ)


class RateAndLeaveMetricsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch = Branch.objects.create(name="Rate HQ", latitude=23.81, longitude=90.41, radius_meters=200)
        OfficeSchedule.objects.update_or_create(
            branch=cls.branch,
            defaults={
                "office_start_time": datetime.strptime("09:00", "%H:%M").time(),
                "office_end_time": datetime.strptime("18:00", "%H:%M").time(),
                "late_after_minutes": 15,
                "working_days": ["sunday", "monday", "tuesday", "wednesday", "thursday"],
            },
        )
        cls.dept = Department.objects.create(name="Ops")
        cls.desig = Designation.objects.create(name="Staff")
        cls.admin = User.objects.create_superuser(email="rate_adm@example.com", password="pass", role="admin")
        cls.factory = RequestFactory()

        cls.user = User.objects.create_user(email="rate_emp@example.com", password="pass", role="staff")
        cls.master = Employee.objects.create(
            user=cls.user, employee_number="RATE-01", first_name="Rate", last_name="One",
            department=cls.dept, designation=cls.desig, branch=cls.branch, joined_date=date(2026, 1, 1),
        )
        cls.emp = EmployeeProfile.objects.create(
            user=cls.user, master_employee=cls.master, employee_id="RATE-01", full_name="Rate One",
            department="Ops", phone="01755555555", branch=cls.branch, is_active=True, joined_date=date(2026, 1, 1),
        )
        cls.lt = LeaveType.objects.create(name="CasualRate", deduction_percent=Decimal("0.00"))

    def test_1_18_working_days_plus_2_holiday_work_gives_90pct_and_2_holiday_days(self):
        """18 attended working days out of 20 + 2 holiday-work days -> 90.0% scheduled attendance & 2 holiday-work days."""
        # In Nov 2026, Sun-Thu has 22 working days; use 2026-11-01..2026-11-28 (20 Sun-Thu working days)
        work_days = [
            date(2026, 11, d) for d in range(1, 29)
            if date(2026, 11, d).weekday() in (6, 0, 1, 2, 3)
        ]
        self.assertEqual(len(work_days), 20)
        for wd in work_days[:18]:
            Attendance.objects.create(
                employee=self.emp, date=wd, attendance_type="check_in", type="office", status="on_time",
                check_in_time=datetime(wd.year, wd.month, wd.day, 9, 0, tzinfo=BD_TZ),
                check_out_time=datetime(wd.year, wd.month, wd.day, 18, 0, tzinfo=BD_TZ),
                total_hours=Decimal("9.00"),
            )
        # Remaining 2 scheduled working days are approved leave (complete eligible days)
        for wd in work_days[18:]:
            LeaveRequest.objects.create(
                employee=self.emp, leave_type=self.lt,
                start_date=wd, end_date=wd, number_of_days=Decimal("1.0"),
                reason="Planned", status="approved",
            )
        # 2 holiday work days (Fridays)
        for hd in (date(2026, 11, 6), date(2026, 11, 13)):
            Attendance.objects.create(
                employee=self.emp, date=hd, attendance_type="check_in", type="office", status="holiday_attendance",
                check_in_time=datetime(hd.year, hd.month, hd.day, 10, 0, tzinfo=BD_TZ),
                check_out_time=datetime(hd.year, hd.month, hd.day, 15, 0, tzinfo=BD_TZ),
                total_hours=Decimal("5.00"),
            )

        with patch("django.utils.timezone.now", return_value=datetime(2026, 11, 28, 20, 0, tzinfo=BD_TZ)):
            analytics = get_attendance_analytics(
                self.admin,
                {"range": "custom", "start_date": "2026-11-01", "end_date": "2026-11-28"},
            )
        self.assertEqual(analytics["analytics_summary"]["total_present"], 18)
        self.assertEqual(analytics["analytics_summary"]["total_holiday_work"], 2)
        self.assertEqual(analytics["analytics_summary"]["eligible_working_days"], 20)
        self.assertEqual(analytics["analytics_summary"]["attendance_rate"], 90.0)

    def test_2_half_day_leave_preserves_050_weight_not_100(self):
        """A valid 0.50-day leave preserves 0.50 weight; fractional approval stays blocked on integer LeaveBalance."""
        from django.core.exceptions import ValidationError

        lr = LeaveRequest.objects.create(
            employee=self.emp, leave_type=self.lt,
            start_date=date(2026, 11, 10), end_date=date(2026, 11, 10),
            number_of_days=Decimal("0.5"), reason="Half day", status="pending",
        )
        weight = lr.overlapping_days_in(date(2026, 11, 1), date(2026, 11, 30))
        self.assertEqual(weight, Decimal("0.50"))

        lr.status = "approved"
        with self.assertRaises(ValidationError):
            lr.save()

    def test_3_leave_spanning_holiday_and_month_boundary_clipped_properly(self):
        """Leave spanning Friday/Saturday holiday and Oct->Nov boundary clips to Nov working days only."""
        # 2026-10-29 (Thu), 2026-10-30 (Fri holiday), 2026-10-31 (Sat holiday), 2026-11-01 (Sun), 2026-11-02 (Mon)
        lr = LeaveRequest.objects.create(
            employee=self.emp, leave_type=self.lt,
            start_date=date(2026, 10, 29), end_date=date(2026, 11, 2),
            number_of_days=Decimal("3.0"), reason="Long weekend", status="approved",
        )
        nov_weight = lr.overlapping_days_in(date(2026, 11, 1), date(2026, 11, 30))
        oct_weight = lr.overlapping_days_in(date(2026, 10, 1), date(2026, 10, 31))
        self.assertEqual(nov_weight, Decimal("2.00"))
        self.assertEqual(oct_weight, Decimal("1.00"))

    def test_4_incomplete_coverage_and_no_eligible_days_never_hidden_with_min_100(self):
        """Unresolved coverage marks att_pct None ('Unavailable (Incomplete)') in monthly report & HTML."""
        with patch("django.utils.timezone.now", return_value=FROZEN_NOW):
            monthly = get_monthly_report_data(2026, 11, employee_id=self.emp.id)
            req = self.factory.get("/admin-panel/reports/monthly/?year=2026&month=11")
            req.user = self.admin
            resp = MonthlyReportView.as_view()(req)

        row = monthly["rows"][0]
        self.assertIsNone(row["att_pct"])
        self.assertEqual(row["att_pct_display"], "Unavailable (Incomplete)")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Incomplete")
