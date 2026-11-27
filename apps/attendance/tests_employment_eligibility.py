from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.admin_panel.analytics_service import get_attendance_analytics
from apps.admin_panel.views import get_absent_records
from apps.attendance.models import Attendance
from apps.attendance.reporting_service import get_daily_report_data, get_monthly_report_data
from apps.branches.models import Branch, OfficeSchedule
from apps.employees.models import Department, Designation, Employee, EmployeeProfile, EmployeeStatus, EmploymentHistory
from apps.leave.models import LeaveRequest, LeaveType
from apps.payroll.models import (
    EmployeePayrollCalculation,
    EmployeeSalaryAssignment,
    PaymentMode,
    PayrollConfiguration,
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


class EmploymentEligibilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch = Branch.objects.create(name="Elig Branch", latitude=23.81, longitude=90.41, radius_meters=200)
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
        cls.desig = Designation.objects.create(name="Officer")
        cls.admin = User.objects.create_superuser(email="elig_adm@example.com", password="pass", role="admin")

    def _make_emp(self, code, joined_date, status=EmployeeStatus.ACTIVE, is_active=True):
        u = User.objects.create_user(email=f"{code.lower()}@example.com", password="pass", role="staff")
        m = Employee.objects.create(
            user=u,
            employee_number=code,
            first_name="Emp",
            last_name=code,
            department=self.dept,
            designation=self.desig,
            branch=self.branch,
            status=status,
            joined_date=joined_date,
        )
        p = EmployeeProfile.objects.create(
            user=u,
            master_employee=m,
            employee_id=code,
            full_name=f"Emp {code}",
            department="Ops",
            phone=f"016{abs(hash(code)) % 100000000:08d}",
            branch=self.branch,
            is_active=is_active,
            joined_date=joined_date,
        )
        return m, p

    def test_1_pre_joining_zero_absent_and_excluded_from_denominator(self):
        # Joined 5 days ago; 10 days ago through 6 days ago must be not_employed
        today = timezone.localdate()
        joined = today - timedelta(days=5)
        pre_day = today - timedelta(days=8)
        while pre_day.weekday() == 4:
            pre_day -= timedelta(days=1)

        _, prof = self._make_emp("ELIG-PRE", joined_date=joined)
        daily = get_daily_report_data(report_date=pre_day, employee_id=prof.id)
        self.assertEqual(daily["absent"], 0)
        self.assertEqual(daily["rows"][0]["status"], "not_employed")
        self.assertEqual(get_absent_records(date_from=pre_day, date_to=pre_day, employee_id=prof.id), [])

        analytics = get_attendance_analytics(
            self.admin,
            {"range": "custom", "start_date": pre_day.isoformat(), "end_date": pre_day.isoformat(), "branch": str(self.branch.id)},
        )
        self.assertEqual(analytics["analytics_summary"]["total_absent"], 0)
        self.assertEqual(analytics["analytics_summary"]["eligible_working_days"], 0)

    def test_2_post_last_working_day_not_applicable(self):
        today = timezone.localdate()
        joined = today - timedelta(days=20)
        exit_day = today - timedelta(days=6)
        post_day = today - timedelta(days=2)
        if post_day.weekday() == 4:
            post_day -= timedelta(days=1)

        master, prof = self._make_emp("ELIG-EXIT", joined_date=joined)
        EmploymentHistory.objects.create(
            employee=master,
            field_changed="status",
            old_value="Active",
            new_value="Resigned",
            effective_date=exit_day,
        )

        daily_post = get_daily_report_data(report_date=post_day, employee_id=prof.id)
        self.assertEqual(daily_post["absent"], 0)
        self.assertEqual(daily_post["rows"][0]["status"], "not_employed")
        self.assertEqual(get_absent_records(date_from=post_day, date_to=post_day, employee_id=prof.id), [])

    def test_3_rejoining_after_employment_gap(self):
        today = timezone.localdate()
        joined = today - timedelta(days=25)
        exit_day = today - timedelta(days=15)
        rejoin_day = today - timedelta(days=5)
        gap_day = today - timedelta(days=10)
        if gap_day.weekday() == 4:
            gap_day -= timedelta(days=1)

        master, prof = self._make_emp("ELIG-REJOIN", joined_date=joined)
        EmploymentHistory.objects.create(employee=master, field_changed="status", old_value="Active", new_value="Resigned", effective_date=exit_day)
        EmploymentHistory.objects.create(employee=master, field_changed="status", old_value="Resigned", new_value="Active", effective_date=rejoin_day)

        daily_gap = get_daily_report_data(report_date=gap_day, employee_id=prof.id)
        self.assertEqual(daily_gap["rows"][0]["status"], "not_employed")
        self.assertEqual(daily_gap["absent"], 0)

        after_day = today - timedelta(days=2)
        if after_day.weekday() == 4:
            after_day -= timedelta(days=1)
        daily_after = get_daily_report_data(report_date=after_day, employee_id=prof.id)
        self.assertEqual(daily_after["absent"], 1)

        # Conflicting second exit transition without rejoin -> uncertain from conflict, pre-joining still not_employed
        EmploymentHistory.objects.create(employee=master, field_changed="status", old_value="Active", new_value="Resigned", effective_date=today - timedelta(days=2))
        EmploymentHistory.objects.create(employee=master, field_changed="status", old_value="Resigned", new_value="Terminated", effective_date=today - timedelta(days=1))
        conflict_day = today - timedelta(days=1)
        if conflict_day.weekday() == 4:
            conflict_day -= timedelta(days=1)
        daily_conflict = get_daily_report_data(report_date=conflict_day, employee_id=prof.id)
        self.assertEqual(daily_conflict["absent"], 0)
        self.assertEqual(daily_conflict["review_needed"], 1)
        self.assertEqual(daily_conflict["rows"][0]["status"], "eligibility_unresolved")

    def test_4_missing_exit_date_unresolved_and_historical_attendance_preserved(self):
        today = timezone.localdate()
        joined = today - timedelta(days=10)
        pre_day = today - timedelta(days=12)
        if pre_day.weekday() == 4:
            pre_day -= timedelta(days=1)
        post_join_workday = today - timedelta(days=3)
        while post_join_workday.weekday() == 4:
            post_join_workday -= timedelta(days=1)

        # Resigned employee with NO EmploymentHistory exit date -> unresolved, do not invent exit date
        _, prof = self._make_emp("ELIG-UNRES", joined_date=joined, status=EmployeeStatus.RESIGNED, is_active=False)
        # Historical attendance on pre-joining date preserved & flagged
        ci = timezone.make_aware(datetime.combine(pre_day, datetime.strptime("09:00", "%H:%M").time()))
        Attendance.objects.create(employee=prof, date=pre_day, attendance_type="check_in", type="office", status="on_time", check_in_time=ci, check_out_time=ci + timedelta(hours=8), total_hours=8.0)

        daily_pre = get_daily_report_data(report_date=pre_day, employee_id=prof.id)
        self.assertEqual(daily_pre["present"], 1)
        self.assertTrue(daily_pre["rows"][0]["day_calc"]["eligibility_conflict"])
        self.assertTrue(daily_pre["rows"][0]["day_calc"]["eligibility_unresolved"])

        # Working day after joined_date without attendance -> review_needed, never Absent/deduction
        daily_uncertain = get_daily_report_data(report_date=post_join_workday, employee_id=prof.id)
        self.assertEqual(daily_uncertain["absent"], 0)
        self.assertEqual(daily_uncertain["review_needed"], 1)
        self.assertEqual(daily_uncertain["rows"][0]["status"], "eligibility_unresolved")
        self.assertEqual(get_absent_records(date_from=post_join_workday, date_to=post_join_workday, employee_id=prof.id), [])

        monthly = get_monthly_report_data(post_join_workday.year, post_join_workday.month, employee_id=prof.id)
        self.assertEqual(monthly["total_absent"], 0)
        self.assertGreater(monthly["total_review_needed"], 0)

        analytics = get_attendance_analytics(
            self.admin,
            {"range": "custom", "start_date": post_join_workday.isoformat(), "end_date": post_join_workday.isoformat()},
        )
        # Even when scoped via attendance/active, unresolved employee is never counted absent
        self.assertEqual(analytics["analytics_summary"]["total_absent"], 0)

    def test_5_downstream_payroll_and_bulk_query_bound(self):
        today = timezone.localdate()
        month_start = today.replace(day=1)
        if today.day < 5:
            prev_end = month_start - timedelta(days=1)
            month_start = prev_end.replace(day=1)
            month_end = prev_end
        else:
            month_end = today
        joined = month_start + timedelta(days=3)

        master, prof = self._make_emp("ELIG-PAY", joined_date=joined)
        comp = SalaryComponent.objects.create(name="Basic", code="ELIG_BASIC", type=SalaryComponentType.EARNING, value_type=SalaryComponentValueType.FIXED, value=Decimal("30000.00"))
        struct = SalaryStructure.objects.create(name="Std")
        SalaryStructureComponent.objects.create(salary_structure=struct, salary_component=comp, value_type=SalaryComponentValueType.FIXED, value=Decimal("30000.00"))
        EmployeeSalaryAssignment.objects.create(employee=master, salary_structure=struct, gross_salary=Decimal("30000.00"), payment_mode=PaymentMode.BANK, effective_from=month_start)

        run_draft = PayrollRun.objects.create(name="Draft", period_start=month_start, period_end=month_end, status=PayrollRunStatus.DRAFT)
        with CaptureQueriesContext(connection) as ctx:
            monthly = get_monthly_report_data(month_start.year, month_start.month, employee_id=prof.id)
        self.assertLessEqual(len(ctx), 15)

        calcs = PayrollService.sync_payroll_inputs(run_draft)
        my_calc = next(c for c in calcs if c.employee_id == master.id)
        self.assertEqual(my_calc.unpaid_absent_days, Decimal(str(monthly["rows"][0]["absent"])))
        existing_count = EmployeePayrollCalculation.objects.filter(payroll_run=run_draft).count()
        existing_absent = my_calc.unpaid_absent_days

        # Mixed resolved + unresolved employee in same payroll run: reject atomically with no partial writes
        unres_master, _ = self._make_emp("ELIG-MIX", joined_date=month_start, status=EmployeeStatus.RESIGNED, is_active=True)
        EmployeeSalaryAssignment.objects.create(employee=unres_master, salary_structure=struct, gross_salary=Decimal("30000.00"), payment_mode=PaymentMode.BANK, effective_from=month_start)
        with self.assertRaises(ValidationError) as cm:
            PayrollService.sync_payroll_inputs(run_draft)
        self.assertIn("ELIG-MIX", str(cm.exception))
        self.assertEqual(EmployeePayrollCalculation.objects.filter(payroll_run=run_draft).count(), existing_count)
        my_calc.refresh_from_db()
        self.assertEqual(my_calc.unpaid_absent_days, existing_absent)

        # Locked payroll run must not be modified
        run_draft.status = PayrollRunStatus.APPROVED_LOCKED
        run_draft.save()
        with self.assertRaises(ValidationError):
            PayrollService.sync_payroll_inputs(run_draft)

    def test_6_unresolved_employment_with_approved_unpaid_leave_rejected_without_writes(self):
        p_start = date(2026, 10, 25)
        p_end = date(2026, 10, 27)  # Sun-Tue (all working days)
        comp = SalaryComponent.objects.create(
            name="Basic", code="ELIG_LV_BASIC", type=SalaryComponentType.EARNING,
            value_type=SalaryComponentValueType.FIXED, value=Decimal("30000.00"),
        )
        struct = SalaryStructure.objects.create(name="EligLvStd")
        SalaryStructureComponent.objects.create(
            salary_structure=struct, salary_component=comp,
            value_type=SalaryComponentValueType.FIXED, value=Decimal("30000.00"),
        )

        # Resolved employee synced first so stored calculation exists
        res_master, _ = self._make_emp("ELIG-LV-OK", joined_date=p_start - timedelta(days=10))
        EmployeeSalaryAssignment.objects.create(
            employee=res_master, salary_structure=struct, gross_salary=Decimal("30000.00"),
            payment_mode=PaymentMode.BANK, effective_from=p_start - timedelta(days=10),
        )
        run = PayrollRun.objects.create(name="LvUnres", period_start=p_start, period_end=p_end, status=PayrollRunStatus.DRAFT)
        calcs = PayrollService.sync_payroll_inputs(run)
        res_calc = next(c for c in calcs if c.employee_id == res_master.id)
        stored_absent = res_calc.unpaid_absent_days
        stored_count = EmployeePayrollCalculation.objects.filter(payroll_run=run).count()

        # Unresolved employee (resigned without exit date) + approved unpaid leave covering entire period
        unres_master, unres_prof = self._make_emp(
            "ELIG-LV-BAD", joined_date=p_start - timedelta(days=10), status=EmployeeStatus.RESIGNED, is_active=True
        )
        EmployeeSalaryAssignment.objects.create(
            employee=unres_master, salary_structure=struct, gross_salary=Decimal("30000.00"),
            payment_mode=PaymentMode.BANK, effective_from=p_start - timedelta(days=10),
        )
        unpaid_lt = LeaveType.objects.create(name="UnpaidElig", deduction_percent=Decimal("100.00"))
        LeaveRequest.objects.create(
            employee=unres_prof, leave_type=unpaid_lt,
            start_date=p_start, end_date=p_end, number_of_days=Decimal("3.0"),
            reason="Unpaid", status="approved",
        )

        daily = get_daily_report_data(report_date=p_start, employee_id=unres_prof.id)
        row = daily["rows"][0]
        self.assertEqual(row["status"], "eligibility_unresolved")
        self.assertEqual(row["status_display"], "Review Needed")
        self.assertTrue(row["eligibility_unresolved"])
        self.assertTrue(row["needs_review"])
        self.assertFalse(row["day_calc"]["is_on_leave"])

        with self.assertRaises(ValidationError) as cm:
            PayrollService.sync_payroll_inputs(run)
        self.assertIn("ELIG-LV-BAD", str(cm.exception))
        self.assertEqual(EmployeePayrollCalculation.objects.filter(payroll_run=run).count(), stored_count)
        res_calc.refresh_from_db()
        self.assertEqual(res_calc.unpaid_absent_days, stored_absent)

    def test_7_known_employed_approved_leave_unchanged_and_query_bounded(self):
        p_start = date(2026, 10, 25)
        p_end = date(2026, 10, 26)
        comp = SalaryComponent.objects.create(
            name="Basic", code="ELIG_LV2_BASIC", type=SalaryComponentType.EARNING,
            value_type=SalaryComponentValueType.FIXED, value=Decimal("30000.00"),
        )
        struct = SalaryStructure.objects.create(name="EligLv2Std")
        SalaryStructureComponent.objects.create(
            salary_structure=struct, salary_component=comp,
            value_type=SalaryComponentValueType.FIXED, value=Decimal("30000.00"),
        )
        master, prof = self._make_emp("ELIG-LV-VALID", joined_date=p_start - timedelta(days=10))
        EmployeeSalaryAssignment.objects.create(
            employee=master, salary_structure=struct, gross_salary=Decimal("30000.00"),
            payment_mode=PaymentMode.BANK, effective_from=p_start - timedelta(days=10),
        )
        unpaid_lt = LeaveType.objects.create(name="UnpaidValid", deduction_percent=Decimal("100.00"))
        LeaveRequest.objects.create(
            employee=prof, leave_type=unpaid_lt,
            start_date=p_start, end_date=p_start, number_of_days=Decimal("1.0"),
            reason="Valid", status="approved",
        )

        with CaptureQueriesContext(connection) as ctx:
            daily = get_daily_report_data(start_date=p_start, end_date=p_end, employee_id=prof.id)
        self.assertLessEqual(len(ctx), 10)
        leave_row = next(r for r in daily["rows"] if r["date"] == p_start)
        self.assertEqual(leave_row["status"], "on_leave")
        self.assertFalse(leave_row["eligibility_unresolved"])
        self.assertFalse(leave_row["needs_review"])
        self.assertFalse(leave_row["day_calc"]["eligibility_conflict"])

        run = PayrollRun.objects.create(name="LvValid", period_start=p_start, period_end=p_end, status=PayrollRunStatus.DRAFT)
        calcs = PayrollService.sync_payroll_inputs(run)
        c = next(x for x in calcs if x.employee_id == master.id)
        self.assertEqual(c.source_total_approved_leave_days, Decimal("1.00"))
        self.assertEqual(c.unpaid_absent_days, Decimal("2.00"))

    def test_8_rejoining_interval_valid_despite_historical_uncertainty(self):
        p_start = date(2026, 10, 25)
        p_end = date(2026, 10, 26)
        master, prof = self._make_emp("ELIG-REJOIN-HIST", joined_date=p_start - timedelta(days=30))
        # Historical uncertainty: duplicate exit before documented rejoining
        EmploymentHistory.objects.create(
            employee=master, field_changed="status", old_value="Active", new_value="Resigned",
            effective_date=p_start - timedelta(days=20),
        )
        EmploymentHistory.objects.create(
            employee=master, field_changed="status", old_value="Resigned", new_value="Terminated",
            effective_date=p_start - timedelta(days=18),
        )
        EmploymentHistory.objects.create(
            employee=master, field_changed="status", old_value="Terminated", new_value="Active",
            effective_date=p_start - timedelta(days=5),
        )
        lt = LeaveType.objects.create(name="PaidRejoin", deduction_percent=Decimal("0.00"))
        LeaveRequest.objects.create(
            employee=prof, leave_type=lt,
            start_date=p_start, end_date=p_end, number_of_days=Decimal("2.0"),
            reason="Rejoined", status="approved",
        )

        daily = get_daily_report_data(start_date=p_start, end_date=p_end, employee_id=prof.id)
        for r in daily["rows"]:
            self.assertEqual(r["status"], "on_leave")
            self.assertFalse(r["eligibility_unresolved"])
            self.assertFalse(r["needs_review"])
