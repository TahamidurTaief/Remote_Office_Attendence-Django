from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.attendance.models import Attendance, OvertimeRequest
from apps.branches.models import Branch, OfficeSchedule
from apps.employees.models import Department, Designation, Employee, EmployeeProfile, EmployeeStatus, EmploymentHistory
from apps.leave.models import LeaveRequest, LeaveType
from apps.payroll.models import (
    EmployeePayrollCalculation,
    EmployeeSalaryAssignment,
    PaymentMode,
    PayrollRun,
    PayrollRunStatus,
    SalaryComponent,
    SalaryComponentType,
    SalaryComponentValueType,
    SalaryStructure,
    SalaryStructureComponent,
)
from apps.payroll.services import PayrollService

User = get_user_model()


class PayrollPeriodSyncTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch = Branch.objects.create(name="Pay Branch", latitude=23.81, longitude=90.41, radius_meters=200)
        OfficeSchedule.objects.update_or_create(
            branch=cls.branch,
            defaults={
                "office_start_time": datetime.strptime("09:00", "%H:%M").time(),
                "office_end_time": datetime.strptime("18:00", "%H:%M").time(),
                "late_after_minutes": 15,
                "working_days": ["saturday", "sunday", "monday", "tuesday", "wednesday", "thursday"],
            },
        )
        cls.dept = Department.objects.create(name="Finance")
        cls.desig = Designation.objects.create(name="Exec")
        cls.comp = SalaryComponent.objects.create(
            name="Basic", code="PAY_BASIC", type=SalaryComponentType.EARNING, value_type=SalaryComponentValueType.FIXED, value=Decimal("30000.00")
        )
        cls.struct = SalaryStructure.objects.create(name="PayStd")
        SalaryStructureComponent.objects.create(
            salary_structure=cls.struct, salary_component=cls.comp, value_type=SalaryComponentValueType.FIXED, value=Decimal("30000.00")
        )

    def _make_emp(self, code, joined_date):
        u = User.objects.create_user(email=f"{code.lower()}@example.com", password="pass", role="staff")
        m = Employee.objects.create(
            user=u,
            employee_number=code,
            first_name="Pay",
            last_name=code,
            department=self.dept,
            designation=self.desig,
            branch=self.branch,
            status=EmployeeStatus.ACTIVE,
            joined_date=joined_date,
        )
        p = EmployeeProfile.objects.create(
            user=u,
            master_employee=m,
            employee_id=code,
            full_name=f"Pay {code}",
            department="Finance",
            phone=f"015{abs(hash(code)) % 100000000:08d}",
            branch=self.branch,
            is_active=True,
            joined_date=joined_date,
        )
        EmployeeSalaryAssignment.objects.create(
            employee=m,
            salary_structure=self.struct,
            gross_salary=Decimal("30000.00"),
            payment_mode=PaymentMode.BANK,
            effective_from=joined_date,
        )
        return m, p

    def test_1_partial_month_period_excludes_out_of_period_attendance_and_ot(self):
        today = timezone.localdate()
        # Use a fixed past 5-day window inside a single month
        p_end = today - timedelta(days=2)
        p_start = p_end - timedelta(days=4)
        outside_day = p_start - timedelta(days=3)
        if outside_day.weekday() == 4:
            outside_day -= timedelta(days=1)

        master, prof = self._make_emp("PAY-1", joined_date=outside_day - timedelta(days=10))
        # Attendance & OT outside payroll period must NOT be counted
        ci_out = timezone.make_aware(datetime.combine(outside_day, datetime.strptime("09:00", "%H:%M").time()))
        Attendance.objects.create(employee=prof, date=outside_day, attendance_type="check_in", type="office", status="on_time", check_in_time=ci_out, check_out_time=ci_out + timedelta(hours=8), total_hours=8.0)
        OvertimeRequest.objects.create(employee=prof, date=outside_day, ot_minutes=120, status="approved")

        # 1 present day inside period
        in_day = p_start
        if in_day.weekday() == 4:
            in_day += timedelta(days=1)
        ci_in = timezone.make_aware(datetime.combine(in_day, datetime.strptime("09:00", "%H:%M").time()))
        Attendance.objects.create(employee=prof, date=in_day, attendance_type="check_in", type="office", status="on_time", check_in_time=ci_in, check_out_time=ci_in + timedelta(hours=8), total_hours=8.0)

        run = PayrollRun.objects.create(name="Partial", period_start=p_start, period_end=p_end, status=PayrollRunStatus.DRAFT)
        calcs = PayrollService.sync_payroll_inputs(run)
        c = next(x for x in calcs if x.employee_id == master.id)
        self.assertEqual(c.source_total_present_days, Decimal("1.00"))
        self.assertEqual(c.source_total_approved_ot_hours, Decimal("0.00"))

    def test_2_cross_month_and_half_day_leave_boundary(self):
        # Cross-month period: Oct 29 to Nov 2 of a past month
        today = timezone.localdate()
        first_of_this_month = today.replace(day=1)
        p_start = first_of_this_month - timedelta(days=3)
        p_end = first_of_this_month + timedelta(days=1)
        if p_end >= today:
            prev_first = (first_of_this_month - timedelta(days=1)).replace(day=1)
            p_start = prev_first - timedelta(days=3)
            p_end = prev_first + timedelta(days=1)

        master, prof = self._make_emp("PAY-2", joined_date=p_start - timedelta(days=10))
        lt = LeaveType.objects.create(name="Unpaid", deduction_percent=Decimal("100.00"))
        # Leave overlaps period boundary (starts before p_start, ends on p_start)
        LeaveRequest.objects.create(employee=prof, leave_type=lt, start_date=p_start - timedelta(days=2), end_date=p_start, number_of_days=Decimal("3.0"), reason="Trip", status="approved")

        run = PayrollRun.objects.create(name="CrossMonth", period_start=p_start, period_end=p_end, status=PayrollRunStatus.DRAFT)
        calcs = PayrollService.sync_payroll_inputs(run)
        c = next(x for x in calcs if x.employee_id == master.id)
        expected_leave_days = Decimal("0.00") if p_start.weekday() == 4 else Decimal("1.00")
        self.assertEqual(c.source_total_approved_leave_days, expected_leave_days)

    def test_3_eligibility_and_invalid_period_rejection(self):
        today = timezone.localdate()
        p_end = today - timedelta(days=2)
        p_start = p_end - timedelta(days=4)

        # Invalid period rejected before mutation
        bad_run = PayrollRun.objects.create(name="Bad", period_start=p_end, period_end=p_start, status=PayrollRunStatus.DRAFT)
        with self.assertRaises(ValidationError):
            PayrollService.sync_payroll_inputs(bad_run)

        # Exited before period_start -> 0 eligible days -> excluded
        master, _ = self._make_emp("PAY-3", joined_date=p_start - timedelta(days=20))
        EmploymentHistory.objects.create(
            employee=master, field_changed="status", old_value="Active", new_value="Resigned", effective_date=p_start - timedelta(days=5)
        )
        run = PayrollRun.objects.create(name="EmptyElig", period_start=p_start, period_end=p_end, status=PayrollRunStatus.DRAFT)
        calcs = PayrollService.sync_payroll_inputs(run)
        self.assertFalse(any(c.employee_id == master.id for c in calcs))

    def test_4_idempotency_and_approved_only_ot_decimal(self):
        today = timezone.localdate()
        p_end = today - timedelta(days=2)
        p_start = p_end - timedelta(days=2)
        master, prof = self._make_emp("PAY-4", joined_date=p_start - timedelta(days=10))
        d = p_start if p_start.weekday() != 4 else p_start + timedelta(days=1)
        OvertimeRequest.objects.create(employee=prof, date=d, ot_minutes=90, status="approved")
        d2 = d + timedelta(days=1) if d < p_end else d - timedelta(days=1)
        OvertimeRequest.objects.create(employee=prof, date=d2, ot_minutes=60, status="pending")  # must be ignored

        run = PayrollRun.objects.create(name="Idem", period_start=p_start, period_end=p_end, status=PayrollRunStatus.DRAFT)
        c1 = next(c for c in PayrollService.sync_payroll_inputs(run) if c.employee_id == master.id)
        c2 = next(c for c in PayrollService.sync_payroll_inputs(run) if c.employee_id == master.id)
        self.assertEqual(EmployeePayrollCalculation.objects.filter(payroll_run=run, employee=master).count(), 1)
        self.assertEqual(c1.net_payable, c2.net_payable)
        self.assertEqual(c2.source_total_approved_ot_hours, Decimal("1.50"))

    def test_5_locked_guard_and_bounded_queries(self):
        today = timezone.localdate()
        p_end = today - timedelta(days=2)
        p_start = p_end - timedelta(days=3)
        self._make_emp("PAY-5", joined_date=p_start - timedelta(days=10))

        run = PayrollRun.objects.create(name="Lock", period_start=p_start, period_end=p_end, status=PayrollRunStatus.DRAFT)
        with CaptureQueriesContext(connection) as ctx:
            PayrollService.sync_payroll_inputs(run)
        self.assertLessEqual(len(ctx), 35)

        run.status = PayrollRunStatus.APPROVED_LOCKED
        run.save()
        with self.assertRaises(ValidationError):
            PayrollService.sync_payroll_inputs(run)
