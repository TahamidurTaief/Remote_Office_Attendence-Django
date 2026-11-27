from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.attendance.models import Attendance
from apps.attendance.reporting_service import get_daily_report_data, get_monthly_report_data
from apps.branches.models import Branch, OfficeSchedule
from apps.employees.models import Department, Designation, Employee, EmployeeProfile

User = get_user_model()
BD_TZ = ZoneInfo("Asia/Dhaka")
FROZEN_NOW = datetime(2026, 11, 25, 14, 0, 0, tzinfo=BD_TZ)


class ReportOvertimeCalculationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch = Branch.objects.create(name="OT HQ", latitude=23.81, longitude=90.41, radius_meters=200)
        OfficeSchedule.objects.update_or_create(
            branch=cls.branch,
            defaults={
                "office_start_time": datetime.strptime("09:00", "%H:%M").time(),
                "office_end_time": datetime.strptime("18:00", "%H:%M").time(),
                "late_after_minutes": 15,
                "overtime_after_minutes": 0,
                "working_days": ["saturday", "sunday", "monday", "tuesday", "wednesday", "thursday"],
            },
        )
        cls.dept = Department.objects.create(name="Ops")
        cls.desig = Designation.objects.create(name="Staff")

    def _make_emp(self, code, shift="Day Shift", overtime_enabled=True):
        u = User.objects.create_user(email=f"{code.lower()}@example.com", password="pass", role="staff")
        m = Employee.objects.create(
            user=u, employee_number=code, first_name="Emp", last_name=code,
            department=self.dept, designation=self.desig, branch=self.branch,
            shift=shift, joined_date=date(2026, 1, 1),
        )
        p = EmployeeProfile.objects.create(
            user=u, master_employee=m, employee_id=code, full_name=f"Emp {code}",
            department="Ops", phone=f"013{abs(hash(code)) % 100000000:08d}",
            branch=self.branch, is_active=True, overtime_enabled=overtime_enabled, joined_date=date(2026, 1, 1),
        )
        return m, p

    def test_1_adjacent_sessions_17_19_and_19_20_give_120_ot_minutes(self):
        """Shift ends 18:00; 17:00-19:00 + 19:00-20:00 -> 120 observed OT minutes (no double count)."""
        _, p = self._make_emp("OT-ADJ")
        d = date(2026, 11, 24)
        Attendance.objects.create(
            employee=p, date=d, attendance_type="check_in", type="office", status="on_time",
            check_in_time=datetime(2026, 11, 24, 17, 0, tzinfo=BD_TZ),
            check_out_time=datetime(2026, 11, 24, 19, 0, tzinfo=BD_TZ),
            total_hours=Decimal("2.00"), overtime_minutes=60,
        )
        Attendance.objects.create(
            employee=p, date=d, attendance_type="check_in", type="office", status="on_time",
            check_in_time=datetime(2026, 11, 24, 19, 0, tzinfo=BD_TZ),
            check_out_time=datetime(2026, 11, 24, 20, 0, tzinfo=BD_TZ),
            total_hours=Decimal("1.00"), overtime_minutes=120,
        )

        with patch("django.utils.timezone.now", return_value=FROZEN_NOW):
            with CaptureQueriesContext(connection) as ctx_d:
                daily = get_daily_report_data(report_date=d, employee_id=p.id)
            with CaptureQueriesContext(connection) as ctx_m:
                monthly = get_monthly_report_data(2026, 11, employee_id=p.id)

        self.assertEqual(daily["rows"][0]["day_calc"]["overtime_minutes"], 120)
        self.assertEqual(monthly["total_overtime_minutes"], 120)
        self.assertLessEqual(len(ctx_d), 15)
        self.assertLessEqual(len(ctx_m), 15)

    def test_2_overlapping_sessions_18_20_and_19_21_give_180_not_240(self):
        """Overlapping 18:00-20:00 and 19:00-21:00 -> 180 observed OT minutes, not 240."""
        _, p = self._make_emp("OT-OVL")
        d = date(2026, 11, 24)
        Attendance.objects.create(
            employee=p, date=d, attendance_type="check_in", type="office", status="on_time",
            check_in_time=datetime(2026, 11, 24, 18, 0, tzinfo=BD_TZ),
            check_out_time=datetime(2026, 11, 24, 20, 0, tzinfo=BD_TZ),
            total_hours=Decimal("2.00"), overtime_minutes=120,
        )
        Attendance.objects.create(
            employee=p, date=d, attendance_type="check_in", type="office", status="on_time",
            check_in_time=datetime(2026, 11, 24, 19, 0, tzinfo=BD_TZ),
            check_out_time=datetime(2026, 11, 24, 21, 0, tzinfo=BD_TZ),
            total_hours=Decimal("2.00"), overtime_minutes=180,
        )

        with patch("django.utils.timezone.now", return_value=FROZEN_NOW):
            daily = get_daily_report_data(report_date=d, employee_id=p.id)
        self.assertEqual(daily["rows"][0]["day_calc"]["overtime_minutes"], 180)
        self.assertEqual(daily["rows"][0]["total_hours"], 3.0)

    def test_3_verified_session_plus_missing_checkout_and_field_visit_excluded(self):
        """Missing/auto-closed checkout and field visits do not create extra office overtime."""
        _, p = self._make_emp("OT-MISS")
        d = date(2026, 11, 24)
        Attendance.objects.create(
            employee=p, date=d, attendance_type="check_in", type="office", status="on_time",
            check_in_time=datetime(2026, 11, 24, 17, 0, tzinfo=BD_TZ),
            check_out_time=datetime(2026, 11, 24, 19, 0, tzinfo=BD_TZ),
            total_hours=Decimal("2.00"),
        )
        Attendance.objects.create(
            employee=p, date=d, attendance_type="check_in", type="office", status="on_time",
            check_in_time=datetime(2026, 11, 24, 19, 30, tzinfo=BD_TZ),
            check_out_time=None,
        )
        Attendance.objects.create(
            employee=p, date=d, attendance_type="field_visit", type="field", status="on_time",
            check_in_time=datetime(2026, 11, 24, 21, 0, tzinfo=BD_TZ),
            overtime_minutes=180,
        )

        with patch("django.utils.timezone.now", return_value=FROZEN_NOW):
            daily = get_daily_report_data(report_date=d, employee_id=p.id)
        self.assertEqual(daily["rows"][0]["day_calc"]["overtime_minutes"], 60)

    def test_4_overnight_shift_overtime_spans_midnight(self):
        """Overnight shift 22:00-06:00 with checkout at 07:30 -> 90 observed OT minutes."""
        _, p = self._make_emp("OT-NIGHT", shift="Night (22:00 - 06:00)")
        d = date(2026, 11, 24)
        Attendance.objects.create(
            employee=p, date=d, attendance_type="check_in", type="office", status="on_time",
            check_in_time=datetime(2026, 11, 24, 22, 0, tzinfo=BD_TZ),
            check_out_time=datetime(2026, 11, 25, 7, 30, tzinfo=BD_TZ),
            total_hours=Decimal("9.50"),
        )

        with patch("django.utils.timezone.now", return_value=FROZEN_NOW):
            daily = get_daily_report_data(report_date=d, employee_id=p.id)
        self.assertEqual(daily["rows"][0]["day_calc"]["overtime_minutes"], 90)

    def test_5_overtime_disabled_and_monthly_formatting_parity(self):
        """overtime_enabled=False yields 0 observed OT; enabled yields consistent monthly total_overtime."""
        _, p_off = self._make_emp("OT-OFF", overtime_enabled=False)
        _, p_on = self._make_emp("OT-ON", overtime_enabled=True)
        d = date(2026, 11, 24)
        for emp in (p_off, p_on):
            Attendance.objects.create(
                employee=emp, date=d, attendance_type="check_in", type="office", status="on_time",
                check_in_time=datetime(2026, 11, 24, 9, 0, tzinfo=BD_TZ),
                check_out_time=datetime(2026, 11, 24, 19, 30, tzinfo=BD_TZ),
                total_hours=Decimal("10.50"), overtime_minutes=90,
            )

        with patch("django.utils.timezone.now", return_value=FROZEN_NOW):
            d_off = get_daily_report_data(report_date=d, employee_id=p_off.id)
            m_on = get_monthly_report_data(2026, 11, employee_id=p_on.id)

        self.assertEqual(d_off["rows"][0]["day_calc"]["overtime_minutes"], 0)
        self.assertEqual(m_on["total_overtime_minutes"], 90)
        self.assertEqual(m_on["total_overtime"], "1h 30m")
