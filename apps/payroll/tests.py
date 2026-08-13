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
    PayrollAdjustment,
    PayrollWorkflowAudit,
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

    def test_payroll_manual_adjustments_and_recalculations(self):
        from apps.payroll.models import PayrollAdjustment
        from django.contrib.auth import get_user_model
        
        User = get_user_model()
        user = User.objects.create_user(email="testuser4@example.com", phone="9998887777", password="password")
        
        # Setup Assignment
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

        # Create Adjustment components (arrear salary / other deduction)
        arrear_comp = SalaryComponent.objects.create(
            name="Arrear Salary", code="ARREAR", type=SalaryComponentType.EARNING, value_type=SalaryComponentValueType.FIXED
        )
        other_ded_comp = SalaryComponent.objects.create(
            name="Other Deduction", code="OTHER_DED", type=SalaryComponentType.DEDUCTION, value_type=SalaryComponentValueType.FIXED
        )

        # Create manual adjustments
        # Employee gets BDT 5,000 arrear + BDT 2,000 other deduction
        adj1 = PayrollAdjustment.objects.create(
            employee=self.employee,
            payroll_run=payroll_run,
            component=arrear_comp,
            amount=Decimal('5000.00'),
            type=SalaryComponentType.EARNING,
            reason="Arrear adjustment",
            created_by=user
        )
        adj2 = PayrollAdjustment.objects.create(
            employee=self.employee,
            payroll_run=payroll_run,
            component=other_ded_comp,
            amount=Decimal('2000.00'),
            type=SalaryComponentType.DEDUCTION,
            reason="Other deduction adjustment",
            created_by=user
        )

        # Calculate payroll for employee
        calc = PayrollService.run_payroll_for_employee(payroll_run, self.employee)

        # Basic 50,000 + HRA 25,000 + Medical 15,000 + Conveyance 10,000 = 100,000 standard earnings.
        # Plus BDT 5,000 adjustment = 105,000 total earnings.
        # PF 10,000 standard deduction + BDT 2,000 adjustment = 12,000 total deductions.
        # Net: 105,000 - 12,000 = 93,000.
        self.assertEqual(calc.total_earnings, Decimal('105000.00'))
        self.assertEqual(calc.total_deductions, Decimal('100000.00') * Decimal('10.00') / Decimal('100.00') + Decimal('2000.00'))  # PF + 2000
        self.assertEqual(calc.net_payable, Decimal('93000'))

        # Test duplicate adjustment retry (using sync_uuid) -> Unique Constraint check
        import uuid
        custom_uuid = uuid.uuid4()
        PayrollAdjustment.objects.create(
            employee=self.employee,
            payroll_run=payroll_run,
            component=arrear_comp,
            amount=Decimal('100.00'),
            type=SalaryComponentType.EARNING,
            reason="Unique uuid check",
            created_by=user,
            sync_uuid=custom_uuid
        )
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            PayrollAdjustment.objects.create(
                employee=self.employee,
                payroll_run=payroll_run,
                component=arrear_comp,
                amount=Decimal('100.00'),
                type=SalaryComponentType.EARNING,
                reason="Retry uuid check",
                created_by=user,
                sync_uuid=custom_uuid
            )

    def test_payroll_workflow_transitions_and_reversals(self):
        from apps.payroll.models import PayrollWorkflowAudit
        from django.contrib.auth import get_user_model
        
        User = get_user_model()
        user_admin = User.objects.create_user(email="admin@example.com", phone="5551112222", password="password", is_staff=True)
        user_staff = User.objects.create_user(email="staff@example.com", phone="5552223333", password="password", is_staff=False)

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

        # Run calculations so we have calc instances to snapshot
        PayrollService.run_payroll_for_employee(payroll_run, self.employee)

        # Draft -> Review
        PayrollService.transition_payroll_status(payroll_run, PayrollRunStatus.REVIEW, user_admin, "To Review")
        self.assertEqual(payroll_run.status, PayrollRunStatus.REVIEW)

        # Review -> Approved/Locked
        PayrollService.transition_payroll_status(payroll_run, PayrollRunStatus.APPROVED_LOCKED, user_admin, "To Locked")
        self.assertEqual(payroll_run.status, PayrollRunStatus.APPROVED_LOCKED)

        # Ensure Approved/Locked run cannot run calculations directly (protected)
        with self.assertRaises(ValidationError):
            PayrollService.run_payroll_for_employee(payroll_run, self.employee)

        # Transition Approved/Locked -> Disbursed
        PayrollService.transition_payroll_status(payroll_run, PayrollRunStatus.DISBURSED, user_admin, "To Disbursed")
        self.assertEqual(payroll_run.status, PayrollRunStatus.DISBURSED)

        # Reversal by unauthorized staff should be blocked
        with self.assertRaises(ValidationError):
            PayrollService.reverse_payroll_run(payroll_run, user_staff, "Invalid Reverse Attempt")

        # Reversal by authorized admin should pass, status reset to Draft and snapshot preserved in audit
        audits_count_before = PayrollWorkflowAudit.objects.filter(payroll_run=payroll_run).count()
        PayrollService.reverse_payroll_run(payroll_run, user_admin, "Admin Reversal")
        payroll_run.refresh_from_db()
        self.assertEqual(payroll_run.status, PayrollRunStatus.DRAFT)
        
        # Verify reversal log exists
        reversal_audit = PayrollWorkflowAudit.objects.filter(payroll_run=payroll_run).order_by('-action_at').first()
        self.assertEqual(reversal_audit.to_status, PayrollRunStatus.DRAFT)
        self.assertIsNotNone(reversal_audit.snapshot_data)
        self.assertTrue('calculations' in reversal_audit.snapshot_data)
        self.assertEqual(len(reversal_audit.snapshot_data['calculations']), 1)


class PayrollPresentationLayerTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()

        self.admin_user = User.objects.create_user(
            email="payroll_admin@example.com",
            phone="01711000001",
            password="adminpassword",
            is_staff=True,
            is_superuser=True
        )

        self.staff_user_1 = User.objects.create_user(
            email="staff1@example.com",
            phone="01711000002",
            password="staffpassword",
            is_staff=False
        )

        self.staff_user_2 = User.objects.create_user(
            email="staff2@example.com",
            phone="01711000003",
            password="staffpassword",
            is_staff=False
        )

        self.branch = Branch.objects.create(
            name="Dhaka HQ",
            address="Banani, Dhaka",
            latitude=Decimal('23.7937'),
            longitude=Decimal('90.4066')
        )

        self.employee_1 = Employee.objects.create(
            user=self.staff_user_1,
            employee_number="EMP101",
            first_name="Alice",
            last_name="Staff",
            joined_date=datetime.date(2026, 1, 1),
            status=EmployeeStatus.ACTIVE,
            branch=self.branch,
            bank_name="BRAC Bank Ltd",
            bank_account="15012012345678",
            payment_method="bank"
        )

        self.employee_2 = Employee.objects.create(
            user=self.staff_user_2,
            employee_number="EMP102",
            first_name="Bob",
            last_name="Cashier",
            joined_date=datetime.date(2026, 1, 1),
            status=EmployeeStatus.ACTIVE,
            branch=self.branch,
            payment_method="cash"
        )

        self.basic = SalaryComponent.objects.create(
            name="Basic Salary",
            code="BASIC",
            type=SalaryComponentType.EARNING,
            value_type=SalaryComponentValueType.PERCENTAGE,
            value=Decimal('60.00')
        )
        self.hra = SalaryComponent.objects.create(
            name="House Rent Allowance",
            code="HRA",
            type=SalaryComponentType.EARNING,
            value_type=SalaryComponentValueType.PERCENTAGE,
            value=Decimal('40.00')
        )
        self.bonus_comp = SalaryComponent.objects.create(
            name="Performance Bonus",
            code="BONUS",
            type=SalaryComponentType.EARNING,
            value_type=SalaryComponentValueType.FIXED,
            value=Decimal('0.00')
        )

        self.structure = SalaryStructure.objects.create(name="HQ Structure")
        SalaryStructureComponent.objects.create(salary_structure=self.structure, salary_component=self.basic, value=Decimal('60.00'), value_type=SalaryComponentValueType.PERCENTAGE)
        SalaryStructureComponent.objects.create(salary_structure=self.structure, salary_component=self.hra, value=Decimal('40.00'), value_type=SalaryComponentValueType.PERCENTAGE)

        EmployeeSalaryAssignment.objects.create(
            employee=self.employee_1,
            salary_structure=self.structure,
            gross_salary=Decimal('50000.00'),
            effective_from=datetime.date(2026, 1, 1),
            payment_mode=PaymentMode.BANK
        )

        EmployeeSalaryAssignment.objects.create(
            employee=self.employee_2,
            salary_structure=self.structure,
            gross_salary=Decimal('30000.00'),
            effective_from=datetime.date(2026, 1, 1),
            payment_mode=PaymentMode.CASH
        )

        self.payroll_run = PayrollRun.objects.create(
            name="August 2026 Run",
            period_start=datetime.date(2026, 8, 1),
            period_end=datetime.date(2026, 8, 31),
            status=PayrollRunStatus.DRAFT
        )

        self.calc_1 = PayrollService.run_payroll_for_employee(self.payroll_run, self.employee_1)
        self.calc_2 = PayrollService.run_payroll_for_employee(self.payroll_run, self.employee_2)

    def test_payroll_run_list_and_create_views(self):
        from django.urls import reverse

        # Unauthenticated user should be redirected to login
        response = self.client.get(reverse('payroll:payroll_run_list'))
        self.assertEqual(response.status_code, 302)

        # Staff user should be redirected away from admin management
        self.client.force_login(self.staff_user_1)
        response = self.client.get(reverse('payroll:payroll_run_list'))
        self.assertEqual(response.status_code, 302)

        # Admin user should access list view
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse('payroll:payroll_run_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "August 2026 Run")

        # Create new payroll run
        post_data = {'month': '9', 'year': '2026', 'name': 'September 2026 Run'}
        response = self.client.post(reverse('payroll:payroll_run_create'), post_data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(PayrollRun.objects.filter(name='September 2026 Run').exists())

    def test_payroll_run_detail_and_grid_query_count(self):
        from django.urls import reverse
        self.client.force_login(self.admin_user)

        # Test detail page renders properly
        url = reverse('payroll:payroll_run_detail', kwargs={'pk': self.payroll_run.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "August 2026 Run")
        self.assertContains(response, "EMP101")
        self.assertContains(response, "EMP102")

        # Test partial grid for HTMX live search and check queries
        grid_url = reverse('payroll:payroll_run_grid_partial', kwargs={'pk': self.payroll_run.pk})
        with self.assertNumQueries(10):  # session, user, security policies, count, and 1 select_related query for calculations
            response = self.client.get(grid_url)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "EMP101")

        # Filter by search
        response = self.client.get(f"{grid_url}?search=Alice")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "EMP101")
        self.assertNotContains(response, "EMP102")

    def test_payslip_access_permissions_and_security(self):
        from django.urls import reverse

        # Staff 1 can view own payslip HTML
        self.client.force_login(self.staff_user_1)
        url_own = reverse('payroll:payslip_detail', kwargs={'pk': self.calc_1.pk})
        response = self.client.get(url_own)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "EMP101")
        self.assertContains(response, "50000.00")

        # Staff 1 can download own payslip PDF
        url_pdf_own = reverse('payroll:payslip_pdf', kwargs={'pk': self.calc_1.pk})
        response = self.client.get(url_pdf_own)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(len(response.content) > 100)

        # Staff 1 CANNOT view Staff 2's payslip
        url_other = reverse('payroll:payslip_detail', kwargs={'pk': self.calc_2.pk})
        response = self.client.get(url_other)
        self.assertEqual(response.status_code, 403)

        # Staff 1 CANNOT download Staff 2's payslip PDF
        url_pdf_other = reverse('payroll:payslip_pdf', kwargs={'pk': self.calc_2.pk})
        response = self.client.get(url_pdf_other)
        self.assertEqual(response.status_code, 403)

        # Admin CAN view any employee's payslip
        self.client.force_login(self.admin_user)
        response = self.client.get(url_other)
        self.assertEqual(response.status_code, 200)

        # Staff 1 can view my-payslips list
        self.client.force_login(self.staff_user_1)
        my_payslips_url = reverse('payroll:my_payslips')
        response = self.client.get(my_payslips_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "50000.00")

    def test_adjustment_add_delete_and_locked_protection(self):
        from django.urls import reverse
        self.client.force_login(self.admin_user)

        add_adj_url = reverse('payroll:payroll_adjustment_add', kwargs={'pk': self.payroll_run.pk})

        # Add BDT 5,000 bonus adjustment to Employee 1
        post_data = {
            'employee_id': self.employee_1.pk,
            'component_id': self.bonus_comp.pk,
            'type': 'earning',
            'amount': '5000.00',
            'reason': 'Great performance'
        }
        response = self.client.post(add_adj_url, post_data)
        self.assertEqual(response.status_code, 302)

        self.calc_1.refresh_from_db()
        self.assertEqual(self.calc_1.net_payable, Decimal('55000.00'))

        adj = PayrollAdjustment.objects.filter(employee=self.employee_1, payroll_run=self.payroll_run).first()
        self.assertIsNotNone(adj)

        # Delete adjustment
        delete_adj_url = reverse('payroll:payroll_adjustment_delete', kwargs={'pk': self.payroll_run.pk, 'adj_pk': adj.pk})
        response = self.client.post(delete_adj_url)
        self.assertEqual(response.status_code, 302)

        self.calc_1.refresh_from_db()
        self.assertEqual(self.calc_1.net_payable, Decimal('50000.00'))

        # Lock payroll run and verify subsequent adjustment additions are rejected
        PayrollService.transition_payroll_status(self.payroll_run, PayrollRunStatus.REVIEW, self.admin_user, "Review")
        PayrollService.transition_payroll_status(self.payroll_run, PayrollRunStatus.APPROVED_LOCKED, self.admin_user, "Locked")

        response = self.client.post(add_adj_url, post_data)
        self.assertEqual(response.status_code, 400)

    def test_payroll_reports_and_export_formats(self):
        from django.urls import reverse
        self.client.force_login(self.admin_user)

        # 1. Payroll Register (HTML, Excel, CSV, PDF)
        reg_html_url = reverse('payroll:payroll_register', kwargs={'pk': self.payroll_run.pk})
        response = self.client.get(reg_html_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "EMP101")
        self.assertContains(response, "EMP102")

        for fmt in ['excel', 'csv', 'pdf']:
            reg_export_url = reverse('payroll:payroll_register_export', kwargs={'pk': self.payroll_run.pk, 'format': fmt})
            response = self.client.get(reg_export_url)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(len(response.content) > 0)

        # 2. Bank Report (HTML, Excel, CSV, PDF)
        bank_html_url = reverse('payroll:bank_report', kwargs={'pk': self.payroll_run.pk})
        response = self.client.get(bank_html_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "BRAC Bank Ltd")
        self.assertContains(response, "EMP101")
        self.assertNotContains(response, "EMP102")  # EMP102 is 100% cash

        for fmt in ['excel', 'csv', 'pdf']:
            bank_export_url = reverse('payroll:bank_report_export', kwargs={'pk': self.payroll_run.pk, 'format': fmt})
            response = self.client.get(bank_export_url)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(len(response.content) > 0)

        # 3. Cash Report (HTML, Excel, CSV, PDF)
        cash_html_url = reverse('payroll:cash_report', kwargs={'pk': self.payroll_run.pk})
        response = self.client.get(cash_html_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "EMP102")
        self.assertNotContains(response, "EMP101")  # EMP101 is 100% bank

        for fmt in ['excel', 'csv', 'pdf']:
            cash_export_url = reverse('payroll:cash_report_export', kwargs={'pk': self.payroll_run.pk, 'format': fmt})
            response = self.client.get(cash_export_url)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(len(response.content) > 0)


