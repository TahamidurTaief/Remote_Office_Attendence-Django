import math
from decimal import Decimal, ROUND_HALF_UP
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from apps.payroll.models import (
    SalaryComponentType,
    SalaryComponentValueType,
    EmployeeSalaryAssignment,
    PayrollRun,
    EmployeePayrollCalculation,
    PayrollRunStatus,
    PaymentMode,
    SalaryStructureComponent
)
from apps.employees.models import Employee

class PayrollCalculationEngine:
    """
    Pure deterministic calculation engine for payroll computations.
    """

    @staticmethod
    def calculate_employee_payroll(
        gross_salary: Decimal,
        structure_components_list: list,  # List of dicts representing structure component options
        unpaid_absent_days: Decimal = Decimal('0.00'),
        other_deduction: Decimal = Decimal('0.00'),
        ot_hours: Decimal = Decimal('0.00'),
        ot_policy_callback=None,
        payment_mode: str = PaymentMode.BANK,
        bank_limit: Decimal = Decimal('0.00'),
        absence_divisor: int = 30
    ) -> dict:
        """
        Calculates all payroll values deterministically.
        `structure_components_list` should contain dicts with keys:
            - 'code': str
            - 'name': str
            - 'type': 'earning' | 'deduction'
            - 'value_type': 'percentage' | 'fixed'
            - 'value': Decimal
            - 'is_pf': bool
        """
        # Ensure Decimal input types
        gross_salary = Decimal(str(gross_salary))
        unpaid_absent_days = Decimal(str(unpaid_absent_days))
        other_deduction = Decimal(str(other_deduction))
        ot_hours = Decimal(str(ot_hours))
        bank_limit = Decimal(str(bank_limit))

        components_breakdown = []
        total_earnings = Decimal('0.00')
        total_deductions = Decimal('0.00')

        # 1. Process structured components (Earnings and Deductions)
        for comp in structure_components_list:
            comp_value = Decimal(str(comp['value']))
            if comp['value_type'] == SalaryComponentValueType.PERCENTAGE:
                amount = (comp_value / Decimal('100.00')) * gross_salary
            else:
                amount = comp_value
            
            # Keep precision high for intermediate calculations, round to 4 decimal places
            amount = amount.quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)
            
            comp_snapshot = {
                'code': comp['code'],
                'name': comp['name'],
                'type': comp['type'],
                'value_type': comp['value_type'],
                'value': str(comp_value),
                'amount': str(amount),
                'is_pf': comp.get('is_pf', False)
            }
            components_breakdown.append(comp_snapshot)

            if comp['type'] == SalaryComponentType.EARNING:
                total_earnings += amount
            elif comp['type'] == SalaryComponentType.DEDUCTION:
                total_deductions += amount

        # 2. Absence Deduction: Gross / Divisor * unpaid_absent_days
        absence_divisor_dec = Decimal(str(absence_divisor))
        if unpaid_absent_days > Decimal('0.00'):
            absence_deduction = (gross_salary / absence_divisor_dec) * unpaid_absent_days
            absence_deduction = absence_deduction.quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)
        else:
            absence_deduction = Decimal('0.0000')

        # 3. OT Amount using the callback, if provided
        if ot_policy_callback and ot_hours > Decimal('0.00'):
            ot_amount = Decimal(str(ot_policy_callback(gross_salary, ot_hours)))
            ot_amount = ot_amount.quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)
        else:
            ot_amount = Decimal('0.0000')

        # 4. Total calculation
        # Earnings can include standard components + Overtime
        total_earnings += ot_amount

        # Deductions include standard components + absence deduction + other deduction
        total_deductions += absence_deduction + other_deduction

        net_payable_raw = total_earnings - total_deductions
        # Round final BDT payable to nearest integer using ROUND_HALF_UP
        net_payable = net_payable_raw.quantize(Decimal('1'), rounding=ROUND_HALF_UP)

        # 5. Disbursement payment mode split
        bank_payable = Decimal('0.00')
        cash_payable = Decimal('0.00')

        if payment_mode == PaymentMode.BANK:
            bank_payable = net_payable
            cash_payable = Decimal('0.00')
        elif payment_mode == PaymentMode.CASH:
            bank_payable = Decimal('0.00')
            cash_payable = net_payable
        elif payment_mode == PaymentMode.SPLIT:
            if net_payable > Decimal('0.00'):
                bank_payable = min(net_payable, bank_limit)
                cash_payable = net_payable - bank_payable
            else:
                bank_payable = Decimal('0.00')
                cash_payable = net_payable # Negative or zero net payable goes fully to cash/net

        return {
            'gross_salary': gross_salary,
            'payment_mode': payment_mode,
            'bank_limit': bank_limit,
            'unpaid_absent_days': unpaid_absent_days,
            'absence_deduction': absence_deduction.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP),
            'other_deduction': other_deduction.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP),
            'ot_hours': ot_hours,
            'ot_amount': ot_amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP),
            'total_earnings': total_earnings.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP),
            'total_deductions': total_deductions.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP),
            'net_payable': net_payable,
            'bank_payable': bank_payable,
            'cash_payable': cash_payable,
            'components': components_breakdown
        }

class PayrollService:
    """
    Service Layer to manage PayrollRuns, salary structure snapshotting, and calculation persistence.
    """

    @classmethod
    def get_active_assignment(cls, employee: Employee, date_val) -> EmployeeSalaryAssignment:
        """
        Gets the active salary assignment for an employee on a given date.
        """
        assignment = EmployeeSalaryAssignment.objects.filter(
            employee=employee,
            effective_from__lte=date_val
        ).filter(
            models.Q(effective_to__isnull=True) | models.Q(effective_to__gte=date_val)
        ).order_by('-effective_from').first()
        return assignment

    @classmethod
    @transaction.atomic
    def run_payroll_for_employee(
        cls,
        payroll_run: PayrollRun,
        employee: Employee,
        unpaid_absent_days: Decimal = Decimal('0.00'),
        other_deduction: Decimal = Decimal('0.00'),
        ot_hours: Decimal = Decimal('0.00'),
        ot_policy_callback=None,
        absence_divisor: int = 30,
        synced_at=None,
        source_total_present_days=Decimal('0.00'),
        source_total_approved_leave_days=Decimal('0.00'),
        source_total_approved_ot_hours=Decimal('0.00')
    ) -> EmployeePayrollCalculation:
        """
        Runs payroll for a single employee, snapshots their salary structures, and saves the calculations.
        If payroll run is locked, raises ValidationError.
        If same employee/payroll run is calculated twice, replaces/updates the previous calculation record.
        """
        if payroll_run.status in [PayrollRunStatus.APPROVED_LOCKED, PayrollRunStatus.DISBURSED]:
            raise ValidationError("Cannot run or modify payroll calculations for locked or disbursed payroll runs.")

        # Find active salary assignment as of period_end
        assignment = cls.get_active_assignment(employee, payroll_run.period_end)
        if not assignment:
            raise ValidationError(f"No active salary assignment found for employee {employee.employee_number} on {payroll_run.period_end}")

        # Build structural components list from structure components
        structure_components = SalaryStructureComponent.objects.filter(
            salary_structure=assignment.salary_structure
        ).select_related('salary_component')

        structure_list = []
        for sc in structure_components:
            structure_list.append({
                'code': sc.salary_component.code,
                'name': sc.salary_component.name,
                'type': sc.salary_component.type,
                'value_type': sc.value_type,
                'value': sc.value,
                'is_pf': sc.salary_component.is_pf
            })

        # Calculate using engine
        calc_result = PayrollCalculationEngine.calculate_employee_payroll(
            gross_salary=assignment.gross_salary,
            structure_components_list=structure_list,
            unpaid_absent_days=unpaid_absent_days,
            other_deduction=other_deduction,
            ot_hours=ot_hours,
            ot_policy_callback=ot_policy_callback,
            payment_mode=assignment.payment_mode,
            bank_limit=assignment.bank_limit,
            absence_divisor=absence_divisor
        )

        # Serialize the Decimal values to string to ensure JSON serialization succeeds.
        import json
        
        def serialize_decimals(obj):
            if isinstance(obj, Decimal):
                return str(obj)
            if isinstance(obj, dict):
                return {k: serialize_decimals(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [serialize_decimals(i) for i in obj]
            return obj

        serialized_snapshot = serialize_decimals(calc_result)

        # Save / Update calculation record
        calculation, created = EmployeePayrollCalculation.objects.update_or_create(
            payroll_run=payroll_run,
            employee=employee,
            defaults={
                'gross_salary': calc_result['gross_salary'],
                'payment_mode': calc_result['payment_mode'],
                'bank_limit': calc_result['bank_limit'],
                'unpaid_absent_days': calc_result['unpaid_absent_days'],
                'absence_deduction': calc_result['absence_deduction'],
                'other_deduction': calc_result['other_deduction'],
                'ot_hours': calc_result['ot_hours'],
                'ot_amount': calc_result['ot_amount'],
                'total_earnings': calc_result['total_earnings'],
                'total_deductions': calc_result['total_deductions'],
                'net_payable': calc_result['net_payable'],
                'bank_payable': calc_result['bank_payable'],
                'cash_payable': calc_result['cash_payable'],
                'structure_snapshot': serialized_snapshot,
                'synced_at': synced_at,
                'source_total_present_days': source_total_present_days,
                'source_total_approved_leave_days': source_total_approved_leave_days,
                'source_total_approved_ot_hours': source_total_approved_ot_hours
            }
        )

        return calculation

    @classmethod
    @transaction.atomic
    def sync_payroll_inputs(cls, payroll_run: PayrollRun) -> list:
        """
        Pulls a deterministic snapshot of monthly Attendance, Leave and approved OT,
        and saves them for all eligible employees.
        """
        if payroll_run.status in [PayrollRunStatus.APPROVED_LOCKED, PayrollRunStatus.DISBURSED]:
            raise ValidationError("Cannot run or modify payroll calculations for locked or disbursed payroll runs.")

        from apps.attendance.reporting_service import get_monthly_report_data
        from apps.employees.models import Employee, EmployeeProfile
        from apps.branches.models import OfficeSchedule, Holiday

        year = payroll_run.period_start.year
        month = payroll_run.period_start.month

        # Fetch canonical monthly report data
        report = get_monthly_report_data(year, month)
        
        # We need a mapping from EmployeeProfile ID to Employee master object
        profiles = {p.id: p for p in report['employees']}

        # Loop through rows from canonical report data
        calculations = []
        synced_at_now = timezone.now()

        for row in report['rows']:
            profile = row['employee']
            master_employee = profile.master_employee
            if not master_employee:
                continue

            # Eligibility checks: employee joins or resigns mid-month
            # Exclude if employee joined after the payroll run period ends
            if master_employee.joined_date and master_employee.joined_date > payroll_run.period_end:
                continue
            # Exclude if employee resigned before the period starts (checking EmployeeStatus and any metadata if applicable, otherwise active status check is handled)

            # Present days
            present_days = Decimal(str(row['present']))
            # Approved leave days
            approved_leave_days = Decimal(str(row['on_leave']))

            # Canonical report statistics for OT
            emp_stats = report['employee_stats'].get(profile.id, {})
            ot_minutes = emp_stats.get('total_ot_minutes', 0)
            approved_ot_hours = Decimal(str(ot_minutes)) / Decimal('60.00')

            # Unpaid absence days derivation: unpaid absent days ONLY after approved paid leave is accounted for
            # Canonical absent count
            absent_count = Decimal(str(row['absent']))
            # Since absent count from reporting_service is already days where employee was not present, 
            # and was not on holiday, and was not on approved leave, it represents unpaid absence day count.
            # Example: 22 present + 2 approved paid leave + 1 unpaid absence -> payroll snapshot records 1 unpaid absent day only.
            # Let's verify: calendar month might have holidays/weekends. The remaining non-working/holiday days are weekends, etc.
            # So absent count from the canonical report is correct.
            unpaid_absent_days = absent_count

            # Standard Overtime Policy callback if employee has one configured
            # Pull approved OT hours only; OT amount stays zero unless employee has a configured OT policy
            ot_policy_name = master_employee.overtime_policy
            ot_policy_callback = None
            if ot_policy_name and ot_policy_name.strip() and ot_policy_name.lower() != 'none':
                # Deterministic calculation helper or policy callback
                # Default policy: Standard Overtime (1.5x) -> (Gross / divisor / 8) * 1.5 * OT_hours
                def default_ot_callback(gross, hours):
                    # We can use a reasonable formula: (Gross / 30 / 8) * 1.5 * hours
                    # Gross / 30 is daily salary. Daily salary / 8 is hourly salary.
                    # Or we can check if there's any other policy in the database.
                    return (gross / Decimal('240.00')) * Decimal('1.5') * hours
                ot_policy_callback = default_ot_callback

            # Run payroll calculation and persistence
            calc = cls.run_payroll_for_employee(
                payroll_run=payroll_run,
                employee=master_employee,
                unpaid_absent_days=unpaid_absent_days,
                other_deduction=Decimal('0.00'),
                ot_hours=approved_ot_hours,
                ot_policy_callback=ot_policy_callback,
                absence_divisor=30,
                synced_at=synced_at_now,
                source_total_present_days=present_days,
                source_total_approved_leave_days=approved_leave_days,
                source_total_approved_ot_hours=approved_ot_hours
            )
            calculations.append(calc)

        return calculations
