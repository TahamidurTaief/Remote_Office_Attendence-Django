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
            weekly_holiday_policy="",
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

    def test_2_cross_month_leave_boundary(self):
        # Fixed cross-month period: 2026-10-29 (Thu) to 2026-11-02 (Mon)
        p_start = date(2026, 10, 29)
        p_end = date(2026, 11, 2)

        master, prof = self._make_emp("PAY-2", joined_date=date(2026, 10, 1))
        lt = LeaveType.objects.create(name="Unpaid", deduction_percent=Decimal("100.00"))
        # 3-day leave (Oct 27..Oct 29): only Oct 29 (Thu) overlaps [p_start, p_end]
        LeaveRequest.objects.create(employee=prof, leave_type=lt, start_date=date(2026, 10, 27), end_date=date(2026, 10, 29), number_of_days=Decimal("3.0"), reason="Trip", status="approved")

        run = PayrollRun.objects.create(name="CrossMonth", period_start=p_start, period_end=p_end, status=PayrollRunStatus.DRAFT)
        calcs = PayrollService.sync_payroll_inputs(run)
        c = next(x for x in calcs if x.employee_id == master.id)
        self.assertEqual(c.source_total_approved_leave_days, Decimal("1.00"))

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

        before_count = EmployeePayrollCalculation.objects.filter(payroll_run=run).count()
        run.status = PayrollRunStatus.APPROVED_LOCKED
        run.save()
        with self.assertRaises(ValidationError):
            PayrollService.sync_payroll_inputs(run)
        self.assertEqual(EmployeePayrollCalculation.objects.filter(payroll_run=run).count(), before_count)


class PayrollLeaveWeightingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch = Branch.objects.create(name="LeaveWt Branch", latitude=23.81, longitude=90.41, radius_meters=200)
        OfficeSchedule.objects.update_or_create(
            branch=cls.branch,
            defaults={
                "office_start_time": datetime.strptime("09:00", "%H:%M").time(),
                "office_end_time": datetime.strptime("18:00", "%H:%M").time(),
                "late_after_minutes": 15,
                "working_days": ["saturday", "sunday", "monday", "tuesday", "wednesday", "thursday"],
            },
        )
        cls.dept = Department.objects.create(name="LeaveWtDept")
        cls.desig = Designation.objects.create(name="LeaveWtDesig")
        cls.comp = SalaryComponent.objects.create(
            name="Basic", code="LW_BASIC", type=SalaryComponentType.EARNING, value_type=SalaryComponentValueType.FIXED, value=Decimal("30000.00")
        )
        cls.struct = SalaryStructure.objects.create(name="LeaveWtStd")
        SalaryStructureComponent.objects.create(
            salary_structure=cls.struct, salary_component=cls.comp, value_type=SalaryComponentValueType.FIXED, value=Decimal("30000.00")
        )

    def _make_emp(self, code, joined_date):
        u = User.objects.create_user(email=f"{code.lower()}@example.com", password="pass", role="staff")
        m = Employee.objects.create(
            user=u,
            employee_number=code,
            first_name="Leave",
            last_name=code,
            department=self.dept,
            designation=self.desig,
            branch=self.branch,
            status=EmployeeStatus.ACTIVE,
            joined_date=joined_date,
            weekly_holiday_policy="",
        )
        p = EmployeeProfile.objects.create(
            user=u,
            master_employee=m,
            employee_id=code,
            full_name=f"Leave {code}",
            department="LeaveWtDept",
            phone=f"014{abs(hash(code)) % 100000000:08d}",
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

    def _two_past_workdays(self):
        # Fixed past working days: Mon 2026-11-16 and Tue 2026-11-17
        return date(2026, 11, 16), date(2026, 11, 17)

    def test_1_half_day_and_full_plus_half_day_leave_weights_and_deduction(self):
        d1, d2 = self._two_past_workdays()
        m1, p1 = self._make_emp("LW-1", joined_date=d1 - timedelta(days=5))
        m2, p2 = self._make_emp("LW-2", joined_date=d1 - timedelta(days=5))
        lt_unpaid = LeaveType.objects.create(name="UnpaidHalf", deduction_percent=Decimal("100.00"))

        lr1 = LeaveRequest.objects.create(employee=p1, leave_type=lt_unpaid, start_date=d1, end_date=d1, number_of_days=Decimal("0.5"), reason="Half", status="approved")
        LeaveRequest.objects.filter(pk=lr1.pk).update(number_of_days=Decimal("0.5"))
        ci2 = timezone.make_aware(datetime.combine(d2, datetime.strptime("09:00", "%H:%M").time()))
        Attendance.objects.create(employee=p1, date=d2, attendance_type="check_in", type="office", status="on_time", check_in_time=ci2, check_out_time=ci2 + timedelta(hours=8), total_hours=8.0)

        LeaveRequest.objects.create(employee=p2, leave_type=lt_unpaid, start_date=d1, end_date=d1, number_of_days=Decimal("1.0"), reason="Full", status="approved")
        lr2_half = LeaveRequest.objects.create(employee=p2, leave_type=lt_unpaid, start_date=d2, end_date=d2, number_of_days=Decimal("0.5"), reason="Half", status="approved")
        LeaveRequest.objects.filter(pk=lr2_half.pk).update(number_of_days=Decimal("0.5"))

        run = PayrollRun.objects.create(name="HalfWtRun", period_start=d1, period_end=d2, status=PayrollRunStatus.DRAFT)
        calcs = PayrollService.sync_payroll_inputs(run)
        c1 = next(c for c in calcs if c.employee_id == m1.id)
        c2 = next(c for c in calcs if c.employee_id == m2.id)

        self.assertEqual(c1.source_total_approved_leave_days, Decimal("0.50"))
        self.assertEqual(c1.unpaid_absent_days, Decimal("0.50"))
        self.assertEqual(c1.absence_deduction, Decimal("500.00"))

        self.assertEqual(c2.source_total_approved_leave_days, Decimal("1.50"))
        self.assertEqual(c2.unpaid_absent_days, Decimal("1.50"))
        self.assertEqual(c2.absence_deduction, Decimal("1500.00"))

    def test_2_half_day_outside_period_excluded(self):
        d1, d2 = self._two_past_workdays()
        outside = d1 - timedelta(days=2)
        if outside.weekday() == 4:
            outside -= timedelta(days=1)
        master, prof = self._make_emp("LW-3", joined_date=outside - timedelta(days=5))
        lt = LeaveType.objects.create(name="PaidHalf", deduction_percent=Decimal("100.00"))
        lr_out = LeaveRequest.objects.create(employee=prof, leave_type=lt, start_date=outside, end_date=outside, number_of_days=Decimal("0.5"), reason="Out", status="approved")
        LeaveRequest.objects.filter(pk=lr_out.pk).update(number_of_days=Decimal("0.5"))

        run = PayrollRun.objects.create(name="OutHalfRun", period_start=d2, period_end=d2, status=PayrollRunStatus.DRAFT)
        calcs = PayrollService.sync_payroll_inputs(run)
        c = next(x for x in calcs if x.employee_id == master.id)
        self.assertEqual(c.source_total_approved_leave_days, Decimal("0.00"))
        self.assertEqual(c.absence_deduction, Decimal("1000.00"))  # Only d2 absent (30000/30), 0 from outside half-day

    def test_3_attendance_conflict_excludes_half_day_leave_weight(self):
        _, d2 = self._two_past_workdays()
        master, prof = self._make_emp("LW-4", joined_date=d2 - timedelta(days=5))
        lt_unpaid = LeaveType.objects.create(name="ConflictHalf", deduction_percent=Decimal("100.00"))
        LeaveRequest.objects.create(employee=prof, leave_type=lt_unpaid, start_date=d2, end_date=d2, number_of_days=Decimal("0.5"), reason="Half", status="approved")
        ci = timezone.make_aware(datetime.combine(d2, datetime.strptime("09:00", "%H:%M").time()))
        Attendance.objects.create(employee=prof, date=d2, attendance_type="check_in", type="office", status="on_time", check_in_time=ci, check_out_time=ci + timedelta(hours=8), total_hours=8.0)

        run = PayrollRun.objects.create(name="ConflictHalfRun", period_start=d2, period_end=d2, status=PayrollRunStatus.DRAFT)
        calcs = PayrollService.sync_payroll_inputs(run)
        c = next(x for x in calcs if x.employee_id == master.id)
        self.assertEqual(c.source_total_present_days, Decimal("1.00"))
        self.assertEqual(c.source_total_approved_leave_days, Decimal("0.00"))
        self.assertEqual(c.unpaid_absent_days, Decimal("0.00"))

    def test_4_overlapping_approved_leaves_rejected_atomically(self):
        _, d2 = self._two_past_workdays()
        master, prof = self._make_emp("LW-5", joined_date=d2 - timedelta(days=5))
        lt = LeaveType.objects.create(name="OverlapLt", deduction_percent=Decimal("0.00"))
        LeaveRequest.objects.create(employee=prof, leave_type=lt, start_date=d2, end_date=d2, number_of_days=Decimal("0.5"), reason="First", status="approved")

        run = PayrollRun.objects.create(name="OverlapRun", period_start=d2, period_end=d2, status=PayrollRunStatus.DRAFT)
        PayrollService.sync_payroll_inputs(run)
        existing_count = EmployeePayrollCalculation.objects.filter(payroll_run=run).count()

        LeaveRequest.objects.create(employee=prof, leave_type=lt, start_date=d2, end_date=d2, number_of_days=Decimal("0.5"), reason="Second", status="approved")
        with self.assertRaises(ValidationError) as cm:
            PayrollService.sync_payroll_inputs(run)
        self.assertIn("LW-5", str(cm.exception))
        self.assertEqual(EmployeePayrollCalculation.objects.filter(payroll_run=run).count(), existing_count)
