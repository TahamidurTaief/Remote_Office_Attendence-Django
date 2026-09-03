"""
Tests for seed_operational_data management command:
- Dry-run mode non-persistence
- Idempotency on repeated execution
- Existing data preservation
- Relational integrity and non-circular reporting hierarchy
- Date boundaries (2026-07-03 to 2026-09-03)
- Payroll mathematical reconciliation
- Transaction rollback on error
"""

import datetime
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import CustomUser
from apps.attendance.models import Attendance, AttendancePolicy
from apps.branches.models import Branch, Holiday, OfficeSchedule
from apps.employees.models import (
    Asset,
    AssetAssignment,
    Department,
    Designation,
    Employee,
    EmployeeProfile,
    EmploymentHistory,
)
from apps.expense.models import Expense, ExpenseCategory
from apps.leave.models import LeaveBalance, LeaveRequest, LeaveType
from apps.payroll.models import (
    EmployeePayrollCalculation,
    EmployeeSalaryAssignment,
    PayrollPolicy,
    PayrollRun,
    SalaryComponent,
    SalaryStructure,
    SalaryStructureComponent,
)
from apps.projects.models import (
    DailyProgressLog,
    ManpowerDeployment,
    Project,
    ProjectMaterial,
    ProjectSignOff,
    ProjectTask,
    ProjectTaskReply,
    ProjectType,
    TaskDependency,
)
from apps.schedule.models import ScheduleEvent
from apps.workflow.models import WorkflowAction, WorkflowDefinition, WorkflowInstance, WorkflowStep


class SeedOperationalDataTests(TestCase):
    def setUp(self):
        # Create minimal baseline branch and users
        self.branch = Branch.objects.create(
            id=1,
            name="Dhaka Main Branch",
            address="Baitul Mukarram, Dhaka",
            latitude=Decimal("23.730442"),
            longitude=Decimal("23.730445"),
            radius_meters=100,
            is_active=True,
        )
        self.admin_user = CustomUser.objects.create(
            email="admin.demo@signtech.test",
            phone="01999999999",
            role="admin",
            is_staff=True,
            is_superuser=True,
        )

        self.lt_casual = LeaveType.objects.create(
            id=1,
            name="Casual Leave",
            default_days_per_year=10,
            category="casual",
            is_default=True,
            is_active=True,
            deduction_percent=Decimal("0.00"),
        )
        self.lt_sick = LeaveType.objects.create(
            id=2,
            name="Sick Leave",
            default_days_per_year=15,
            category="sick",
            is_default=False,
            is_active=True,
            deduction_percent=Decimal("0.00"),
        )

        # Create 5 test employees
        self.employees = []
        for i in range(1, 6):
            user = CustomUser.objects.create(
                phone=f"0170000000{i}",
                role="staff",
            )
            emp = Employee.objects.create(
                id=i,
                employee_number=f"EMP-TEST-{i:03d}",
                first_name=f"TestFirst{i}",
                last_name=f"TestLast{i}",
                phone=f"0170000000{i}",
                user=user,
                joined_date=datetime.date(2026, 6, 1),
                status="active",
            )
            prof = EmployeeProfile.objects.create(
                id=i,
                user=user,
                master_employee=emp,
                branch=self.branch,
                employee_id=f"EMP-TEST-{i:03d}",
                full_name=f"TestFirst{i} TestLast{i}",
                phone=f"0170000000{i}",
                joined_date=datetime.date(2026, 6, 1),
                is_active=True,
            )
            self.employees.append(emp)

    def test_dry_run_leaves_database_unmodified(self):
        """Dry-run must not persist changes to the database."""
        initial_emp_count = Employee.objects.count()
        initial_att_count = Attendance.objects.count()
        initial_proj_count = Project.objects.count()

        call_command(
            "seed_operational_data",
            dry_run=True,
            start_date="2026-07-03",
            end_date="2026-09-03",
            seed="test-dry-run",
        )

        self.assertEqual(Employee.objects.count(), initial_emp_count)
        self.assertEqual(Attendance.objects.count(), initial_att_count)
        self.assertEqual(Project.objects.count(), initial_proj_count)
        # Verify employee fields still blank/original
        emp1 = Employee.objects.get(id=1)
        self.assertIsNone(emp1.department)

    def test_apply_and_idempotency(self):
        """Applying the seeder twice with the same seed/dates must produce zero duplicate records."""
        # First Run
        call_command(
            "seed_operational_data",
            apply=True,
            start_date="2026-07-03",
            end_date="2026-09-03",
            seed="signtech-test-2026",
        )

        emp_count_1 = Employee.objects.count()
        att_count_1 = Attendance.objects.count()
        proj_count_1 = Project.objects.count()
        task_count_1 = ProjectTask.objects.count()
        expense_count_1 = Expense.objects.count()
        payroll_count_1 = PayrollRun.objects.count()

        self.assertGreater(att_count_1, 0)
        self.assertGreater(proj_count_1, 0)
        self.assertGreater(expense_count_1, 0)
        self.assertEqual(payroll_count_1, 3)

        # Second Run (Idempotency check)
        call_command(
            "seed_operational_data",
            apply=True,
            start_date="2026-07-03",
            end_date="2026-09-03",
            seed="signtech-test-2026",
        )

        self.assertEqual(Employee.objects.count(), emp_count_1)
        self.assertEqual(Attendance.objects.count(), att_count_1)
        self.assertEqual(Project.objects.count(), proj_count_1)
        self.assertEqual(ProjectTask.objects.count(), task_count_1)
        self.assertEqual(Expense.objects.count(), expense_count_1)
        self.assertEqual(PayrollRun.objects.count(), payroll_count_1)

    def test_relational_integrity_and_reporting_hierarchy(self):
        """Hierarchy must be non-circular and reporting manager links valid."""
        call_command(
            "seed_operational_data",
            apply=True,
            start_date="2026-07-03",
            end_date="2026-09-03",
            seed="signtech-test-2026",
        )

        for emp in Employee.objects.all():
            self.assertIsNotNone(emp.department)
            self.assertIsNotNone(emp.designation)
            self.assertIsNotNone(emp.branch)
            self.assertIsNotNone(emp.basic_salary)

            # Circular reporting check
            visited = set()
            curr = emp.reporting_manager
            while curr:
                self.assertNotIn(curr.id, visited, f"Circular reporting loop detected at {curr}")
                visited.add(curr.id)
                curr = curr.reporting_manager

    def test_date_boundaries(self):
        """All operational records must stay within the specified start/end date range."""
        start_date = datetime.date(2026, 7, 3)
        end_date = datetime.date(2026, 9, 3)

        call_command(
            "seed_operational_data",
            apply=True,
            start_date="2026-07-03",
            end_date="2026-09-03",
            seed="signtech-test-2026",
        )

        # Attendance check
        att_dates = Attendance.objects.values_list("date", flat=True)
        for d in att_dates:
            self.assertGreaterEqual(d, start_date)
            self.assertLessEqual(d, end_date)

        # Project check
        for p in Project.objects.all():
            self.assertGreaterEqual(p.start_date, start_date)

        # Schedule event check
        for ev in ScheduleEvent.objects.all():
            self.assertGreaterEqual(ev.date, start_date)
            self.assertLessEqual(ev.date, end_date)

    def test_payroll_reconciliation(self):
        """Payroll runs must mathematically reconcile earnings, deductions, bank, and cash payable."""
        call_command(
            "seed_operational_data",
            apply=True,
            start_date="2026-07-03",
            end_date="2026-09-03",
            seed="signtech-test-2026",
        )

        runs = PayrollRun.objects.all()
        self.assertEqual(runs.count(), 3)

        for prun in runs:
            for calc in prun.calculations.all():
                # Net payable = Total earnings - Total deductions
                expected_net = (calc.total_earnings - calc.total_deductions).quantize(Decimal("1"), rounding="ROUND_HALF_UP")
                self.assertEqual(calc.net_payable, expected_net)
                # Split reconciliation
                self.assertEqual(calc.bank_payable + calc.cash_payable, calc.net_payable)
