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
    def resolve_policy_from_snapshot(cls, payroll_run: PayrollRun, calc: EmployeePayrollCalculation):
        """
        Resolve (absence_divisor, ot_multiplier) strictly from stored calculation/run snapshots.
        Never reads current live configuration when a historical snapshot is expected.
        Raises ValidationError if required policy evidence is unavailable.
        """
        calc_snap = calc.structure_snapshot if isinstance(calc.structure_snapshot, dict) else {}
        policy_snap = calc_snap.get('policy_snapshot') if isinstance(calc_snap.get('policy_snapshot'), dict) else None
        run_snap = payroll_run.configuration_snapshot if isinstance(payroll_run.configuration_snapshot, dict) else {}

        absence_divisor = None
        ot_multiplier = None

        if policy_snap:
            if policy_snap.get('absence_divisor') is not None:
                absence_divisor = int(policy_snap['absence_divisor'])
            if policy_snap.get('ot_multiplier') is not None:
                ot_multiplier = Decimal(str(policy_snap['ot_multiplier']))

        if absence_divisor is None and run_snap:
            basis = run_snap.get('working_day_basis')
            if basis == 'fixed_30':
                absence_divisor = 30
            elif basis == 'calendar_days' and payroll_run.period_start and payroll_run.period_end:
                absence_divisor = (payroll_run.period_end - payroll_run.period_start).days + 1

        if ot_multiplier is None and run_snap and run_snap.get('overtime_multiplier') is not None:
            ot_multiplier = Decimal(str(run_snap['overtime_multiplier']))

        if absence_divisor is None or ot_multiplier is None:
            raise ValidationError(
                "Original payroll policy snapshot is unavailable; cannot safely recalculate adjustment."
            )

        return absence_divisor, ot_multiplier

    @classmethod
    @transaction.atomic
    def recalculate_employee_for_adjustment(
        cls,
        payroll_run: PayrollRun,
        employee: Employee,
        calc: EmployeePayrollCalculation,
    ) -> EmployeePayrollCalculation:
        """
        Recalculate a single employee's payroll snapshot after an adjustment change,
        preserving the original policy snapshot and synced inputs.
        """
        absence_divisor, ot_multiplier = cls.resolve_policy_from_snapshot(payroll_run, calc)
        return cls.run_payroll_for_employee(
            payroll_run=payroll_run,
            employee=employee,
            unpaid_absent_days=calc.unpaid_absent_days,
            other_deduction=calc.other_deduction,
            ot_hours=calc.ot_hours,
            absence_divisor=absence_divisor,
            ot_multiplier=ot_multiplier,
            synced_at=calc.synced_at,
            source_total_present_days=calc.source_total_present_days,
            source_total_approved_leave_days=calc.source_total_approved_leave_days,
            source_total_approved_ot_hours=calc.source_total_approved_ot_hours,
        )

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
        ot_multiplier: Decimal = None,
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

        # Ensure canonical tenant configuration is snapshot onto payroll run (only if no snapshot exists)
        if not payroll_run.configuration_id and not payroll_run.configuration_snapshot:
            try:
                from apps.payroll.configuration_service import PayrollConfigurationService
                from apps.tenants.context import get_current_tenant
                tenant = getattr(employee, 'tenant', None) or getattr(payroll_run, 'tenant', None) or get_current_tenant()
                if tenant:
                    cfg = PayrollConfigurationService.get_active_config(tenant, create_if_missing=False)
                    if cfg:
                        PayrollConfigurationService.attach_to_payroll_run(payroll_run, config=cfg)
            except Exception:
                pass

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
        effective_ot_multiplier = Decimal(str(ot_multiplier)) if ot_multiplier is not None else Decimal('1.50')
        if ot_policy_callback is None:
            ot_policy_name = employee.overtime_policy
            if ot_policy_name and ot_policy_name.strip() and ot_policy_name.lower() != 'none':
                if ot_policy_name == 'fixed_300':
                    ot_policy_callback = lambda gross, hours: Decimal('300.00') * hours
                else:
                    ot_policy_callback = (
                        lambda gross, hours, div=absence_divisor, mult=effective_ot_multiplier:
                        (gross / (Decimal(str(div)) * Decimal('8.00'))) * mult * hours
                    )

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
        serialized_snapshot['policy_snapshot'] = {
            'absence_divisor': int(absence_divisor),
            'ot_multiplier': str(effective_ot_multiplier),
            'ot_policy_name': employee.overtime_policy or '',
        }

        # Snapshot employee payment destination
        from apps.payroll.payment_destination_service import PayrollPaymentDestinationService
        dest_snapshot = PayrollPaymentDestinationService.snapshot_destination_for_calculation(employee)

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
                'payment_snapshot': dest_snapshot,
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
        Pulls a deterministic snapshot of Attendance, Leave and approved OT
        for the exact inclusive [payroll_run.period_start, payroll_run.period_end] period,
        using bulk queries and authoritative employee-day calculations.
        """
        if payroll_run.status in [PayrollRunStatus.APPROVED_LOCKED, PayrollRunStatus.DISBURSED]:
            raise ValidationError("Cannot run or modify payroll calculations for locked or disbursed payroll runs.")

        if not payroll_run.period_start or not payroll_run.period_end or payroll_run.period_end < payroll_run.period_start:
            raise ValidationError("Invalid payroll run period: period_start and period_end must form a valid inclusive range.")

        from collections import defaultdict
        from datetime import timedelta
        from apps.attendance.models import AttendancePolicy, OvertimeRequest
        from apps.attendance.reporting_service import (
            OptimizedSchedule,
            get_daily_report_data,
            is_employee_holiday_optimized,
        )
        from apps.branches.models import Holiday
        from apps.leave.models import LeaveRequest
        from apps.payroll.models import PayrollPolicy
        from apps.payroll.configuration_service import PayrollConfigurationService
        from apps.tenants.context import get_current_tenant

        p_start = payroll_run.period_start
        p_end = payroll_run.period_end

        # Single authoritative period-scoped attendance/eligibility calculation
        report = get_daily_report_data(start_date=p_start, end_date=p_end)

        rows_by_emp = defaultdict(list)
        for r in report['rows']:
            rows_by_emp[r['employee'].id].append(r)

        profile_ids = [p.id for p in report['employees']]
        if not profile_ids:
            return []

        # Bulk-load approved leaves overlapping [p_start, p_end]
        leave_by_emp_date = {}
        overlapping_leave_keys = set()
        leave_qs = (
            LeaveRequest.objects.filter(
                employee_id__in=profile_ids,
                status='approved',
                start_date__lte=p_end,
                end_date__gte=p_start,
            )
            .select_related('leave_type')
            .order_by('id')
        )
        for lr in leave_qs:
            s_d = max(lr.start_date, p_start)
            e_d = min(lr.end_date, p_end)
            day_weight = (
                Decimal(str(lr.number_of_days))
                if (lr.start_date == lr.end_date and lr.number_of_days is not None and Decimal('0.0') <= Decimal(str(lr.number_of_days)) < Decimal('1.0'))
                else Decimal('1.0')
            )
            ded_pct = lr.leave_type.deduction_percent or Decimal('0.00')
            curr_d = s_d
            while curr_d <= e_d:
                key = (lr.employee_id, curr_d)
                if key in leave_by_emp_date:
                    overlapping_leave_keys.add(key)
                else:
                    leave_by_emp_date[key] = (day_weight, ded_pct)
                curr_d += timedelta(days=1)

        # Preflight before any persistence: reject runs with unresolved employment dates or overlapping approved leaves
        unresolved_employees = []
        overlap_employees = []
        for profile in report['employees']:
            if not profile.master_employee:
                continue
            emp_rows = rows_by_emp.get(profile.id, [])
            if any(r.get('needs_review') or r.get('status') == 'eligibility_unresolved' for r in emp_rows):
                unresolved_employees.append(f"{profile.full_name} ({profile.employee_id})")
            if any(r['status'] == 'on_leave' and (profile.id, r['date']) in overlapping_leave_keys for r in emp_rows):
                overlap_employees.append(f"{profile.full_name} ({profile.employee_id})")
        if unresolved_employees:
            sample = ", ".join(unresolved_employees[:5])
            extra = f" (+{len(unresolved_employees) - 5} more)" if len(unresolved_employees) > 5 else ""
            raise ValidationError(
                f"Cannot sync payroll: unresolved employment dates require review for {sample}{extra}. "
                "Update the missing joining or exit date in Employment History before running payroll."
            )
        if overlap_employees:
            sample = ", ".join(overlap_employees[:5])
            extra = f" (+{len(overlap_employees) - 5} more)" if len(overlap_employees) > 5 else ""
            raise ValidationError(
                f"Cannot sync payroll: overlapping approved leave requests found for {sample}{extra}. "
                "Resolve duplicate approved leave requests before running payroll."
            )

        # Bulk-load approved OT minutes in [p_start, p_end]
        ot_by_emp = {
            row['employee_id']: (row['total'] or 0)
            for row in OvertimeRequest.objects.filter(
                employee_id__in=profile_ids,
                status='approved',
                date__gte=p_start,
                date__lte=p_end,
            )
            .values('employee_id')
            .annotate(total=models.Sum('ot_minutes'))
        }

        # Bulk-load policies, attendance policies & holidays for working_days divisor
        all_payroll_policies = list(PayrollPolicy.objects.all())
        payroll_policy_by_branch = {p.branch_id: p for p in all_payroll_policies if p.branch_id is not None}
        global_payroll_policy = next((p for p in all_payroll_policies if p.branch_id is None), None)

        att_policies = list(AttendancePolicy.objects.all())
        att_policies_by_branch = {p.branch_id: p for p in att_policies if p.branch_id is not None}
        global_att_policy = next((p for p in att_policies if p.branch_id is None), None)

        holidays = list(Holiday.objects.filter(date__gte=p_start, date__lte=p_end))
        branch_holidays = defaultdict(set)
        global_holidays = set()
        for h in holidays:
            if h.branch_id:
                branch_holidays[h.branch_id].add(h.date)
            else:
                global_holidays.add(h.date)

        # Resolve PayrollConfiguration once for run/tenant
        default_tenant = getattr(payroll_run, 'tenant', None) or get_current_tenant()
        cfg = payroll_run.configuration
        if not cfg and default_tenant:
            try:
                cfg = PayrollConfigurationService.get_active_config(default_tenant, create_if_missing=False)
                if cfg and not payroll_run.configuration_id:
                    PayrollConfigurationService.attach_to_payroll_run(payroll_run, config=cfg)
            except Exception:
                cfg = None

        calculations = []
        synced_at_now = timezone.now()
        period_dates = []
        curr_d = p_start
        while curr_d <= p_end:
            period_dates.append(curr_d)
            curr_d += timedelta(days=1)

        for profile in report['employees']:
            master_employee = profile.master_employee
            if not master_employee:
                continue

            emp_rows = rows_by_emp.get(profile.id, [])
            eligible_days = sum(1 for r in emp_rows if r['status'] != 'not_employed')
            if eligible_days == 0:
                continue

            present_days = Decimal(str(sum(1 for r in emp_rows if r['is_present'])))
            absent_count = Decimal(str(sum(1 for r in emp_rows if r['is_absent'])))

            approved_ot_minutes = ot_by_emp.get(profile.id, 0)
            approved_ot_hours = Decimal(str(approved_ot_minutes)) / Decimal('60.00')

            approved_leave_days = Decimal('0.00')
            leave_deduction_days = Decimal('0.00')
            for r in emp_rows:
                if r['status'] == 'on_leave':
                    info = leave_by_emp_date.get((profile.id, r['date']))
                    day_weight, ded_pct = info if info else (Decimal('1.0'), Decimal('0.00'))
                    approved_leave_days += day_weight
                    leave_deduction_days += (day_weight * ded_pct) / Decimal('100.00')

            approved_leave_days = approved_leave_days.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            deduction_days = (absent_count + leave_deduction_days).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

            branch = master_employee.branch if master_employee else profile.branch
            policy = payroll_policy_by_branch.get(branch.id if branch else None, global_payroll_policy)

            if cfg:
                divisor_mode = cfg.working_day_basis
            else:
                divisor_mode = policy.absence_divisor_mode if policy else 'fixed_30'

            if divisor_mode == 'calendar_days':
                absence_divisor = (p_end - p_start).days + 1
            elif divisor_mode == 'working_days':
                schedule = OptimizedSchedule(profile, att_policies_by_branch, global_att_policy)
                w_days = sum(
                    1 for d in period_dates
                    if not is_employee_holiday_optimized(profile, d, schedule, branch_holidays, global_holidays)
                )
                absence_divisor = max(w_days, 1)
            else:
                absence_divisor = 30

            if cfg:
                ot_multiplier = cfg.overtime_multiplier
            else:
                ot_multiplier = policy.default_ot_multiplier if policy else Decimal('1.50')

            ot_policy_name = master_employee.overtime_policy
            ot_policy_callback = None
            if ot_policy_name and ot_policy_name.strip() and ot_policy_name.lower() != 'none':
                if ot_policy_name == 'fixed_300':
                    ot_policy_callback = lambda gross, hours: Decimal('300.00') * hours
                else:
                    def default_ot_callback(gross, hours, div=absence_divisor, mult=ot_multiplier):
                        return (gross / (Decimal(str(div)) * Decimal('8.00'))) * mult * hours
                    ot_policy_callback = default_ot_callback

            calc = cls.run_payroll_for_employee(
                payroll_run=payroll_run,
                employee=master_employee,
                unpaid_absent_days=deduction_days,
                other_deduction=Decimal('0.00'),
                ot_hours=approved_ot_hours,
                ot_policy_callback=ot_policy_callback,
                absence_divisor=absence_divisor,
                ot_multiplier=ot_multiplier,
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
        if not (user.is_superuser or (hasattr(user, 'id') and PermissionEngine.evaluate(user, 'payroll.approve', action_type='approve').allowed)):
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
