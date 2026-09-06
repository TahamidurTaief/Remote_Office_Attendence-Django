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
        # PF deduction = 10% of Basic (50k) = 5,000
        # Total deduction = 5,000 + 3,333.33 = 8,333.33
        # Net payable: 100000 - 8333.33 = 91667 (rounded to nearest integer BDT)
        self.assertEqual(calc_jan.net_payable, Decimal('91667'))
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

        # Verify January snapshot remains: 100k gross, 1 absent day, PF=5k on Basic -> Net 91,667
        calc_jan.refresh_from_db()
        self.assertEqual(calc_jan.gross_salary, Decimal('100000.00'))
        self.assertEqual(calc_jan.net_payable, Decimal('91667'))

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
            att = Attendance.objects.create(
                employee=profile,
                date=datetime.date(2026, 8, d),
                attendance_type='check_in',
                status='on_time',
                total_hours=Decimal('8.00'),
                overtime_minutes=60 if d in [1, 2] else 0, # 120 minutes total = 2.0 hours approved OT
                ot_status='approved' if d in [1, 2] else 'none'
            )
            if d in [1, 2]:
                from apps.attendance.models import OvertimeRequest
                OvertimeRequest.objects.create(
                    employee=profile,
                    date=datetime.date(2026, 8, d),
                    attendance=att,
                    ot_minutes=60,
                    status='approved'
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
        # PF = 10% of Basic (50k) = 5,000 + BDT 2,000 adjustment = 7,000 total deductions.
        # Net: 105,000 - 7,000 = 98,000.
        self.assertEqual(calc.total_earnings, Decimal('105000.00'))
        self.assertEqual(calc.total_deductions, Decimal('50000.00') * Decimal('10.00') / Decimal('100.00') + Decimal('2000.00'))  # PF (on Basic) + 2000
        self.assertEqual(calc.net_payable, Decimal('98000'))

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
            phone="+8801711111111",
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
            phone="+8801722222222",
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
        self.assertContains(response, "+8801711111111")
        self.assertContains(response, "+8801722222222")

        # Test partial grid for HTMX live search and check queries
        grid_url = reverse('payroll:payroll_run_grid_partial', kwargs={'pk': self.payroll_run.pk})
        with self.assertNumQueries(16):  # session, user, tenant, security policies, notification counts, pinned menu, and calculations
            response = self.client.get(grid_url)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "+8801711111111")

        # Filter by search
        response = self.client.get(f"{grid_url}?search=Alice")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "+8801711111111")
        self.assertNotContains(response, "+8801722222222")

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


class PayrollReconciliationAndProductionReadinessTests(TestCase):
    """
    Comprehensive reconciliation and production readiness test suite.
    Validates calculations against representative Excel rows, checks intentional
    deviations, tests snapshot immutability, audits RBAC, and benchmark performance.
    """
    def setUp(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()

        self.admin = User.objects.create_user(
            email="super_admin@example.com",
            phone="01811000001",
            password="pass",
            is_staff=True,
            is_superuser=True
        )
        self.staff_a = User.objects.create_user(
            email="alice_staff@example.com",
            phone="01811000002",
            password="pass",
            is_staff=False
        )
        self.staff_b = User.objects.create_user(
            email="bob_staff@example.com",
            phone="01811000003",
            password="pass",
            is_staff=False
        )

        self.branch = Branch.objects.create(
            name="Principal Office",
            address="Dhaka",
            latitude=Decimal('23.81'),
            longitude=Decimal('90.41')
        )

        # Standard 50/25/15/10 Structure with 10% PF on Basic
        self.basic = SalaryComponent.objects.create(name="Basic", code="BASIC", type=SalaryComponentType.EARNING, value_type=SalaryComponentValueType.PERCENTAGE, value=Decimal('50.00'))
        self.hra = SalaryComponent.objects.create(name="HRA", code="HRA", type=SalaryComponentType.EARNING, value_type=SalaryComponentValueType.PERCENTAGE, value=Decimal('25.00'))
        self.medical = SalaryComponent.objects.create(name="Medical", code="MEDICAL", type=SalaryComponentType.EARNING, value_type=SalaryComponentValueType.PERCENTAGE, value=Decimal('15.00'))
        self.conveyance = SalaryComponent.objects.create(name="Conveyance", code="CONVEYANCE", type=SalaryComponentType.EARNING, value_type=SalaryComponentValueType.PERCENTAGE, value=Decimal('10.00'))
        self.pf = SalaryComponent.objects.create(name="PF", code="PF", type=SalaryComponentType.DEDUCTION, value_type=SalaryComponentValueType.PERCENTAGE, value=Decimal('10.00'), is_pf=True)

        self.arrear_comp = SalaryComponent.objects.create(name="Arrear", code="ARREAR", type=SalaryComponentType.EARNING, value_type=SalaryComponentValueType.FIXED, value=Decimal('0.00'))
        self.tds_comp = SalaryComponent.objects.create(name="TDS Tax", code="TDS", type=SalaryComponentType.DEDUCTION, value_type=SalaryComponentValueType.FIXED, value=Decimal('0.00'))

        self.structure = SalaryStructure.objects.create(name="Std 50/25/15/10 Structure")
        SalaryStructureComponent.objects.create(salary_structure=self.structure, salary_component=self.basic, value=Decimal('50.00'), value_type=SalaryComponentValueType.PERCENTAGE)
        SalaryStructureComponent.objects.create(salary_structure=self.structure, salary_component=self.hra, value=Decimal('25.00'), value_type=SalaryComponentValueType.PERCENTAGE)
        SalaryStructureComponent.objects.create(salary_structure=self.structure, salary_component=self.medical, value=Decimal('15.00'), value_type=SalaryComponentValueType.PERCENTAGE)
        SalaryStructureComponent.objects.create(salary_structure=self.structure, salary_component=self.conveyance, value=Decimal('10.00'), value_type=SalaryComponentValueType.PERCENTAGE)
        SalaryStructureComponent.objects.create(salary_structure=self.structure, salary_component=self.pf, value=Decimal('10.00'), value_type=SalaryComponentValueType.PERCENTAGE)

    def test_reconciliation_row_1_standard_full_attendance(self):
        """Row 1: Gross 100,000, 0 absences, 10% PF on Basic -> Deductions 5,000, Net 95,000."""
        emp = Employee.objects.create(
            employee_number="REC001", first_name="Rahim", last_name="Uddin",
            joined_date=datetime.date(2026, 1, 1), status=EmployeeStatus.ACTIVE,
            branch=self.branch, bank_name="City Bank", bank_account="110220330", payment_method="bank"
        )
        EmployeeSalaryAssignment.objects.create(employee=emp, salary_structure=self.structure, gross_salary=Decimal('100000.00'), effective_from=datetime.date(2026, 1, 1))

        run = PayrollRun.objects.create(name="Jan 2026", period_start=datetime.date(2026, 1, 1), period_end=datetime.date(2026, 1, 31))
        calc = PayrollService.run_payroll_for_employee(run, emp)

        self.assertEqual(calc.gross_salary, Decimal('100000.00'))
        self.assertEqual(calc.total_earnings, Decimal('100000.00'))
        self.assertEqual(calc.total_deductions, Decimal('5000.00'))  # 10% PF on 50k Basic = 5,000
        self.assertEqual(calc.net_payable, Decimal('95000.00'))
        self.assertEqual(calc.bank_payable, Decimal('95000.00'))
        self.assertEqual(calc.cash_payable, Decimal('0.00'))

    def test_reconciliation_row_2_absences_and_other_deduction(self):
        """Row 2: Gross 60,000, 2 unpaid absences, BDT 2,000 other deduction -> Net 51,000."""
        emp = Employee.objects.create(
            employee_number="REC002", first_name="Karim", last_name="Ahmed",
            joined_date=datetime.date(2026, 1, 1), status=EmployeeStatus.ACTIVE,
            branch=self.branch, payment_method="cash"
        )
        EmployeeSalaryAssignment.objects.create(
            employee=emp, salary_structure=self.structure, gross_salary=Decimal('60000.00'),
            effective_from=datetime.date(2026, 1, 1), payment_mode=PaymentMode.CASH
        )

        run = PayrollRun.objects.create(name="Jan 2026", period_start=datetime.date(2026, 1, 1), period_end=datetime.date(2026, 1, 31))
        calc = PayrollService.run_payroll_for_employee(
            payroll_run=run,
            employee=emp,
            unpaid_absent_days=Decimal('2'),
            other_deduction=Decimal('2000.00')
        )

        # Basic: 30,000, HRA: 15,000, Med: 9,000, Conv: 6,000
        # PF: 3,000 (10% of 30k Basic)
        # Absence Deduction: (60,000 / 30) * 2 = 4,000
        # Other Deduction: 2,000
        # Total Deductions = 3,000 + 4,000 + 2,000 = 9,000
        # Net = 60,000 - 9,000 = 51,000
        self.assertEqual(calc.absence_deduction, Decimal('4000.00'))
        self.assertEqual(calc.other_deduction, Decimal('2000.00'))
        self.assertEqual(calc.total_deductions, Decimal('9000.00'))
        self.assertEqual(calc.net_payable, Decimal('51000.00'))
        self.assertEqual(calc.cash_payable, Decimal('51000.00'))
        self.assertEqual(calc.bank_payable, Decimal('0.00'))

    def test_reconciliation_row_3_overtime_and_arrear_adjustment(self):
        """Row 3: Gross 45,000 + 10 OT hours (fixed_300 policy) + BDT 5,000 arrear + BDT 1,500 TDS deduction."""
        emp = Employee.objects.create(
            employee_number="REC003", first_name="Salma", last_name="Begum",
            joined_date=datetime.date(2026, 1, 1), status=EmployeeStatus.ACTIVE,
            branch=self.branch, payment_method="bank", overtime_policy="fixed_300"
        )
        EmployeeSalaryAssignment.objects.create(
            employee=emp, salary_structure=self.structure, gross_salary=Decimal('45000.00'),
            effective_from=datetime.date(2026, 1, 1)
        )

        run = PayrollRun.objects.create(name="Jan 2026", period_start=datetime.date(2026, 1, 1), period_end=datetime.date(2026, 1, 31))

        # Add manual adjustments
        PayrollAdjustment.objects.create(
            payroll_run=run, employee=emp, component=self.arrear_comp, amount=Decimal('5000.00'),
            type=SalaryComponentType.EARNING, reason="Previous month adjustment", created_by=self.admin
        )
        PayrollAdjustment.objects.create(
            payroll_run=run, employee=emp, component=self.tds_comp, amount=Decimal('1500.00'),
            type=SalaryComponentType.DEDUCTION, reason="TDS deduction", created_by=self.admin
        )

        calc = PayrollService.run_payroll_for_employee(
            payroll_run=run,
            employee=emp,
            ot_hours=Decimal('10.0')
        )

        # Gross: 45,000 + Arrear: 5,000 + OT: 3,000 (10 hrs * 300) = Total Earnings 53,000
        # Total Deductions: PF (2,250 - 10% of 22.5k Basic) + TDS (1,500) = 3,750
        # Net = 53,000 - 3,750 = 49,250
        self.assertEqual(calc.ot_amount, Decimal('3000.00'))
        self.assertEqual(calc.total_earnings, Decimal('53000.00'))
        self.assertEqual(calc.total_deductions, Decimal('3750.00'))
        self.assertEqual(calc.net_payable, Decimal('49250.00'))

        # Verification of OT amount = 0 when overtime_policy is 'none' / not configured
        emp.overtime_policy = 'none'
        emp.save()
        calc_no_ot = PayrollService.run_payroll_for_employee(
            payroll_run=run,
            employee=emp,
            ot_hours=Decimal('10.0')
        )
        self.assertEqual(calc_no_ot.ot_amount, Decimal('0.00'))
        self.assertEqual(calc_no_ot.total_earnings, Decimal('50000.00'))

    def test_reconciliation_row_4_split_payment_mode(self):
        """Row 4: Split payment with bank limit 50,000 on net 75,000 -> Bank 50,000, Cash 25,000."""
        emp = Employee.objects.create(
            employee_number="REC004", first_name="Tariq", last_name="Islam",
            joined_date=datetime.date(2026, 1, 1), status=EmployeeStatus.ACTIVE,
            branch=self.branch, bank_name="Dhaka Bank", bank_account="998877", payment_method="bank"
        )
        # Structure without PF for direct testing of net payable
        structure_no_pf = SalaryStructure.objects.create(name="No PF Structure")
        SalaryStructureComponent.objects.create(salary_structure=structure_no_pf, salary_component=self.basic, value=Decimal('50.00'), value_type=SalaryComponentValueType.PERCENTAGE)
        SalaryStructureComponent.objects.create(salary_structure=structure_no_pf, salary_component=self.hra, value=Decimal('50.00'), value_type=SalaryComponentValueType.PERCENTAGE)

        EmployeeSalaryAssignment.objects.create(
            employee=emp, salary_structure=structure_no_pf, gross_salary=Decimal('75000.00'),
            effective_from=datetime.date(2026, 1, 1), payment_mode=PaymentMode.SPLIT, bank_limit=Decimal('50000.00')
        )

        run = PayrollRun.objects.create(name="Jan 2026", period_start=datetime.date(2026, 1, 1), period_end=datetime.date(2026, 1, 31))
        calc = PayrollService.run_payroll_for_employee(run, emp)

        self.assertEqual(calc.net_payable, Decimal('75000.00'))
        self.assertEqual(calc.bank_payable, Decimal('50000.00'))
        self.assertEqual(calc.cash_payable, Decimal('25000.00'))

    def test_reconciliation_row_5_split_bank_limit_exceeding_net(self):
        """Row 5: Split payment with bank limit 50,000 on net 35,000 -> Bank 35,000, Cash 0."""
        emp = Employee.objects.create(
            employee_number="REC005", first_name="Nusrat", last_name="Jahan",
            joined_date=datetime.date(2026, 1, 1), status=EmployeeStatus.ACTIVE,
            branch=self.branch, payment_method="bank"
        )
        structure_no_pf = SalaryStructure.objects.filter(name="No PF Structure").first()
        if not structure_no_pf:
            structure_no_pf = SalaryStructure.objects.create(name="No PF Structure")
            SalaryStructureComponent.objects.create(salary_structure=structure_no_pf, salary_component=self.basic, value=Decimal('50.00'), value_type=SalaryComponentValueType.PERCENTAGE)
            SalaryStructureComponent.objects.create(salary_structure=structure_no_pf, salary_component=self.hra, value=Decimal('50.00'), value_type=SalaryComponentValueType.PERCENTAGE)

        EmployeeSalaryAssignment.objects.create(
            employee=emp, salary_structure=structure_no_pf, gross_salary=Decimal('35000.00'),
            effective_from=datetime.date(2026, 1, 1), payment_mode=PaymentMode.SPLIT, bank_limit=Decimal('50000.00')
        )

        run = PayrollRun.objects.create(name="Jan 2026", period_start=datetime.date(2026, 1, 1), period_end=datetime.date(2026, 1, 31))
        calc = PayrollService.run_payroll_for_employee(run, emp)

        self.assertEqual(calc.net_payable, Decimal('35000.00'))
        self.assertEqual(calc.bank_payable, Decimal('35000.00'))
        self.assertEqual(calc.cash_payable, Decimal('0.00'))

    def test_reconciliation_row_6_zero_or_negative_net_clamp(self):
        """Row 6: Heavy deductions exceeding earnings yields negative net payable without crashing."""
        emp = Employee.objects.create(
            employee_number="REC006", first_name="Farhan", last_name="Ali",
            joined_date=datetime.date(2026, 1, 1), status=EmployeeStatus.ACTIVE,
            branch=self.branch
        )
        EmployeeSalaryAssignment.objects.create(
            employee=emp, salary_structure=self.structure, gross_salary=Decimal('20000.00'),
            effective_from=datetime.date(2026, 1, 1)
        )

        run = PayrollRun.objects.create(name="Jan 2026", period_start=datetime.date(2026, 1, 1), period_end=datetime.date(2026, 1, 31))
        calc = PayrollService.run_payroll_for_employee(
            payroll_run=run,
            employee=emp,
            other_deduction=Decimal('30000.00')  # Exceeds 20,000 gross
        )

        # 20k earnings - (1k PF + 30k other ded) = -11,000
        self.assertEqual(calc.net_payable, Decimal('-11000.00'))
        self.assertEqual(calc.total_deductions, Decimal('31000.00'))

    def test_snapshot_immutability_after_salary_structure_change(self):
        """Locked January payroll cannot change when employee gets salary hike in February."""
        emp = Employee.objects.create(
            user=self.staff_a, employee_number="REC007", first_name="Jamal", last_name="Hossain",
            joined_date=datetime.date(2026, 1, 1), status=EmployeeStatus.ACTIVE, branch=self.branch
        )
        assignment_jan = EmployeeSalaryAssignment.objects.create(
            employee=emp, salary_structure=self.structure, gross_salary=Decimal('50000.00'),
            effective_from=datetime.date(2026, 1, 1)
        )

        jan_run = PayrollRun.objects.create(
            name="Jan 2026", period_start=datetime.date(2026, 1, 1),
            period_end=datetime.date(2026, 1, 31), status=PayrollRunStatus.DRAFT
        )
        calc_jan = PayrollService.run_payroll_for_employee(jan_run, emp)
        self.assertEqual(calc_jan.gross_salary, Decimal('50000.00'))

        # Lock January Run
        PayrollService.transition_payroll_status(jan_run, PayrollRunStatus.REVIEW, self.admin, "Review")
        PayrollService.transition_payroll_status(jan_run, PayrollRunStatus.APPROVED_LOCKED, self.admin, "Locked")

        # In February, employee gets promoted with Gross 80,000
        assignment_feb = EmployeeSalaryAssignment.objects.create(
            employee=emp, salary_structure=self.structure, gross_salary=Decimal('80000.00'),
            effective_from=datetime.date(2026, 2, 1)
        )

        # Recalculating January must be blocked
        with self.assertRaises(ValidationError):
            PayrollService.run_payroll_for_employee(jan_run, emp)

        # Checking January calculation record in DB remains unchanged
        # Gross 50k, Basic 25k (50%), PF = 10% of Basic = 2,500 -> Net = 47,500
        calc_jan.refresh_from_db()
        self.assertEqual(calc_jan.gross_salary, Decimal('50000.00'))
        self.assertEqual(calc_jan.net_payable, Decimal('47500.00'))

        # Run February Payroll (80k Gross, Basic 40k, PF = 4k -> Net = 76k)
        feb_run = PayrollRun.objects.create(
            name="Feb 2026", period_start=datetime.date(2026, 2, 1),
            period_end=datetime.date(2026, 2, 28), status=PayrollRunStatus.DRAFT
        )
        calc_feb = PayrollService.run_payroll_for_employee(feb_run, emp)
        self.assertEqual(calc_feb.gross_salary, Decimal('80000.00'))
        self.assertEqual(calc_feb.net_payable, Decimal('76000.00'))

        # January remains unchanged: Gross 50k, Basic 25k, PF 2,500 -> Net 47,500
        calc_jan.refresh_from_db()
        self.assertEqual(calc_jan.gross_salary, Decimal('50000.00'))
        self.assertEqual(calc_jan.net_payable, Decimal('47500.00'))

    def test_large_roster_performance_and_exports(self):
        """Simulate large roster with 100 employees and test batch calculations and exports."""
        run = PayrollRun.objects.create(
            name="Large Scale Run",
            period_start=datetime.date(2026, 8, 1),
            period_end=datetime.date(2026, 8, 31),
            status=PayrollRunStatus.DRAFT
        )

        # Bulk create 100 employees
        employees = []
        assignments = []
        for i in range(100):
            emp = Employee(
                employee_number=f"BULK_{i:03d}",
                first_name=f"Staff_{i}",
                last_name="Test",
                joined_date=datetime.date(2026, 1, 1),
                status=EmployeeStatus.ACTIVE,
                branch=self.branch,
                payment_method="bank" if i % 2 == 0 else "cash"
            )
            employees.append(emp)
        Employee.objects.bulk_create(employees)

        all_emps = Employee.objects.filter(employee_number__startswith="BULK_")
        for emp in all_emps:
            assignments.append(EmployeeSalaryAssignment(
                employee=emp,
                salary_structure=self.structure,
                gross_salary=Decimal('40000.00'),
                effective_from=datetime.date(2026, 1, 1),
                payment_mode=PaymentMode.BANK if emp.payment_method == "bank" else PaymentMode.CASH
            ))
        EmployeeSalaryAssignment.objects.bulk_create(assignments)

        # Run calculations
        for emp in all_emps:
            PayrollService.run_payroll_for_employee(run, emp)

        self.assertEqual(EmployeePayrollCalculation.objects.filter(payroll_run=run).count(), 100)

        # Test reports generation for 100 employees
        from apps.payroll.reports import (
            export_payroll_register_excel,
            export_payroll_register_csv,
            export_payroll_register_pdf,
            export_bank_report_excel,
            export_cash_report_excel
        )
        calcs = list(EmployeePayrollCalculation.objects.filter(payroll_run=run).select_related('employee', 'employee__department', 'employee__designation'))

        excel_bytes = export_payroll_register_excel(run, calcs)
        self.assertTrue(len(excel_bytes) > 5000)

        csv_bytes = export_payroll_register_csv(run, calcs)
        self.assertTrue(len(csv_bytes) > 2000)

        pdf_bytes = export_payroll_register_pdf(run, calcs)
        self.assertTrue(len(pdf_bytes) > 5000)

        bank_calcs = [c for c in calcs if c.bank_payable > 0]
        self.assertEqual(len(bank_calcs), 50)
        bank_excel = export_bank_report_excel(run, bank_calcs)
        self.assertTrue(len(bank_excel) > 3000)

        cash_calcs = [c for c in calcs if c.cash_payable > 0]
        self.assertEqual(len(cash_calcs), 50)
        cash_excel = export_cash_report_excel(run, cash_calcs)
        self.assertTrue(len(cash_excel) > 3000)


from django.test import Client
from apps.accounts.models import CustomUser

class PayrollUITests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin_user = CustomUser.objects.create_superuser(
            email="admin@test.com",
            password="adminpassword"
        )
        self.employee_user = CustomUser.objects.create_user(
            email="emp@test.com",
            phone="01711111111",
            password="emppassword",
            role="staff"
        )
        self.branch = Branch.objects.create(
            name="Test Branch",
            latitude=Decimal("23.00"),
            longitude=90.00
        )
        self.employee = Employee.objects.create(
            employee_number="EMP-TEST-01",
            first_name="Test",
            last_name="Employee",
            status=EmployeeStatus.ACTIVE,
            branch=self.branch,
            user=self.employee_user
        )
        self.basic_comp = SalaryComponent.objects.create(
            name="Basic",
            code="BASIC",
            type=SalaryComponentType.EARNING,
            value_type=SalaryComponentValueType.PERCENTAGE,
            value=Decimal("50.00")
        )
        self.structure = SalaryStructure.objects.create(
            name="Test Structure",
            is_active=True
        )
        SalaryStructureComponent.objects.create(
            salary_structure=self.structure,
            salary_component=self.basic_comp,
            value=Decimal("100.00"),
            value_type=SalaryComponentValueType.PERCENTAGE
        )

    def test_create_payroll_run_view(self):
        self.client.login(email="admin@test.com", password="adminpassword")
        response = self.client.post("/payroll/runs/create/", {
            "month": "8",
            "year": "2026",
            "name": "August 2026 Run"
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(PayrollRun.objects.filter(name="August 2026 Run").exists())

    def test_salary_component_crud(self):
        self.client.login(email="admin@test.com", password="adminpassword")
        
        # Create
        response = self.client.post("/payroll/components/create/", {
            "name": "House Rent",
            "code": "HRA",
            "type": "earning",
            "value_type": "percentage",
            "value": "25.00"
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(SalaryComponent.objects.filter(code="HRA").exists())

        # Update
        hra = SalaryComponent.objects.get(code="HRA")
        response = self.client.post(f"/payroll/components/{hra.pk}/edit/", {
            "name": "House Rent Updated",
            "type": "earning",
            "value_type": "percentage",
            "value": "30.00"
        })
        self.assertEqual(response.status_code, 302)
        hra.refresh_from_db()
        self.assertEqual(hra.name, "House Rent Updated")

        # Delete
        response = self.client.post(f"/payroll/components/{hra.pk}/delete/")
        self.assertEqual(response.status_code, 302)
        self.assertFalse(SalaryComponent.objects.filter(code="HRA").exists())

    def test_salary_structure_validation(self):
        self.client.login(email="admin@test.com", password="adminpassword")
        
        # Invalid percentage total (not 100%)
        response = self.client.post("/payroll/structures/create/", {
            "name": "Invalid Structure",
            "component_ids": [self.basic_comp.pk],
            "component_values": ["90.00"],
            "component_value_types": ["percentage"]
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(SalaryStructure.objects.filter(name="Invalid Structure").exists())

        # Valid percentage total (100%)
        response = self.client.post("/payroll/structures/create/", {
            "name": "Valid Structure",
            "component_ids": [self.basic_comp.pk],
            "component_values": ["100.00"],
            "component_value_types": ["percentage"]
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(SalaryStructure.objects.filter(name="Valid Structure").exists())

    def test_salary_structure_create_and_edit_page_views(self):
        self.client.login(email="admin@test.com", password="adminpassword")

        # GET Create Page
        response = self.client.get("/payroll/structures/create/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "payroll/salary_structure_form.html")
        self.assertIn("components", response.context)
        self.assertFalse(response.context["is_edit"])
        self.assertContains(response, "Create New Salary Structure")
        self.assertContains(response, "Live Salary Simulator")
        self.assertContains(response, "Earnings Allocation")
        self.assertContains(response, "Apply Preset")

        # GET Edit Page
        response = self.client.get(f"/payroll/structures/{self.structure.pk}/edit/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "payroll/salary_structure_form.html")
        self.assertEqual(response.context["structure"], self.structure)
        self.assertTrue(response.context["is_edit"])
        self.assertContains(response, "Edit Salary Structure:")
        self.assertContains(response, "Test Structure")

        # POST Edit Page
        response = self.client.post(f"/payroll/structures/{self.structure.pk}/edit/", {
            "name": "Updated Structure Name",
            "is_active": "on",
            "component_ids": [self.basic_comp.pk],
            "component_values": ["100.00"],
            "component_value_types": ["percentage"]
        })
        self.assertEqual(response.status_code, 302)
        self.structure.refresh_from_db()
        self.assertEqual(self.structure.name, "Updated Structure Name")

    def test_employee_salary_assignment(self):
        self.client.login(email="admin@test.com", password="adminpassword")
        response = self.client.post("/payroll/setup/assign/", {
            "employee_id": self.employee.pk,
            "salary_structure_id": self.structure.pk,
            "gross_salary": "60000.00",
            "effective_from": "2026-08-01",
            "effective_to": "2026-08-31",
            "payment_mode": "bank",
            "bank_limit": "0.00"
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(EmployeeSalaryAssignment.objects.filter(employee=self.employee).exists())

        # Overlapping assignment should fail
        response2 = self.client.post("/payroll/setup/assign/", {
            "employee_id": self.employee.pk,
            "salary_structure_id": self.structure.pk,
            "gross_salary": "70000.00",
            "effective_from": "2026-08-15",
            "effective_to": "2026-09-15",
            "payment_mode": "bank",
            "bank_limit": "0.00"
        })
        self.assertEqual(response2.status_code, 302)
        self.assertEqual(EmployeeSalaryAssignment.objects.filter(employee=self.employee).count(), 1)

    def test_missing_salary_warning(self):
        run = PayrollRun.objects.create(
            name="August 2026 Warning Test",
            period_start=datetime.date(2026, 8, 1),
            period_end=datetime.date(2026, 8, 31)
        )
        self.client.login(email="admin@test.com", password="adminpassword")
        response = self.client.get(f"/payroll/runs/{run.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "lack active Salary Assignments")

    def test_locked_state_action_blocking(self):
        run = PayrollRun.objects.create(
            name="August 2026 Locked Test",
            period_start=datetime.date(2026, 8, 1),
            period_end=datetime.date(2026, 8, 31),
            status=PayrollRunStatus.APPROVED_LOCKED
        )
        self.client.login(email="admin@test.com", password="adminpassword")
        
        # Syncing a locked run should fail or do nothing (service/view enforcement)
        response = self.client.post(f"/payroll/runs/{run.pk}/sync/")
        self.assertEqual(response.status_code, 302)
        
        # Adjustments should not allow edits in locked run (usually adjustment delete/add views fail)
        response2 = self.client.post(f"/payroll/runs/{run.pk}/adjustments/add/", {
            "employee_id": self.employee.pk,
            "component_id": self.basic_comp.pk,
            "amount": "1000.00",
            "reason": "Test adjustment"
        })
        self.assertIn(response2.status_code, [400, 403])  # or equivalent error status

    def test_payslip_access_rbac(self):
        # Create calculation for employee
        run = PayrollRun.objects.create(
            name="August 2026 Lock Test",
            period_start=datetime.date(2026, 8, 1),
            period_end=datetime.date(2026, 8, 31),
            status=PayrollRunStatus.APPROVED_LOCKED
        )
        calc = EmployeePayrollCalculation.objects.create(
            payroll_run=run,
            employee=self.employee,
            gross_salary=Decimal("50000.00"),
            payment_mode="bank",
            total_earnings=Decimal("50000.00"),
            total_deductions=Decimal("0.00"),
            net_payable=Decimal("50000.00"),
            bank_payable=Decimal("50000.00"),
            cash_payable=Decimal("0.00"),
            structure_snapshot={}
        )

        # Other employee user
        other_employee_user = CustomUser.objects.create_user(
            email="other@test.com",
            phone="01711111112",
            password="otherpassword",
            role="staff"
        )
        
        # Login other employee
        self.client.login(email="other@test.com", password="otherpassword")
        # Try to view calculation detail/payslip of the first employee
        response = self.client.get(f"/payroll/payslips/{calc.pk}/")
        self.assertEqual(response.status_code, 403)

    def test_absence_divisor_zero_division_guard(self):
        result = PayrollCalculationEngine.calculate_employee_payroll(
            gross_salary=Decimal("60000.00"),
            structure_components_list=[{
                'code': 'BASIC',
                'name': 'Basic Salary',
                'type': SalaryComponentType.EARNING,
                'value_type': SalaryComponentValueType.PERCENTAGE,
                'value': Decimal('100.00'),
                'is_pf': False,
            }],
            unpaid_absent_days=Decimal("2.00"),
            absence_divisor=0,
        )
        self.assertEqual(result["total_deductions"], Decimal("4000.0000"))
        self.assertEqual(result["net_payable"], Decimal("56000.0000"))


class PayrollConfigurationCenterTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        from django.contrib.auth import get_user_model
        from apps.tenants.models import Tenant, TenantMembership

        cache.clear()
        User = get_user_model()

        # Setup two distinct tenants
        self.tenant_a = Tenant.objects.create(name="Tenant Alpha", slug="tenant-alpha")
        self.tenant_b = Tenant.objects.create(name="Tenant Beta", slug="tenant-beta")

        # Setup users
        self.admin_user_a = User.objects.create_user(
            email="admin.alpha@example.com",
            password="password123",
            role="admin"
        )
        TenantMembership.objects.create(tenant=self.tenant_a, user=self.admin_user_a, is_active=True)

        self.staff_user_a = User.objects.create_user(
            email="staff.alpha@example.com",
            password="password123",
            role="staff"
        )
        TenantMembership.objects.create(tenant=self.tenant_a, user=self.staff_user_a, is_active=True)

        self.user_b = User.objects.create_user(
            email="user.beta@example.com",
            password="password123",
            role="admin"
        )
        TenantMembership.objects.create(tenant=self.tenant_b, user=self.user_b, is_active=True)

        self.superuser = User.objects.create_superuser(
            email="superadmin@example.com",
            password="password123"
        )

    def test_default_configuration_initialization_and_isolation(self):
        """Verify default v1 configuration is initialized per-tenant and isolated."""
        from apps.payroll.configuration_service import PayrollConfigurationService
        from apps.payroll.models import AbsenceDivisorMode

        config_a = PayrollConfigurationService.get_active_config(self.tenant_a)
        self.assertIsNotNone(config_a)
        self.assertEqual(config_a.tenant, self.tenant_a)
        self.assertEqual(config_a.version, 1)
        self.assertTrue(config_a.is_active)
        self.assertFalse(config_a.is_archived)
        self.assertEqual(config_a.currency, "BDT")
        self.assertEqual(config_a.cutoff_day, 25)
        self.assertEqual(config_a.payment_day, 30)
        self.assertEqual(config_a.working_day_basis, AbsenceDivisorMode.FIXED_30)

        # Tenant B configuration is separate
        config_b = PayrollConfigurationService.get_active_config(self.tenant_b)
        self.assertIsNotNone(config_b)
        self.assertEqual(config_b.tenant, self.tenant_b)
        self.assertNotEqual(config_a.id, config_b.id)

    def test_inplace_update_when_no_payroll_runs_exist(self):
        """When not referenced by any payroll run, changes update the active config in-place."""
        from apps.payroll.configuration_service import PayrollConfigurationService
        from apps.payroll.models import AbsenceDivisorMode

        config_a = PayrollConfigurationService.get_active_config(self.tenant_a)
        initial_id = config_a.id

        updated_config, is_new = PayrollConfigurationService.save_or_update_config(
            tenant=self.tenant_a,
            user=self.admin_user_a,
            data={
                'currency': 'USD',
                'cutoff_day': 20,
                'working_day_basis': AbsenceDivisorMode.CALENDAR_DAYS,
            }
        )

        self.assertFalse(is_new)
        self.assertEqual(updated_config.id, initial_id)
        self.assertEqual(updated_config.version, 1)
        self.assertEqual(updated_config.currency, 'USD')
        self.assertEqual(updated_config.cutoff_day, 20)
        self.assertEqual(updated_config.working_day_basis, AbsenceDivisorMode.CALENDAR_DAYS)

        # Check cached lookup returns updated values
        active = PayrollConfigurationService.get_active_config(self.tenant_a)
        self.assertEqual(active.currency, 'USD')

    def test_auto_versioning_safeguard_when_referenced_by_payroll_run(self):
        """
        When active configuration is referenced by a payroll run, modifications
        must spawn a new incremented version (v2), archiving v1 without touching the run.
        """
        from apps.payroll.configuration_service import PayrollConfigurationService
        from apps.payroll.models import AbsenceDivisorMode

        config_v1 = PayrollConfigurationService.get_active_config(self.tenant_a)
        
        # Attach to a payroll run
        run = PayrollRun.objects.create(
            name="August 2026",
            period_start=datetime.date(2026, 8, 1),
            period_end=datetime.date(2026, 8, 31),
            status=PayrollRunStatus.APPROVED_LOCKED
        )
        PayrollConfigurationService.attach_to_payroll_run(run, config=config_v1)

        self.assertEqual(run.configuration_id, config_v1.id)
        self.assertEqual(run.configuration_snapshot['version'], 1)
        self.assertEqual(run.configuration_snapshot['cutoff_day'], 25)

        # Now update configuration as admin
        config_v2, is_new = PayrollConfigurationService.save_or_update_config(
            tenant=self.tenant_a,
            user=self.admin_user_a,
            data={
                'currency': 'BDT',
                'cutoff_day': 22,
                'overtime_multiplier': Decimal('2.00'),
                'working_day_basis': AbsenceDivisorMode.WORKING_DAYS
            }
        )

        self.assertTrue(is_new)
        self.assertEqual(config_v2.version, 2)
        self.assertTrue(config_v2.is_active)
        self.assertFalse(config_v2.is_archived)
        self.assertEqual(config_v2.cutoff_day, 22)
        self.assertEqual(config_v2.overtime_multiplier, Decimal('2.00'))

        # Verify previous version is archived
        config_v1.refresh_from_db()
        self.assertFalse(config_v1.is_active)
        self.assertTrue(config_v1.is_archived)
        self.assertIsNotNone(config_v1.archived_at)
        self.assertEqual(config_v1.archived_by, self.admin_user_a)

        # Verify historical payroll run retains immutable snapshot of v1
        run.refresh_from_db()
        self.assertEqual(run.configuration_id, config_v1.id)
        self.assertEqual(run.configuration_snapshot['version'], 1)
        self.assertEqual(run.configuration_snapshot['cutoff_day'], 25)

    def test_hard_delete_protection(self):
        """Configuration referenced by a payroll run cannot be hard-deleted."""
        from apps.payroll.configuration_service import PayrollConfigurationService

        config = PayrollConfigurationService.get_active_config(self.tenant_a)
        run = PayrollRun.objects.create(
            name="September 2026",
            period_start=datetime.date(2026, 9, 1),
            period_end=datetime.date(2026, 9, 30),
        )
        PayrollConfigurationService.attach_to_payroll_run(run, config=config)

        with self.assertRaises(ValidationError):
            config.delete()

    def test_ai_permission_separation_and_default_denied(self):
        """
        Verify AI capabilities are separated (read, simulate, propose, approve, execute)
        and default-denied. Autonomous distribution is always blocked.
        """
        from apps.payroll.ai_permissions import AIPayrollPermissionService

        # Staff user lacks configuration permissions by default
        self.assertFalse(
            AIPayrollPermissionService.check_permission(
                self.staff_user_a,
                self.tenant_a,
                AIPayrollPermissionService.PERM_READ
            )
        )
        self.assertFalse(
            AIPayrollPermissionService.check_permission(
                self.staff_user_a,
                self.tenant_a,
                AIPayrollPermissionService.PERM_SIMULATE
            )
        )

        # Admin user possesses read, simulate, and propose
        self.assertTrue(
            AIPayrollPermissionService.check_permission(
                self.admin_user_a,
                self.tenant_a,
                AIPayrollPermissionService.PERM_READ
            )
        )
        self.assertTrue(
            AIPayrollPermissionService.check_permission(
                self.admin_user_a,
                self.tenant_a,
                AIPayrollPermissionService.PERM_SIMULATE
            )
        )

        # Autonomous distribution is strictly blocked even for superusers
        self.assertFalse(
            AIPayrollPermissionService.check_permission(
                self.superuser,
                self.tenant_a,
                'payroll.distribute_salary'
            )
        )

    def test_forged_tenant_access_rejection_and_security_audit(self):
        """Attempting cross-tenant access triggers permission denial and security audit log."""
        from apps.payroll.ai_permissions import AIPayrollPermissionService
        from apps.audit.models import AuditEvent
        from django.core.exceptions import PermissionDenied

        initial_audit_count = AuditEvent.objects.filter(action='forged_tenant_access_attempt').count()

        # User A tries to simulate on Tenant B
        with self.assertRaises(PermissionDenied):
            AIPayrollPermissionService.assert_permission(
                user=self.admin_user_a,
                target_tenant=self.tenant_b,
                action=AIPayrollPermissionService.PERM_SIMULATE,
                request_tenant=self.tenant_a  # Forged/mismatched context
            )

        new_audit_count = AuditEvent.objects.filter(action='forged_tenant_access_attempt').count()
        self.assertGreater(new_audit_count, initial_audit_count)

    def test_payroll_configuration_view_authorization_and_render(self):
        """Test GET /payroll/configuration/ renders Cotton UI for authorized user."""
        from django.urls import reverse

        self.client.force_login(self.admin_user_a)
        url = reverse('payroll:payroll_configuration')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Payroll Configuration Center")
        self.assertContains(response, "Tenant Alpha")
        self.assertContains(response, "v1")

    def test_ai_simulation_endpoint_zero_side_effects(self):
        """POST /payroll/configuration/simulate/ executes calculation without database changes."""
        from django.urls import reverse
        from apps.payroll.models import PayrollConfiguration

        self.client.force_login(self.admin_user_a)
        url = reverse('payroll:payroll_configuration_simulate')

        initial_config_count = PayrollConfiguration.objects.count()
        initial_run_count = PayrollRun.objects.count()

        response = self.client.post(url, data={}, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('summary', data)
        self.assertIn('total_gross', data['summary'])
        self.assertTrue(data.get('is_simulation'))

        # DB must have zero new payroll runs or configs
        self.assertEqual(PayrollConfiguration.objects.count(), initial_config_count)
        self.assertEqual(PayrollRun.objects.count(), initial_run_count)

    def test_global_search_registration_and_filtering(self):
        """Verify Payroll Configuration Center appears in Global Search for authorized users."""
        from apps.accounts.search_service import GlobalSearchService

        items_admin = GlobalSearchService.get_navigation_catalog(self.admin_user_a)
        payroll_config_items = [i for i in items_admin if i['label'] == 'Payroll Configuration Center']
        self.assertEqual(len(payroll_config_items), 1)
        self.assertEqual(payroll_config_items[0]['href'], '/payroll/configuration/')

        # Staff user should not see Payroll Configuration Center
        items_staff = GlobalSearchService.get_navigation_catalog(self.staff_user_a)
        staff_config_items = [i for i in items_staff if i['label'] == 'Payroll Configuration Center']
        self.assertEqual(len(staff_config_items), 0)


class PayrollPaymentDestinationTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from apps.tenants.models import Tenant, TenantMembership
        from apps.branches.models import Branch
        from apps.employees.models import Employee, EmployeeStatus
        from apps.payroll.models import (
            SalaryComponent, SalaryComponentType, SalaryComponentValueType,
            SalaryStructure, SalaryStructureComponent, EmployeeSalaryAssignment
        )

        User = get_user_model()
        self.tenant_a = Tenant.objects.create(name="Destination Corp A", slug="dest-corp-a")
        self.tenant_b = Tenant.objects.create(name="Destination Corp B", slug="dest-corp-b")

        self.admin_user_a = User.objects.create_user(
            email="dest.admin.a@example.com",
            password="password123",
            role="admin"
        )
        TenantMembership.objects.create(tenant=self.tenant_a, user=self.admin_user_a, is_active=True)

        self.staff_user_a = User.objects.create_user(
            email="dest.staff.a@example.com",
            password="password123",
            role="staff"
        )
        TenantMembership.objects.create(tenant=self.tenant_a, user=self.staff_user_a, is_active=True)

        self.admin_user_b = User.objects.create_user(
            email="dest.admin.b@example.com",
            password="password123",
            role="admin"
        )
        TenantMembership.objects.create(tenant=self.tenant_b, user=self.admin_user_b, is_active=True)

        self.branch_a = Branch.objects.create(
            name="Branch A",
            address="Gulshan, Dhaka",
            latitude=Decimal('23.8103'),
            longitude=Decimal('90.4125')
        )
        self.branch_b = Branch.objects.create(
            name="Branch B",
            address="Agrabad, Chittagong",
            latitude=Decimal('22.3569'),
            longitude=Decimal('91.7832')
        )

        self.emp_user_a = User.objects.create_user(
            email="dest.emp.a@example.com",
            password="password123",
            role="staff"
        )
        TenantMembership.objects.create(tenant=self.tenant_a, user=self.emp_user_a, is_active=True)

        self.emp_user_b = User.objects.create_user(
            email="dest.emp.b@example.com",
            password="password123",
            role="staff"
        )
        TenantMembership.objects.create(tenant=self.tenant_b, user=self.emp_user_b, is_active=True)

        self.employee_a = Employee.objects.create(
            employee_number="EMP-DEST-A1",
            first_name="Rahim",
            last_name="Uddin",
            joined_date=datetime.date(2026, 1, 1),
            status=EmployeeStatus.ACTIVE,
            branch=self.branch_a,
            user=self.emp_user_a
        )
        self.employee_b = Employee.objects.create(
            employee_number="EMP-DEST-B1",
            first_name="Karim",
            last_name="Chowdhury",
            joined_date=datetime.date(2026, 1, 1),
            status=EmployeeStatus.ACTIVE,
            branch=self.branch_b,
            user=self.emp_user_b
        )

        # Setup basic structure for payroll run
        basic = SalaryComponent.objects.create(
            name="Basic Pay",
            code="BASIC_D",
            type=SalaryComponentType.EARNING,
            value_type=SalaryComponentValueType.PERCENTAGE,
            value=Decimal('100.00')
        )
        structure = SalaryStructure.objects.create(name="Dest Test Structure")
        SalaryStructureComponent.objects.create(salary_structure=structure, salary_component=basic, value=Decimal('100.00'), value_type=SalaryComponentValueType.PERCENTAGE)
        EmployeeSalaryAssignment.objects.create(
            employee=self.employee_a,
            salary_structure=structure,
            gross_salary=Decimal('50000.00'),
            effective_from=datetime.date(2026, 1, 1)
        )

    def test_bank_destination_creation_and_masking(self):
        from apps.payroll.payment_destination_service import PayrollPaymentDestinationService
        from apps.payroll.models import PaymentType, PayrollPaymentDestination

        dest = PayrollPaymentDestinationService.save_destination(
            tenant=self.tenant_a,
            employee=self.employee_a,
            payment_type=PaymentType.BANK,
            user=self.admin_user_a,
            bank_name="BRAC Bank PLC",
            account_holder="Rahim Uddin",
            account_number="1501203456789001",
            branch_name="Gulshan Branch",
            routing_number="060261358"
        )
        self.assertEqual(dest.payment_type, PaymentType.BANK)
        self.assertEqual(dest.bank_name, "BRAC Bank PLC")
        self.assertEqual(dest.account_number, "1501203456789001")
        self.assertEqual(dest.masked_account, "••••9001")

        # Check masked dictionary
        masked = PayrollPaymentDestinationService.get_masked_destination(self.employee_a)
        self.assertEqual(masked['payment_type'], PaymentType.BANK)
        self.assertEqual(masked['masked_account'], "••••9001")
        self.assertNotIn("1501203456789001", str(masked.values()))

    def test_mfs_destination_validation_and_creation(self):
        from apps.payroll.payment_destination_service import PayrollPaymentDestinationService
        from apps.payroll.models import PaymentType, MFSProvider

        # Invalid wallet number should raise ValidationError
        with self.assertRaises(ValidationError):
            PayrollPaymentDestinationService.save_destination(
                tenant=self.tenant_a,
                employee=self.employee_a,
                payment_type=PaymentType.MFS,
                user=self.admin_user_a,
                mfs_provider=MFSProvider.BKASH,
                mfs_wallet_number="12345"  # invalid
            )

        with self.assertRaises(ValidationError):
            PayrollPaymentDestinationService.save_destination(
                tenant=self.tenant_a,
                employee=self.employee_a,
                payment_type=PaymentType.MFS,
                user=self.admin_user_a,
                mfs_provider=MFSProvider.BKASH,
                mfs_wallet_number="01212345678"  # 012 is invalid in BD
            )

        # Valid wallet number succeeds
        dest = PayrollPaymentDestinationService.save_destination(
            tenant=self.tenant_a,
            employee=self.employee_a,
            payment_type=PaymentType.MFS,
            user=self.admin_user_a,
            mfs_provider=MFSProvider.BKASH,
            mfs_wallet_number="01712345678"
        )
        self.assertEqual(dest.payment_type, PaymentType.MFS)
        self.assertEqual(dest.mfs_provider, MFSProvider.BKASH)
        self.assertEqual(dest.mfs_wallet_number, "01712345678")
        self.assertEqual(dest.masked_account, "••••5678")

    def test_atomic_clearing_of_obsolete_fields_on_type_switch(self):
        """Switching from Bank to Cash or MFS must atomically clear obsolete destination data."""
        from apps.payroll.payment_destination_service import PayrollPaymentDestinationService
        from apps.payroll.models import PaymentType, MFSProvider

        # 1. Setup Bank
        dest = PayrollPaymentDestinationService.save_destination(
            tenant=self.tenant_a,
            employee=self.employee_a,
            payment_type=PaymentType.BANK,
            user=self.admin_user_a,
            bank_name="Dutch-Bangla Bank",
            account_holder="Rahim Uddin",
            account_number="1234567890123",
            branch_name="Motijheel",
            routing_number="090261111"
        )
        self.assertEqual(dest.payment_type, PaymentType.BANK)
        self.assertEqual(dest.account_number, "1234567890123")

        # 2. Switch to Cash -> bank fields MUST be completely cleared
        cash_dest = PayrollPaymentDestinationService.save_destination(
            tenant=self.tenant_a,
            employee=self.employee_a,
            payment_type=PaymentType.CASH,
            user=self.admin_user_a
        )
        self.assertEqual(cash_dest.payment_type, PaymentType.CASH)
        self.assertEqual(cash_dest.bank_name, "")
        self.assertEqual(cash_dest.account_holder, "")
        self.assertEqual(cash_dest.account_number, "")
        self.assertEqual(cash_dest.branch_name, "")
        self.assertEqual(cash_dest.routing_number, "")
        self.assertEqual(cash_dest.mfs_provider, "")
        self.assertEqual(cash_dest.mfs_wallet_number, "")
        self.assertEqual(cash_dest.masked_account, "")

        # 3. Switch to MFS -> wallet set, bank empty
        mfs_dest = PayrollPaymentDestinationService.save_destination(
            tenant=self.tenant_a,
            employee=self.employee_a,
            payment_type=PaymentType.MFS,
            user=self.admin_user_a,
            mfs_provider=MFSProvider.NAGAD,
            mfs_wallet_number="01812345678"
        )
        self.assertEqual(mfs_dest.payment_type, PaymentType.MFS)
        self.assertEqual(mfs_dest.mfs_provider, MFSProvider.NAGAD)
        self.assertEqual(mfs_dest.mfs_wallet_number, "01812345678")
        self.assertEqual(mfs_dest.bank_name, "")
        self.assertEqual(mfs_dest.account_number, "")

        # 4. Switch back to Bank -> MFS fields cleared
        bank_dest2 = PayrollPaymentDestinationService.save_destination(
            tenant=self.tenant_a,
            employee=self.employee_a,
            payment_type=PaymentType.BANK,
            user=self.admin_user_a,
            bank_name="City Bank",
            account_holder="Rahim Uddin",
            account_number="987654321000",
            branch_name="Dhanmondi"
        )
        self.assertEqual(bank_dest2.payment_type, PaymentType.BANK)
        self.assertEqual(bank_dest2.mfs_provider, "")
        self.assertEqual(bank_dest2.mfs_wallet_number, "")
        self.assertEqual(bank_dest2.account_number, "987654321000")

    def test_tenant_boundary_isolation_rejection(self):
        """Cross-tenant assignment or access must be strictly rejected."""
        from apps.payroll.payment_destination_service import PayrollPaymentDestinationService
        from apps.payroll.models import PaymentType

        # Attempt to save destination for employee_b (tenant B) under tenant A context
        with self.assertRaises(ValidationError):
            PayrollPaymentDestinationService.save_destination(
                tenant=self.tenant_a,
                employee=self.employee_b,  # Belongs to tenant B!
                payment_type=PaymentType.CASH,
                user=self.admin_user_a
            )

    def test_historical_calculation_immutability_snapshot(self):
        """Payroll calculation saves immutable snapshot; later employee edits don't change past snapshot."""
        from apps.payroll.payment_destination_service import PayrollPaymentDestinationService
        from apps.payroll.models import PaymentType, PayrollRun, PayrollRunStatus, EmployeePayrollCalculation
        from apps.payroll.services import PayrollService

        # 1. Set initial destination as Bank
        PayrollPaymentDestinationService.save_destination(
            tenant=self.tenant_a,
            employee=self.employee_a,
            payment_type=PaymentType.BANK,
            user=self.admin_user_a,
            bank_name="Eastern Bank PLC",
            account_holder="Rahim Uddin",
            account_number="100200300400",
            branch_name="Principal Branch"
        )

        # 2. Run payroll for this employee
        payroll_run = PayrollRun.objects.create(
            name="January 2026 Payroll",
            period_start=datetime.date(2026, 1, 1),
            period_end=datetime.date(2026, 1, 31),
            status=PayrollRunStatus.DRAFT
        )
        calc = PayrollService.run_payroll_for_employee(
            payroll_run=payroll_run,
            employee=self.employee_a
        )

        # Verify snapshot captured bank
        self.assertIsNotNone(calc.payment_snapshot)
        self.assertEqual(calc.payment_snapshot.get('payment_type'), PaymentType.BANK)
        self.assertEqual(calc.payment_snapshot.get('bank_name'), "Eastern Bank PLC")
        self.assertEqual(calc.payment_snapshot.get('masked_account'), "••••0400")

        # 3. Employee changes destination to Cash afterwards
        PayrollPaymentDestinationService.save_destination(
            tenant=self.tenant_a,
            employee=self.employee_a,
            payment_type=PaymentType.CASH,
            user=self.admin_user_a
        )

        # 4. Reload historical calculation: snapshot MUST remain unchanged!
        calc.refresh_from_db()
        self.assertEqual(calc.payment_snapshot.get('payment_type'), PaymentType.BANK)
        self.assertEqual(calc.payment_snapshot.get('bank_name'), "Eastern Bank PLC")
        self.assertEqual(calc.payment_snapshot.get('masked_account'), "••••0400")

    def test_ai_permission_boundaries_and_masking(self):
        """AI may read only masked destinations and cannot execute autonomous disbursements."""
        from apps.payroll.payment_destination_service import PayrollPaymentDestinationService
        from apps.payroll.models import PaymentType
        from apps.payroll.ai_permissions import (
            get_masked_destination_for_ai,
            prepare_payment_action,
            AIPermissionRegistry,
            PERM_DESTINATION_READ,
            PERM_DESTINATION_PREPARE
        )

        PayrollPaymentDestinationService.save_destination(
            tenant=self.tenant_a,
            employee=self.employee_a,
            payment_type=PaymentType.BANK,
            user=self.admin_user_a,
            bank_name="Trust Bank Ltd",
            account_holder="Rahim Uddin",
            account_number="888877776666",
            branch_name="Gulshan"
        )

        # 1. Staff user without AI read perm is denied
        denied_read = get_masked_destination_for_ai(self.staff_user_a, self.employee_a)
        self.assertFalse(denied_read['allowed'])

        # 2. Admin user with AI read perm receives masked destination only
        allowed_read = get_masked_destination_for_ai(self.admin_user_a, self.employee_a)
        self.assertTrue(allowed_read['allowed'])
        dest_data = allowed_read['destination']
        self.assertEqual(dest_data['masked_account'], "••••6666")
        self.assertNotIn("888877776666", str(dest_data.values()))

        # 3. AI prepare payment action creates draft and explicitly prohibits autonomous disbursement
        action_res = prepare_payment_action(
            user=self.admin_user_a,
            employee=self.employee_a,
            amount=Decimal('50000.00'),
            notes="January Salary Prep"
        )
        self.assertTrue(action_res['prepared'])
        self.assertEqual(action_res['action_type'], 'payroll_disbursement_draft')
        self.assertFalse(action_res['can_disburse_autonomously'])
        self.assertTrue(action_res['requires_human_approval'])

    def test_global_search_raw_account_probe_protection(self):
        """Raw account number probe queries in Global Search must be rejected."""
        from apps.accounts.search_service import GlobalSearchService

        # Search with normal query
        normal_res = GlobalSearchService.search(self.admin_user_a, "Payment")
        self.assertTrue(any("Payment" in item.get('label', '') for item in normal_res))

        # Search with raw account number (8+ digits probe)
        probe_res = GlobalSearchService.search(self.admin_user_a, "1501203456789001")
        self.assertEqual(probe_res, [])

    def test_audit_event_redaction(self):
        """Audit events must not record raw account or wallet numbers."""
        from apps.notifications.models import AuditLog
        from apps.audit.models import AuditEvent
        from apps.payroll.payment_destination_service import PayrollPaymentDestinationService
        from apps.payroll.models import PaymentType

        raw_acc = "9999888877771234"
        PayrollPaymentDestinationService.save_destination(
            tenant=self.tenant_a,
            employee=self.employee_a,
            payment_type=PaymentType.BANK,
            user=self.admin_user_a,
            bank_name="Audit Test Bank",
            account_holder="Rahim Uddin",
            account_number=raw_acc,
            branch_name="Main"
        )

        recent_audits = AuditLog.objects.filter(
            action="payroll_payment_destination_updated"
        ).order_by('-timestamp')[:5]
        self.assertTrue(recent_audits.exists())
        for audit in recent_audits:
            audit_str = f"{audit.summary} {audit.metadata}"
            self.assertNotIn(raw_acc, audit_str)
            self.assertIn("••••1234", audit_str)

        recent_platform = AuditEvent.objects.filter(
            action="payroll_payment_destination_updated"
        ).order_by('-created_at')[:5]
        self.assertTrue(recent_platform.exists())
        for ev in recent_platform:
            ev_str = f"{ev.reason_note} {ev.after_data}"
            self.assertNotIn(raw_acc, ev_str)
            self.assertIn("••••1234", ev_str)

    def test_payment_destinations_views(self):
        """Verify list view and update view authorization, Cotton rendering, and POST submission."""
        from django.urls import reverse
        from apps.payroll.models import PaymentType, MFSProvider

        self.client.force_login(self.admin_user_a)

        # 1. List view
        list_url = reverse('payroll:payment_destinations')
        res_list = self.client.get(list_url)
        self.assertEqual(res_list.status_code, 200)
        self.assertContains(res_list, "Payroll Payment Destinations")
        self.assertContains(res_list, self.employee_a.first_name)

        # 2. Edit view GET
        edit_url = reverse('payroll:payment_destination_edit', args=[self.employee_a.id])
        res_edit = self.client.get(edit_url)
        self.assertEqual(res_edit.status_code, 200)
        self.assertContains(res_edit, "Payment Destination")
        self.assertContains(res_edit, self.employee_a.get_full_name())

        # 3. Edit view POST: change to MFS
        post_data = {
            'payment_type': 'mfs',
            'mfs_provider': 'bkash',
            'wallet_number': '01912345678',
        }
        res_post = self.client.post(edit_url, post_data)
        self.assertEqual(res_post.status_code, 302)

        # Check DB destination updated
        dest = self.employee_a.payment_destination
        self.assertEqual(dest.payment_type, PaymentType.MFS)
        self.assertEqual(dest.mfs_provider, MFSProvider.BKASH)
        self.assertEqual(dest.get_wallet_number(), '01912345678')

    def test_form_validation(self):
        """Form enforces conditional validation rules across Bank, Cash, and MFS."""
        from apps.payroll.forms import PayrollPaymentDestinationForm

        # Bank without account number is invalid
        form_bank_invalid = PayrollPaymentDestinationForm(data={
            'payment_type': 'bank',
            'bank_name': 'Test Bank',
            'branch_name': 'Test Branch',
            'account_holder_name': 'Rahim Uddin',
            'account_number': '',
        })
        self.assertFalse(form_bank_invalid.is_valid())
        self.assertIn('account_number', form_bank_invalid.errors)

        # MFS with invalid wallet number is invalid
        form_mfs_invalid = PayrollPaymentDestinationForm(data={
            'payment_type': 'mfs',
            'mfs_provider': 'bkash',
            'wallet_number': '999999',
        })
        self.assertFalse(form_mfs_invalid.is_valid())
        self.assertIn('wallet_number', form_mfs_invalid.errors)

        # Cash is valid with zero extra fields
        form_cash = PayrollPaymentDestinationForm(data={
            'payment_type': 'cash',
        })
        self.assertTrue(form_cash.is_valid())




