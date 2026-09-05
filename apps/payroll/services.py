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
        absence_divisor: int = 30,
        manual_adjustments: list = None  # List of dicts representing manual adjustments
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
        if manual_adjustments is None:
            manual_adjustments = []

        # Find Basic component amount first if it exists
        basic_amount = Decimal('0.00')
        for comp in structure_components_list:
            if comp['code'].upper() == 'BASIC':
                comp_value = Decimal(str(comp['value']))
                if comp['value_type'] == SalaryComponentValueType.PERCENTAGE:
                    basic_amount = (comp_value / Decimal('100.00')) * gross_salary
                else:
                    basic_amount = comp_value
                basic_amount = basic_amount.quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)
                break

        components_breakdown = []
        total_earnings = Decimal('0.00')
        total_deductions = Decimal('0.00')

        # 1. Process structured components (Earnings and Deductions)
        for comp in structure_components_list:
            comp_value = Decimal(str(comp['value']))
            if comp.get('is_pf', False):
                if comp['value_type'] == SalaryComponentValueType.PERCENTAGE:
                    amount = (comp_value / Decimal('100.00')) * basic_amount
                else:
                    amount = comp_value
            elif comp['value_type'] == SalaryComponentValueType.PERCENTAGE:
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
                'is_pf': comp.get('is_pf', False),
                'is_adjustment': False
            }
            components_breakdown.append(comp_snapshot)

            if comp['type'] == SalaryComponentType.EARNING:
                total_earnings += amount
            elif comp['type'] == SalaryComponentType.DEDUCTION:
                total_deductions += amount

        # 1b. Process manual adjustments (Earnings and Deductions) exactly once
        for adj in manual_adjustments:
            adj_amount = Decimal(str(adj['amount'])).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)
            adj_snapshot = {
                'code': adj['component_code'],
                'name': adj['component_name'],
                'type': adj['type'],
                'value_type': 'fixed',
                'value': str(adj_amount),
                'amount': str(adj_amount),
                'is_pf': False,
                'is_adjustment': True,
                'reason': adj.get('reason', '')
            }
            components_breakdown.append(adj_snapshot)
            if adj['type'] == SalaryComponentType.EARNING:
                total_earnings += adj_amount
            elif adj['type'] == SalaryComponentType.DEDUCTION:
                total_deductions += adj_amount

        # 2. Absence Deduction: Gross / Divisor * unpaid_absent_days
        absence_divisor_val = max(int(absence_divisor or 30), 1)
        absence_divisor_dec = Decimal(str(absence_divisor_val))
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

        # Fetch manual adjustments for this employee in this payroll run
        from apps.payroll.models import PayrollAdjustment
        adjustments_qs = PayrollAdjustment.objects.filter(payroll_run=payroll_run, employee=employee)
        manual_adjustments = []
        for adj in adjustments_qs:
            manual_adjustments.append({
                'component_code': adj.component.code,
                'component_name': adj.component.name,
                'amount': adj.amount,
                'type': adj.type,
                'reason': adj.reason
            })

        # Resolve OT policy callback if None and employee has a configured policy
        if ot_policy_callback is None:
            ot_policy_name = employee.overtime_policy
            if ot_policy_name and ot_policy_name.strip() and ot_policy_name.lower() != 'none':
                if ot_policy_name == 'fixed_300':
                    ot_policy_callback = lambda gross, hours: Decimal('300.00') * hours
                else:
                    ot_policy_callback = lambda gross, hours: (gross / (Decimal(str(absence_divisor)) * Decimal('8.00'))) * Decimal('1.50') * hours

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
            absence_divisor=absence_divisor,
            manual_adjustments=manual_adjustments
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

            # Approved OT hours derived strictly from approved OvertimeRequest records
            from apps.attendance.models import OvertimeRequest
            approved_ot_minutes = OvertimeRequest.objects.filter(
                employee=profile,
                status='approved',
                date__gte=payroll_run.period_start,
                date__lte=payroll_run.period_end
            ).aggregate(total=models.Sum('ot_minutes'))['total'] or 0
            approved_ot_hours = Decimal(str(approved_ot_minutes)) / Decimal('60.00')

            # Unpaid absence days derivation with dynamic leave deductions
            absent_count = Decimal(str(row['absent']))

            from apps.leave.models import LeaveRequest
            overlapping_leaves = LeaveRequest.objects.filter(
                employee=profile,
                status='approved',
                start_date__lte=payroll_run.period_end,
                end_date__gte=payroll_run.period_start
            ).select_related('leave_type')

            leave_deduction_days = Decimal('0.00')
            for lr in overlapping_leaves:
                days_in_period = lr.overlapping_days_in(payroll_run.period_start, payroll_run.period_end)
                ded_pct = lr.leave_type.deduction_percent or Decimal('0.00')
                leave_deduction_days += (days_in_period * ded_pct) / Decimal('100.00')

            deduction_days = (absent_count + leave_deduction_days).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

            # Resolve PayrollPolicy for employee's branch (or company-wide default)
            from apps.payroll.models import PayrollPolicy
            branch = master_employee.branch if master_employee else profile.branch
            policy = None
            if branch:
                policy = PayrollPolicy.objects.filter(branch=branch).first()
            if not policy:
                policy = PayrollPolicy.objects.filter(branch__isnull=True).first()

            divisor_mode = policy.absence_divisor_mode if policy else 'fixed_30'
            if divisor_mode == 'calendar_days':
                absence_divisor = (payroll_run.period_end - payroll_run.period_start).days + 1
            elif divisor_mode == 'working_days':
                from apps.attendance.schedule_utils import get_branch_schedule
                from apps.branches.models import Holiday
                from datetime import timedelta
                from django.conf import settings
                schedule = get_branch_schedule(profile)
                working_days_set = getattr(settings, 'WORKING_DAYS', [0, 1, 2, 3, 5, 6])
                policy_str = master_employee.weekly_holiday_policy if master_employee.weekly_holiday_policy else ''
                w_policy = [day.strip().lower() for day in policy_str.split(',') if day.strip()]

                w_days = 0
                curr = payroll_run.period_start
                while curr <= payroll_run.period_end:
                    day_name = curr.strftime('%A').lower()
                    is_off = False
                    if w_policy:
                        if day_name in w_policy:
                            is_off = True
                    elif schedule:
                        if day_name not in schedule.working_days:
                            is_off = True
                    else:
                        if curr.weekday() not in working_days_set:
                            is_off = True

                    if not is_off:
                        h_qs = Holiday.objects.filter(date=curr)
                        if branch:
                            h_qs = h_qs.filter(models.Q(branch=branch) | models.Q(branch__isnull=True))
                        else:
                            h_qs = h_qs.filter(branch__isnull=True)
                        if not h_qs.exists():
                            w_days += 1
                    curr += timedelta(days=1)
                absence_divisor = max(w_days, 1)
            else:
                absence_divisor = 30

            ot_multiplier = policy.default_ot_multiplier if policy else Decimal('1.50')

            # Standard Overtime Policy callback if employee has one configured
            ot_policy_name = master_employee.overtime_policy
            ot_policy_callback = None
            if ot_policy_name and ot_policy_name.strip() and ot_policy_name.lower() != 'none':
                if ot_policy_name == 'fixed_300':
                    ot_policy_callback = lambda gross, hours: Decimal('300.00') * hours
                else:
                    def default_ot_callback(gross, hours, div=absence_divisor, mult=ot_multiplier):
                        return (gross / (Decimal(str(div)) * Decimal('8.00'))) * mult * hours
                    ot_policy_callback = default_ot_callback

            # Run payroll calculation and persistence
            calc = cls.run_payroll_for_employee(
                payroll_run=payroll_run,
                employee=master_employee,
                unpaid_absent_days=deduction_days,
                other_deduction=Decimal('0.00'),
                ot_hours=approved_ot_hours,
                ot_policy_callback=ot_policy_callback,
                absence_divisor=absence_divisor,
                synced_at=synced_at_now,
                source_total_present_days=present_days,
                source_total_approved_leave_days=approved_leave_days,
                source_total_approved_ot_hours=approved_ot_hours
            )
            calculations.append(calc)

        return calculations

    @classmethod
    @transaction.atomic
    def transition_payroll_status(cls, payroll_run: PayrollRun, target_status: PayrollRunStatus, user, note: str = '') -> PayrollRun:
        """
        Validates transition and updates status.
        Transitions flow: Draft -> Review -> Approved/Locked -> Disbursed.
        """
        old_status = payroll_run.status
        if old_status == target_status:
            return payroll_run

        # Validation logic for allowed flows
        allowed = False
        if old_status == PayrollRunStatus.DRAFT and target_status == PayrollRunStatus.REVIEW:
            allowed = True
        elif old_status == PayrollRunStatus.REVIEW and target_status == PayrollRunStatus.APPROVED_LOCKED:
            allowed = True
        elif old_status == PayrollRunStatus.APPROVED_LOCKED and target_status == PayrollRunStatus.DISBURSED:
            allowed = True

        if not allowed:
            raise ValidationError(f"Invalid payroll transition from {old_status} to {target_status}.")

        payroll_run.status = target_status
        payroll_run.save()

        # Audit transition
        from apps.payroll.models import PayrollWorkflowAudit, EmployeePayrollCalculation
        
        # Capture current snapshot state of calculations
        calcs = EmployeePayrollCalculation.objects.filter(payroll_run=payroll_run)
        serialized_calcs = []
        for calc in calcs:
            serialized_calcs.append({
                'employee_id': calc.employee.id,
                'employee_number': calc.employee.employee_number,
                'gross_salary': str(calc.gross_salary),
                'net_payable': str(calc.net_payable),
                'total_earnings': str(calc.total_earnings),
                'total_deductions': str(calc.total_deductions),
                'bank_payable': str(calc.bank_payable),
                'cash_payable': str(calc.cash_payable),
                'ot_hours': str(calc.ot_hours),
                'ot_amount': str(calc.ot_amount),
                'unpaid_absent_days': str(calc.unpaid_absent_days),
                'absence_deduction': str(calc.absence_deduction),
                'structure_snapshot': calc.structure_snapshot
            })

        PayrollWorkflowAudit.objects.create(
            payroll_run=payroll_run,
            from_status=old_status,
            to_status=target_status,
            action_by=user,
            note=note,
            snapshot_data={'calculations': serialized_calcs}
        )

        return payroll_run

    @classmethod
    @transaction.atomic
    def reverse_payroll_run(cls, payroll_run: PayrollRun, user, note: str = '') -> PayrollRun:
        """
        Reverses a locked or disbursed payroll run back to Draft, preserving snapshot history.
        Requires authorized admin action.
        """
        from apps.accounts.engine import PermissionEngine
        if not (user.is_superuser or PermissionEngine.evaluate(user, 'payroll.approve').allowed):
            raise ValidationError("Only authorized administrators can reverse payroll runs.")

        old_status = payroll_run.status
        if old_status not in [PayrollRunStatus.APPROVED_LOCKED, PayrollRunStatus.DISBURSED]:
            raise ValidationError("Only Approved/Locked or Disbursed payroll runs can be reversed.")

        # Capture snapshot of calculations for historical preservation
        from apps.payroll.models import PayrollWorkflowAudit, EmployeePayrollCalculation
        calcs = EmployeePayrollCalculation.objects.filter(payroll_run=payroll_run)
        serialized_calcs = []
        for calc in calcs:
            serialized_calcs.append({
                'employee_id': calc.employee.id,
                'employee_number': calc.employee.employee_number,
                'gross_salary': str(calc.gross_salary),
                'net_payable': str(calc.net_payable),
                'total_earnings': str(calc.total_earnings),
                'total_deductions': str(calc.total_deductions),
                'bank_payable': str(calc.bank_payable),
                'cash_payable': str(calc.cash_payable),
                'ot_hours': str(calc.ot_hours),
                'ot_amount': str(calc.ot_amount),
                'unpaid_absent_days': str(calc.unpaid_absent_days),
                'absence_deduction': str(calc.absence_deduction),
                'structure_snapshot': calc.structure_snapshot
            })

        # Save workflow reversal record
        PayrollWorkflowAudit.objects.create(
            payroll_run=payroll_run,
            from_status=old_status,
            to_status=PayrollRunStatus.DRAFT,
            action_by=user,
            note=note,
            snapshot_data={'calculations': serialized_calcs}
        )

        # Set status back to Draft
        payroll_run.status = PayrollRunStatus.DRAFT
        payroll_run.save()

        return payroll_run
