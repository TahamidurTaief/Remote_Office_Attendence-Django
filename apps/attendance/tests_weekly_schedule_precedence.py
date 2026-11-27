from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.admin_panel.analytics_service import get_attendance_analytics
from apps.admin_panel.views import get_absent_records
from apps.attendance.models import Attendance
from apps.attendance.reporting_service import (
    OptimizedSchedule,
    get_daily_report_data,
    get_monthly_report_data,
    is_employee_holiday_optimized,
)
from apps.attendance.schedule_utils import calculate_attendance_status, is_employee_holiday
from apps.branches.models import Branch, Holiday, OfficeSchedule
from apps.employees.models import Department, Designation, Employee, EmployeeProfile, EmployeeStatus
from apps.payroll.models import (
    AbsenceDivisorMode,
    EmployeeSalaryAssignment,
    PaymentMode,
    PayrollPolicy,
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


class WeeklySchedulePrecedenceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.dept = Department.objects.create(name="SchedDept")
        cls.desig = Designation.objects.create(name="SchedDesig")
        cls.admin = User.objects.create_superuser(email="sched_adm@example.com", password="pass", role="admin")

        cls.branch_fri_work = Branch.objects.create(name="FriWorkBranch", latitude=23.81, longitude=90.41, radius_meters=200)
        OfficeSchedule.objects.update_or_create(
            branch=cls.branch_fri_work,
            defaults={
                "office_start_time": datetime.strptime("09:00", "%H:%M").time(),
                "office_end_time": datetime.strptime("18:00", "%H:%M").time(),
                "working_days": ["saturday", "sunday", "monday", "tuesday", "wednesday", "thursday", "friday"],
            },
        )

        cls.branch_fri_work.refresh_from_db()

        cls.branch_no_sched = Branch.objects.create(name="NoSchedBranch", latitude=23.82, longitude=90.42, radius_meters=200)
        OfficeSchedule.objects.filter(branch=cls.branch_no_sched).delete()
        cls.branch_no_sched.refresh_from_db()

    def _make_emp(self, code, branch, policy="", joined_date=None):
        joined = joined_date or (timezone.localdate() - timedelta(days=30))
        u = User.objects.create_user(email=f"{code.lower()}@example.com", password="pass", role="staff")
        m = Employee.objects.create(
            user=u,
            employee_number=code,
            first_name="Sched",
            last_name=code,
            department=self.dept,
            designation=self.desig,
            branch=branch,
            status=EmployeeStatus.ACTIVE,
            joined_date=joined,
            weekly_holiday_policy=policy,
        )
        p = EmployeeProfile.objects.create(
            user=u,
            master_employee=m,
            employee_id=code,
            full_name=f"Sched {code}",
            department="SchedDept",
            phone=f"015{abs(hash(code)) % 100000000:08d}",
            branch=branch,
            is_active=True,
            joined_date=joined,
        )
        return m, p

    def _last_friday(self):
        d = timezone.localdate() - timedelta(days=1)
        while d.weekday() != 4:
            d -= timedelta(days=1)
        return d

    @override_settings(WORKING_DAYS=[0, 1, 2, 3, 5, 6])  # Company: Friday (4) off
    def test_1_branch_friday_working_overrides_company_friday_off(self):
        fri = self._last_friday()
        _, prof = self._make_emp("WSP-1", self.branch_fri_work, policy="")
        sched = OptimizedSchedule(prof)

        self.assertFalse(is_employee_holiday(prof, fri))
        self.assertFalse(is_employee_holiday_optimized(prof, fri, sched, branch_holidays={}, global_holidays=set()))

        daily = get_daily_report_data(report_date=fri, employee_id=prof.id)
        self.assertEqual(daily["absent"], 1)
        self.assertEqual(daily["rows"][0]["status"], "absent")

        absent_recs = get_absent_records(date_from=fri, date_to=fri, employee_id=prof.id)
        self.assertEqual(len(absent_recs), 1)

        analytics = get_attendance_analytics(
            self.admin,
            {"range": "custom", "start_date": fri.isoformat(), "end_date": fri.isoformat(), "branch": str(self.branch_fri_work.id)},
        )
        self.assertEqual(analytics["analytics_summary"]["total_absent"], 1)

    @override_settings(WORKING_DAYS=[0, 1, 2, 3, 5, 6])  # Company: Friday (4) off, Thursday (3) working
    def test_2_company_fallback_when_no_employee_or_branch_schedule(self):
        fri = self._last_friday()
        thu = fri - timedelta(days=1)
        _, prof = self._make_emp("WSP-2", self.branch_no_sched, policy="")
        sched = OptimizedSchedule(prof)

        self.assertFalse(sched.has_branch_working_days)
        self.assertTrue(is_employee_holiday(prof, fri))
        self.assertTrue(is_employee_holiday_optimized(prof, fri, sched, branch_holidays={}, global_holidays=set()))
        self.assertFalse(is_employee_holiday(prof, thu))
        self.assertFalse(is_employee_holiday_optimized(prof, thu, sched, branch_holidays={}, global_holidays=set()))

    def test_3_explicit_employee_policy_overrides_branch_conflict(self):
        fri = self._last_friday()
        sun = fri - timedelta(days=5)  # Sunday (weekday=6), which IS in branch_fri_work.working_days
        _, prof = self._make_emp("WSP-3", self.branch_fri_work, policy="Sunday")
        sched = OptimizedSchedule(prof)

        # Employee policy says only Sunday is weekly holiday -> Sunday is holiday, Friday is working
        self.assertTrue(is_employee_holiday(prof, sun))
        self.assertTrue(is_employee_holiday_optimized(prof, sun, sched, branch_holidays={}, global_holidays=set()))
        self.assertFalse(is_employee_holiday(prof, fri))
        self.assertFalse(is_employee_holiday_optimized(prof, fri, sched, branch_holidays={}, global_holidays=set()))

    @override_settings(WORKING_DAYS=[0, 1, 2, 3, 5, 6])
    def test_4_public_date_holiday_on_working_day_and_holiday_attendance_preserved(self):
        fri = self._last_friday()
        _, prof = self._make_emp("WSP-4", self.branch_fri_work, policy="")
        Holiday.objects.create(name="Public Fri Holiday", date=fri, branch=None)
        sched = OptimizedSchedule(prof)

        self.assertTrue(is_employee_holiday(prof, fri))
        self.assertTrue(is_employee_holiday_optimized(prof, fri, sched, branch_holidays={}, global_holidays={fri}))

        # Real attendance on that public holiday is preserved and classified as holiday_attendance
        ci = timezone.make_aware(datetime.combine(fri, datetime.strptime("09:05", "%H:%M").time()))
        self.assertEqual(calculate_attendance_status(ci, sched), "holiday_attendance")
        Attendance.objects.create(
            employee=prof,
            date=fri,
            attendance_type="check_in",
            type="office",
            status="on_time",
            check_in_time=ci,
            check_out_time=ci + timedelta(hours=8),
            total_hours=8.0,
        )
        daily = get_daily_report_data(report_date=fri, employee_id=prof.id)
        self.assertEqual(daily["present"], 1)
        self.assertEqual(daily["absent"], 0)

    @override_settings(WORKING_DAYS=[0, 1, 2, 3, 5, 6])
    def test_5_payroll_divisor_agreement_and_zero_per_day_queries(self):
        fri = self._last_friday()
        master, prof = self._make_emp("WSP-5", self.branch_fri_work, policy="", joined_date=fri - timedelta(days=10))
        sched = OptimizedSchedule(prof)

        # Prefetched schedule/holiday collections execute 0 DB queries
        with CaptureQueriesContext(connection) as ctx:
            for i in range(7):
                is_employee_holiday_optimized(prof, fri - timedelta(days=i), sched, branch_holidays={}, global_holidays=set())
        self.assertEqual(len(ctx), 0)

        # Payroll working_days divisor on a single-day Friday run for branch_fri_work counts Friday as 1 working day
        comp = SalaryComponent.objects.create(name="Basic", code="WSP_BASIC", type=SalaryComponentType.EARNING, value_type=SalaryComponentValueType.FIXED, value=Decimal("30000.00"))
        struct = SalaryStructure.objects.create(name="WSP Std")
        SalaryStructureComponent.objects.create(salary_structure=struct, salary_component=comp, value_type=SalaryComponentValueType.FIXED, value=Decimal("30000.00"))
        EmployeeSalaryAssignment.objects.create(employee=master, salary_structure=struct, gross_salary=Decimal("30000.00"), payment_mode=PaymentMode.BANK, effective_from=fri - timedelta(days=10))
        PayrollPolicy.objects.create(branch=self.branch_fri_work, absence_divisor_mode=AbsenceDivisorMode.WORKING_DAYS)

        run = PayrollRun.objects.create(name="FriWorkRun", period_start=fri, period_end=fri, status=PayrollRunStatus.DRAFT)
        calcs = PayrollService.sync_payroll_inputs(run)
        my_calc = next(c for c in calcs if c.employee_id == master.id)
        self.assertEqual(my_calc.unpaid_absent_days, Decimal("1.00"))
        self.assertEqual(my_calc.absence_deduction, Decimal("30000.00"))
