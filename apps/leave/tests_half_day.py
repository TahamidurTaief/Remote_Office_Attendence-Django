from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.attendance.models import Attendance
from apps.branches.models import Branch, Holiday, OfficeSchedule
from apps.employees.models import Department, Designation, Employee, EmployeeProfile, EmployeeStatus
from apps.leave.forms import AdminAddLeaveForm, AdminLeaveRequestRescheduleForm, LeaveRequestForm
from apps.leave.models import LeaveBalance, LeaveRequest, LeaveType

User = get_user_model()


class HalfDayLeaveFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch = Branch.objects.create(name="HD Branch", latitude=23.81, longitude=90.41, radius_meters=200)
        OfficeSchedule.objects.update_or_create(
            branch=cls.branch,
            defaults={
                "office_start_time": datetime.strptime("09:00", "%H:%M").time(),
                "office_end_time": datetime.strptime("18:00", "%H:%M").time(),
                "late_after_minutes": 15,
                "working_days": ["saturday", "sunday", "monday", "tuesday", "wednesday", "thursday"],
            },
        )
        cls.dept = Department.objects.create(name="HDDept")
        cls.desig = Designation.objects.create(name="HDDesig")
        u = User.objects.create_user(email="hd_emp@example.com", password="pass", role="staff")
        m = Employee.objects.create(
            user=u,
            employee_number="HD-1",
            first_name="Half",
            last_name="Day",
            department=cls.dept,
            designation=cls.desig,
            branch=cls.branch,
            status=EmployeeStatus.ACTIVE,
            joined_date=date(2026, 1, 1),
        )
        cls.prof = EmployeeProfile.objects.create(
            user=u,
            master_employee=m,
            employee_id="HD-1",
            full_name="Half Day",
            department="HDDept",
            phone="01711111111",
            branch=cls.branch,
            is_active=True,
            joined_date=date(2026, 1, 1),
        )
        cls.lt_casual = LeaveType.objects.create(name="CasualHD", default_days_per_year=10, is_active=True)
        cls.lt_sick = LeaveType.objects.create(name="SickHD", default_days_per_year=10, is_active=True)

    def test_1_pending_half_day_form_save_and_edit_preserves_duration(self):
        d = date(2026, 11, 16)  # Monday
        form = LeaveRequestForm(
            data={
                "leave_type": self.lt_casual.id,
                "start_date": d.isoformat(),
                "end_date": d.isoformat(),
                "duration_type": "half",
                "number_of_days": "99.9",  # must be ignored
                "reason": "Doctor",
            },
            employee=self.prof,
        )
        self.assertTrue(form.is_valid(), form.errors)
        lr = form.save(commit=False)
        lr.employee = self.prof
        lr.status = "pending"
        lr.save()

        lr.refresh_from_db()
        self.assertEqual(lr.number_of_days, Decimal("0.5"))

        # Edit date & leave_type while keeping half-day
        d2 = date(2026, 11, 17)
        edit_form = LeaveRequestForm(
            data={
                "leave_type": self.lt_sick.id,
                "start_date": d2.isoformat(),
                "end_date": d2.isoformat(),
                "duration_type": "half",
                "reason": "Updated",
            },
            instance=lr,
            employee=self.prof,
        )
        self.assertEqual(edit_form.fields["duration_type"].initial, "half")
        self.assertTrue(edit_form.is_valid(), edit_form.errors)
        edit_form.save()
        lr.refresh_from_db()
        self.assertEqual(lr.number_of_days, Decimal("0.5"))
        self.assertEqual(lr.leave_type_id, self.lt_sick.id)

        # Multi-day half-day must be rejected
        bad_form = LeaveRequestForm(
            data={
                "leave_type": self.lt_casual.id,
                "start_date": d.isoformat(),
                "end_date": d2.isoformat(),
                "duration_type": "half",
                "reason": "Bad",
            },
            employee=self.prof,
        )
        self.assertFalse(bad_form.is_valid())

    def test_2_edit_half_day_to_full_or_holiday_or_attendance_recomputes(self):
        d = date(2026, 11, 16)
        lr = LeaveRequest.objects.create(
            employee=self.prof,
            leave_type=self.lt_casual,
            start_date=d,
            end_date=d,
            number_of_days=Decimal("0.5"),
            reason="Half",
            status="pending",
        )
        self.assertEqual(lr.number_of_days, Decimal("0.5"))

        # Edit to full day via form
        f_full = LeaveRequestForm(
            data={
                "leave_type": self.lt_casual.id,
                "start_date": d.isoformat(),
                "end_date": d.isoformat(),
                "duration_type": "full",
                "reason": "Full",
            },
            instance=lr,
            employee=self.prof,
        )
        self.assertTrue(f_full.is_valid(), f_full.errors)
        f_full.save()
        lr.refresh_from_db()
        self.assertEqual(lr.number_of_days, Decimal("1.0"))

        # Half-day on a holiday recomputes to 0.0
        hol_day = date(2026, 11, 18)
        Holiday.objects.create(branch=self.branch, date=hol_day, name="Holiday")
        lr.start_date = hol_day
        lr.end_date = hol_day
        lr.number_of_days = Decimal("0.5")
        lr.save()
        lr.refresh_from_db()
        self.assertEqual(lr.number_of_days, Decimal("0.0"))

        # Half-day on attendance day recomputes to 0.0
        att_day = date(2026, 11, 19)
        ci = timezone.make_aware(datetime.combine(att_day, datetime.strptime("09:00", "%H:%M").time()))
        Attendance.objects.create(employee=self.prof, date=att_day, attendance_type="check_in", type="office", status="on_time", check_in_time=ci)
        lr.start_date = att_day
        lr.end_date = att_day
        lr.number_of_days = Decimal("0.5")
        lr.save()
        lr.refresh_from_db()
        self.assertEqual(lr.number_of_days, Decimal("0.0"))

    def test_3_fractional_approval_rejected_atomically_without_balance_mutation(self):
        d = date(2026, 11, 16)
        lr = LeaveRequest.objects.create(
            employee=self.prof,
            leave_type=self.lt_casual,
            start_date=d,
            end_date=d,
            number_of_days=Decimal("0.5"),
            reason="Half",
            status="pending",
        )
        with self.assertRaises(ValidationError):
            lr.status = "approved"
            lr.save()

        lr.refresh_from_db()
        self.assertEqual(lr.status, "pending")
        self.assertEqual(lr.number_of_days, Decimal("0.5"))
        self.assertFalse(
            LeaveBalance.objects.filter(employee=self.prof, leave_type=self.lt_casual, year=d.year).exists()
        )

    def test_4_whole_day_approval_and_repeated_cancellation_idempotency(self):
        d = date(2026, 11, 16)
        lr = LeaveRequest.objects.create(
            employee=self.prof,
            leave_type=self.lt_casual,
            start_date=d,
            end_date=d,
            reason="Full",
            status="pending",
        )
        lr.status = "approved"
        lr.save()
        bal = LeaveBalance.objects.get(employee=self.prof, leave_type=self.lt_casual, year=d.year)
        self.assertEqual(bal.used_days, 1)

        # Cancel once -> refunded
        lr.status = "cancelled"
        lr.save()
        bal.refresh_from_db()
        self.assertEqual(bal.used_days, 0)

        # Cancel again -> idempotent, no double refund
        lr.status = "cancelled"
        lr.save()
        bal.refresh_from_db()
        self.assertEqual(bal.used_days, 0)

    def test_5_admin_forms_and_query_count_bounded(self):
        d = date(2026, 11, 16)
        add_form = AdminAddLeaveForm(
            data={
                "employee": self.prof.id,
                "leave_type": self.lt_casual.id,
                "start_date": d.isoformat(),
                "end_date": d.isoformat(),
                "duration_type": "half",
                "reason": "Admin half",
                "status": "pending",
            }
        )
        self.assertTrue(add_form.is_valid(), add_form.errors)
        with CaptureQueriesContext(connection) as ctx:
            lr = add_form.save()
        self.assertLessEqual(len(ctx), 15)
        self.assertEqual(lr.number_of_days, Decimal("0.5"))

        resched_form = AdminLeaveRequestRescheduleForm(
            data={
                "leave_type": self.lt_sick.id,
                "start_date": (d + timedelta(days=1)).isoformat(),
                "end_date": (d + timedelta(days=1)).isoformat(),
                "duration_type": "half",
                "reason": "Rescheduled",
            },
            instance=lr,
        )
        self.assertTrue(resched_form.is_valid(), resched_form.errors)
        resched_form.save()
        lr.refresh_from_db()
        self.assertEqual(lr.number_of_days, Decimal("0.5"))
