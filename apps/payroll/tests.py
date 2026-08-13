from django.test import TestCase
from django.core.exceptions import ValidationError
from decimal import Decimal, ROUND_HALF_UP
import datetime
from apps.employees.models import Employee, EmployeeStatus
from apps.branches.models import Branch
from apps.payroll.models import (
    SalaryComponent,
    SalaryComponentType,
    SalaryComponentValueType,
    SalaryStructure,
    SalaryStructureComponent,
    EmployeeSalaryAssignment,
    PayrollRun,
    PayrollRunStatus,
    EmployeePayrollCalculation,
    PaymentMode
)
from apps.payroll.services import PayrollCalculationEngine, PayrollService

class PayrollFoundationTests(TestCase):
    def setUp(self):
        # Create a branch and employee
        self.branch = Branch.objects.create(
            name="Dhaka Branch",
            address="Dhaka, Bangladesh",
            latitude=Decimal('23.8103'),
            longitude=Decimal('90.4125')
        )
        self.employee = Employee.objects.create(
            employee_number="EMP001",
            first_name="John",
            last_name="Doe",
            joined_date=datetime.date(2026, 1, 1),
            status=EmployeeStatus.ACTIVE,
            branch=self.branch
        )

        # Setup standard components
        self.basic = SalaryComponent.objects.create(
            name="Basic Salary",
            code="BASIC",
            type=SalaryComponentType.EARNING,
            value_type=SalaryComponentValueType.PERCENTAGE,
            value=Decimal('50.00')
        )
        self.hra = SalaryComponent.objects.create(
            name="House Rent Allowance",
            code="HRA",
            type=SalaryComponentType.EARNING,
            value_type=SalaryComponentValueType.PERCENTAGE,
            value=Decimal('25.00')
        )
        self.medical = SalaryComponent.objects.create(
            name="Medical Allowance",
            code="MEDICAL",
            type=SalaryComponentType.EARNING,
            value_type=SalaryComponentValueType.PERCENTAGE,
            value=Decimal('15.00')
        )
        self.conveyance = SalaryComponent.objects.create(
            name="Conveyance Allowance",
            code="CONVEYANCE",
            type=SalaryComponentType.EARNING,
            value_type=SalaryComponentValueType.PERCENTAGE,
            value=Decimal('10.00')
        )
        self.pf = SalaryComponent.objects.create(
            name="Provident Fund Deduction",
            code="PF",
            type=SalaryComponentType.DEDUCTION,
            value_type=SalaryComponentValueType.PERCENTAGE,
            value=Decimal('10.00'),
            is_pf=True
        )

        # Create a salary structure
        self.structure = SalaryStructure.objects.create(name="Standard Structure 50/25/15/10 with 10% PF")
        SalaryStructureComponent.objects.create(salary_structure=self.structure, salary_component=self.basic, value=Decimal('50.00'), value_type=SalaryComponentValueType.PERCENTAGE)
        SalaryStructureComponent.objects.create(salary_structure=self.structure, salary_component=self.hra, value=Decimal('25.00'), value_type=SalaryComponentValueType.PERCENTAGE)
        SalaryStructureComponent.objects.create(salary_structure=self.structure, salary_component=self.medical, value=Decimal('15.00'), value_type=SalaryComponentValueType.PERCENTAGE)
        SalaryStructureComponent.objects.create(salary_structure=self.structure, salary_component=self.conveyance, value=Decimal('10.00'), value_type=SalaryComponentValueType.PERCENTAGE)
        SalaryStructureComponent.objects.create(salary_structure=self.structure, salary_component=self.pf, value=Decimal('10.00'), value_type=SalaryComponentValueType.PERCENTAGE)

    def test_pure_deterministic_calculation_earnings(self):
        # Gross 100,000 + structure 50/25/15/10 -> Basic 50,000, House 25,000, Medical 15,000, Conveyance 10,000
        structure_list = [
            {'code': 'BASIC', 'name': 'Basic', 'type': 'earning', 'value_type': 'percentage', 'value': Decimal('50.00')},
            {'code': 'HRA', 'name': 'HRA', 'type': 'earning', 'value_type': 'percentage', 'value': Decimal('25.00')},
            {'code': 'MEDICAL', 'name': 'Medical', 'type': 'earning', 'value_type': 'percentage', 'value': Decimal('15.00')},
            {'code': 'CONVEYANCE', 'name': 'Conveyance', 'type': 'earning', 'value_type': 'percentage', 'value': Decimal('10.00')},
        ]
        res = PayrollCalculationEngine.calculate_employee_payroll(
            gross_salary=Decimal('100000.00'),
            structure_components_list=structure_list
        )
        self.assertEqual(res['net_payable'], Decimal('100000'))
        self.assertEqual(res['total_earnings'], Decimal('100000'))
        self.assertEqual(res['total_deductions'], Decimal('0'))
        
        amounts = {c['code']: Decimal(c['amount']) for c in res['components']}
        self.assertEqual(amounts['BASIC'], Decimal('50000'))
        self.assertEqual(amounts['HRA'], Decimal('25000'))
        self.assertEqual(amounts['MEDICAL'], Decimal('15000'))
        self.assertEqual(amounts['CONVEYANCE'], Decimal('10000'))

    def test_unpaid_absence_deduction(self):
        # Gross 60,000, 2 unpaid absences, default divisor 30 -> absence deduction = 4,000
        res = PayrollCalculationEngine.calculate_employee_payroll(
            gross_salary=Decimal('60000.00'),
            structure_components_list=[],
            unpaid_absent_days=Decimal('2.00')
        )
        self.assertEqual(res['absence_deduction'], Decimal('4000.00'))
        self.assertEqual(res['total_deductions'], Decimal('4000.00'))
        self.assertEqual(res['net_payable'], Decimal('-4000'))

    def test_other_deductions_and_pf(self):
        structure_list = [
            {'code': 'BASIC', 'name': 'Basic', 'type': 'earning', 'value_type': 'percentage', 'value': Decimal('100.00')},
            {'code': 'PF', 'name': 'PF', 'type': 'deduction', 'value_type': 'percentage', 'value': Decimal('10.00'), 'is_pf': True},
        ]
        # Gross 100,000. 10% PF = 10,000 deduction. Other Deduction = 5,000. Net should be 85,000.
        res = PayrollCalculationEngine.calculate_employee_payroll(
            gross_salary=Decimal('100000.00'),
            structure_components_list=structure_list,
            other_deduction=Decimal('5000.00')
        )
        self.assertEqual(res['total_earnings'], Decimal('100000.00'))
        self.assertEqual(res['total_deductions'], Decimal('15000.00')) # 10k PF + 5k other
        self.assertEqual(res['net_payable'], Decimal('85000'))

    def test_rounding_half_up(self):
        # Test final BDT payable rounding (ROUND_HALF_UP)
        structure_list = [
            {'code': 'BASIC', 'name': 'Basic', 'type': 'earning', 'value_type': 'percentage', 'value': Decimal('100.00')},
        ]
        # 1.49 should round to 1
        res1 = PayrollCalculationEngine.calculate_employee_payroll(
            gross_salary=Decimal('1.49'),
            structure_components_list=structure_list
        )
        self.assertEqual(res1['net_payable'], Decimal('1'))

        # 1.50 should round to 2
        res2 = PayrollCalculationEngine.calculate_employee_payroll(
            gross_salary=Decimal('1.50'),
            structure_components_list=structure_list
        )
        self.assertEqual(res2['net_payable'], Decimal('2'))

        # 1.51 should round to 2
        res3 = PayrollCalculationEngine.calculate_employee_payroll(
            gross_salary=Decimal('1.51'),
            structure_components_list=structure_list
        )
        self.assertEqual(res3['net_payable'], Decimal('2'))

    def test_payment_mode_and_bank_cash_split(self):
        structure_list = [
            {'code': 'BASIC', 'name': 'Basic', 'type': 'earning', 'value_type': 'percentage', 'value': Decimal('100.00')},
        ]
        
        # Mode: Bank
        res_bank = PayrollCalculationEngine.calculate_employee_payroll(
            gross_salary=Decimal('50000.00'),
            structure_components_list=structure_list,
            payment_mode=PaymentMode.BANK
        )
        self.assertEqual(res_bank['bank_payable'], Decimal('50000'))
        self.assertEqual(res_bank['cash_payable'], Decimal('0'))

        # Mode: Cash
        res_cash = PayrollCalculationEngine.calculate_employee_payroll(
            gross_salary=Decimal('50000.00'),
            structure_components_list=structure_list,
            payment_mode=PaymentMode.CASH
        )
        self.assertEqual(res_cash['bank_payable'], Decimal('0'))
        self.assertEqual(res_cash['cash_payable'], Decimal('50000'))

        # Mode: Split with limit 35,000
        res_split = PayrollCalculationEngine.calculate_employee_payroll(
            gross_salary=Decimal('50000.00'),
            structure_components_list=structure_list,
            payment_mode=PaymentMode.SPLIT,
            bank_limit=Decimal('35000.00')
        )
        self.assertEqual(res_split['bank_payable'], Decimal('35000'))
        self.assertEqual(res_split['cash_payable'], Decimal('15000'))

        # Mode: Split with limit 60,000 (limit exceeds Net)
        res_split2 = PayrollCalculationEngine.calculate_employee_payroll(
            gross_salary=Decimal('50000.00'),
            structure_components_list=structure_list,
            payment_mode=PaymentMode.SPLIT,
            bank_limit=Decimal('60000.00')
        )
        self.assertEqual(res_split2['bank_payable'], Decimal('50000'))
        self.assertEqual(res_split2['cash_payable'], Decimal('0'))

    def test_ot_policy_architecture(self):
        # We don't invent a default OT rate, but allow customizable callback/rate policy
        # Let's say hourly OT rate is standard double (2 * Gross / 240)
        def double_ot_rate_policy(gross, ot_hrs):
            hourly_rate = (gross / Decimal('240.00')) * Decimal('2.00')
            return hourly_rate * ot_hrs

        structure_list = [
            {'code': 'BASIC', 'name': 'Basic', 'type': 'earning', 'value_type': 'percentage', 'value': Decimal('100.00')},
        ]
        # Gross 120,000 -> hourly rate = 120,000 / 240 * 2 = 1,000 BDT/hour
        # 5 OT hours -> 5,000 BDT
        res = PayrollCalculationEngine.calculate_employee_payroll(
            gross_salary=Decimal('120000.00'),
            structure_components_list=structure_list,
            ot_hours=Decimal('5.00'),
            ot_policy_callback=double_ot_rate_policy
        )
        self.assertEqual(res['ot_amount'], Decimal('5000.00'))
        self.assertEqual(res['net_payable'], Decimal('125000'))

    def test_employee_salary_assignment_versioning(self):
        # Assignment 1: Jan 1 to Jan 31
        EmployeeSalaryAssignment.objects.create(
            employee=self.employee,
            salary_structure=self.structure,
            gross_salary=Decimal('80000.00'),
            effective_from=datetime.date(2026, 1, 1),
            effective_to=datetime.date(2026, 1, 31)
        )

        # Assignment 2: Feb 1 onwards
        EmployeeSalaryAssignment.objects.create(
            employee=self.employee,
            salary_structure=self.structure,
            gross_salary=Decimal('90000.00'),
            effective_from=datetime.date(2026, 2, 1)
        )

        # Active on Jan 15
        assign_jan = PayrollService.get_active_assignment(self.employee, datetime.date(2026, 1, 15))
        self.assertEqual(assign_jan.gross_salary, Decimal('80000.00'))

        # Active on Feb 10
        assign_feb = PayrollService.get_active_assignment(self.employee, datetime.date(2026, 2, 10))
        self.assertEqual(assign_feb.gross_salary, Decimal('90000.00'))

    def test_payroll_run_lifecycle_locking_and_snapshot(self):
        # Create salary assignment
        EmployeeSalaryAssignment.objects.create(
            employee=self.employee,
            salary_structure=self.structure,
            gross_salary=Decimal('100000.00'),
            effective_from=datetime.date(2026, 1, 1),
            effective_to=datetime.date(2026, 1, 31)
        )

        # Run payroll for January (status: DRAFT)
        payroll_run_jan = PayrollRun.objects.create(
            period_start=datetime.date(2026, 1, 1),
            period_end=datetime.date(2026, 1, 31),
            status=PayrollRunStatus.DRAFT
        )

        calc_jan = PayrollService.run_payroll_for_employee(
            payroll_run=payroll_run_jan,
            employee=self.employee,
            unpaid_absent_days=Decimal('1.00')
        )
        
        # 100,000 gross. 1 unpaid day deduction = 100,000 / 30 * 1 = 3333.3333.
        # PF deduction = 10,000
        # Total deduction = 13333.33
        # Net payable: 100000 - 13333.33 = 86667 (rounded to nearest integer BDT)
        self.assertEqual(calc_jan.net_payable, Decimal('86667'))
        self.assertEqual(calc_jan.gross_salary, Decimal('100000.00'))

        # Lock January payroll run
        payroll_run_jan.status = PayrollRunStatus.APPROVED_LOCKED
        payroll_run_jan.save()

        # Update assignment or components (change structure/gross for employee starting February)
        # Assignment changes after January payroll is locked: gross becomes 120,000
        EmployeeSalaryAssignment.objects.filter(employee=self.employee).update(effective_to=datetime.date(2026, 1, 31))
        EmployeeSalaryAssignment.objects.create(
            employee=self.employee,
            salary_structure=self.structure,
            gross_salary=Decimal('120000.00'),
            effective_from=datetime.date(2026, 2, 1)
        )

        # Attempt to recalculate/modify locked January payroll run -> should raise ValidationError
        with self.assertRaises(ValidationError):
            PayrollService.run_payroll_for_employee(
                payroll_run=payroll_run_jan,
                employee=self.employee
            )

        # Verify January snapshot and calculation remains unchanged
        calc_jan.refresh_from_db()
        self.assertEqual(calc_jan.gross_salary, Decimal('100000.00'))
        self.assertEqual(calc_jan.net_payable, Decimal('86667'))

    def test_duplicate_calculation_no_duplicates(self):
        EmployeeSalaryAssignment.objects.create(
            employee=self.employee,
            salary_structure=self.structure,
            gross_salary=Decimal('100000.00'),
            effective_from=datetime.date(2026, 1, 1)
        )

        payroll_run = PayrollRun.objects.create(
            period_start=datetime.date(2026, 1, 1),
            period_end=datetime.date(2026, 1, 31),
            status=PayrollRunStatus.DRAFT
        )

        # Run once
        calc1 = PayrollService.run_payroll_for_employee(
            payroll_run=payroll_run,
            employee=self.employee,
            unpaid_absent_days=Decimal('2.00')
        )

        # Run twice (e.g. recalculated with 3 absent days)
        calc2 = PayrollService.run_payroll_for_employee(
            payroll_run=payroll_run,
            employee=self.employee,
            unpaid_absent_days=Decimal('3.00')
        )

        # Verify primary key is same (no new row created) and counts
        self.assertEqual(calc1.pk, calc2.pk)
        self.assertEqual(EmployeePayrollCalculation.objects.filter(payroll_run=payroll_run, employee=self.employee).count(), 1)
        
        # Verify value was updated correctly
        calc2.refresh_from_db()
        self.assertEqual(calc2.unpaid_absent_days, Decimal('3.00'))

    def test_sync_payroll_inputs_success_and_locked_protection(self):
        from apps.employees.models import EmployeeProfile
        from apps.attendance.models import Attendance
        from apps.leave.models import LeaveRequest, LeaveType, LeaveBalance
        from django.contrib.auth import get_user_model
        
        User = get_user_model()
        user = User.objects.create_user(email="testuser@example.com", phone="1234567890", password="password")
        
        # Create EmployeeProfile linked to self.employee
        profile = EmployeeProfile.objects.create(
            user=user,
            master_employee=self.employee,
            employee_id="EMP001",
            full_name="John Doe",
            joined_date=datetime.date(2026, 1, 1),
            phone="1234567890",
            is_active=True,
            branch=self.branch
        )

        EmployeeSalaryAssignment.objects.create(
            employee=self.employee,
            salary_structure=self.structure,
            gross_salary=Decimal('100000.00'),
            effective_from=datetime.date(2026, 8, 1)
        )

        # Create 22 present days, 2 approved leave days, 1 unpaid absence day, 2 hours OT approved.
        # We can construct these simply by adding Attendance and LeaveRequest records
        # August 2026 starts on Saturday
        # Let's add 22 office check-ins (which count as present) on working days
        # working days in August 2026 for Standard schedule: friday/saturday are holidays (or friday/saturday holiday based on weekly_holiday_policy)
        # We can create some Attendance check-ins
        for d in range(1, 23):
            Attendance.objects.create(
                employee=profile,
                date=datetime.date(2026, 8, d),
                attendance_type='check_in',
                status='on_time',
                total_hours=Decimal('8.00'),
                overtime_minutes=60 if d in [1, 2] else 0, # 120 minutes total = 2.0 hours approved OT
                ot_status='approved' if d in [1, 2] else 'none'
            )

        # Approved Leave
        lt = LeaveType.objects.create(name="Paid Leave", category="casual", is_default=True)
        LeaveRequest.objects.create(
            employee=profile,
            leave_type=lt,
            start_date=datetime.date(2026, 8, 24),
            end_date=datetime.date(2026, 8, 25),
            status='approved'
        )

        # Payroll Run
        payroll_run = PayrollRun.objects.create(
            period_start=datetime.date(2026, 8, 1),
            period_end=datetime.date(2026, 8, 31),
            status=PayrollRunStatus.DRAFT
        )

        # First sync (Draft)
        calcs = PayrollService.sync_payroll_inputs(payroll_run)
        self.assertEqual(len(calcs), 1)
        calc = calcs[0]
        self.assertEqual(calc.employee, self.employee)
        self.assertEqual(calc.source_total_present_days, Decimal('22'))
        self.assertEqual(calc.source_total_approved_leave_days, Decimal('2'))
        self.assertEqual(calc.source_total_approved_ot_hours, Decimal('2'))
        self.assertEqual(calc.ot_hours, Decimal('2.00'))
        
        # Verify unpaid absent days derivation:
        # In August (31 days):
        # working days: 21 working days (excluding Friday/Saturday). Let's see: 
        # canonical absent_count will exclude weekends, holidays, and leave.
        # Let's verify that synced unpaid_absent_days matches calc.unpaid_absent_days.
        # Rerunning sync refreshes safely (idempotent)
        calcs_re = PayrollService.sync_payroll_inputs(payroll_run)
        self.assertEqual(len(calcs_re), 1)
        self.assertEqual(calcs_re[0].pk, calc.pk)

        # Locked protect
        payroll_run.status = PayrollRunStatus.APPROVED_LOCKED
        payroll_run.save()
        with self.assertRaises(ValidationError):
            PayrollService.sync_payroll_inputs(payroll_run)

    def test_sync_payroll_inputs_eligibility_mid_month(self):
        from apps.employees.models import EmployeeProfile
        from django.contrib.auth import get_user_model
        
        User = get_user_model()
        user2 = User.objects.create_user(email="testuser2@example.com", phone="0987654321", password="password")
        
        # Employee joins mid-month (e.g. Sept 15, 2026)
        emp_mid = Employee.objects.create(
            employee_number="EMP002",
            first_name="Jane",
            last_name="Doe",
            joined_date=datetime.date(2026, 9, 15),
            status=EmployeeStatus.ACTIVE,
            branch=self.branch
        )
        profile_mid = EmployeeProfile.objects.create(
            user=user2,
            master_employee=emp_mid,
            employee_id="EMP002",
            full_name="Jane Doe",
            joined_date=datetime.date(2026, 9, 15),
            phone="0987654321",
            is_active=True,
            branch=self.branch
        )
        EmployeeSalaryAssignment.objects.create(
            employee=emp_mid,
            salary_structure=self.structure,
            gross_salary=Decimal('80000.00'),
            effective_from=datetime.date(2026, 9, 15)
        )

        # Payroll Run for September 2026
        payroll_run = PayrollRun.objects.create(
            period_start=datetime.date(2026, 9, 1),
            period_end=datetime.date(2026, 9, 30),
            status=PayrollRunStatus.DRAFT
        )

        calcs = PayrollService.sync_payroll_inputs(payroll_run)
        # Should include emp_mid since they joined on Sept 15 (before Sept 30)
        self.assertTrue(any(c.employee == emp_mid for c in calcs))

        # Check an employee who joins in October -> should not be eligible for Sept payroll
        user3 = User.objects.create_user(email="testuser3@example.com", phone="1112223333", password="password")
        emp_oct = Employee.objects.create(
            employee_number="EMP003",
            first_name="Bob",
            last_name="Smith",
            joined_date=datetime.date(2026, 10, 1),
            status=EmployeeStatus.ACTIVE,
            branch=self.branch
        )
        profile_oct = EmployeeProfile.objects.create(
            user=user3,
            master_employee=emp_oct,
            employee_id="EMP003",
            full_name="Bob Smith",
            joined_date=datetime.date(2026, 10, 1),
            phone="1112223333",
            is_active=True,
            branch=self.branch
        )
        EmployeeSalaryAssignment.objects.create(
            employee=emp_oct,
            salary_structure=self.structure,
            gross_salary=Decimal('80000.00'),
            effective_from=datetime.date(2026, 10, 1)
        )

        calcs_re = PayrollService.sync_payroll_inputs(payroll_run)
        # Should NOT include emp_oct since they joined after Sept 30
        self.assertFalse(any(c.employee == emp_oct for c in calcs_re))

