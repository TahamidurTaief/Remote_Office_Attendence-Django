import logging
from decimal import Decimal
from typing import Optional, Tuple, Dict, Any, List
from django.core.cache import cache
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.utils import timezone

from apps.payroll.models import (
    PayrollConfiguration,
    PayrollRun,
    AbsenceDivisorMode,
    PayFrequency,
    PayrollPeriodType,
    ProrationMethod,
    RoundingRule,
    NegativeNetPayPolicy,
)
from apps.tenants.models import Tenant

logger = logging.getLogger(__name__)


class PayrollConfigurationService:
    """
    Canonical service for tenant-scoped Payroll Configuration Center.
    Single source of truth for current payroll behavior, historical versioning,
    and permission-controlled AI simulation/actions.
    """

    CACHE_TIMEOUT = 3600  # 1 hour

    @classmethod
    def get_cache_key(cls, tenant_id: int) -> str:
        return f"payroll_config:tenant_{tenant_id}:active"

    @classmethod
    def invalidate_cache(cls, tenant_id: int):
        cache.delete(cls.get_cache_key(tenant_id))

    @classmethod
    def get_active_config(cls, tenant: Optional[Tenant], create_if_missing: bool = True) -> Optional[PayrollConfiguration]:
        """
        Retrieves the current active PayrollConfiguration for a tenant.
        Cached per-tenant for sub-millisecond lookups.
        If no configuration exists and create_if_missing is True, initializes v1 default.
        """
        if not tenant:
            return None

        cache_key = cls.get_cache_key(tenant.id)
        cached = cache.get(cache_key)
        if cached and getattr(cached, 'tenant_id', None) == tenant.id:
            if PayrollConfiguration.objects.filter(id=cached.id, is_active=True).exists():
                return cached
            else:
                cache.delete(cache_key)

        config = PayrollConfiguration.objects.filter(
            tenant=tenant,
            is_active=True
        ).select_related('tenant', 'created_by').first()

        if not config and create_if_missing:
            with transaction.atomic():
                config = PayrollConfiguration.objects.select_for_update().filter(
                    tenant=tenant,
                    is_active=True
                ).first()
                if not config:
                    config = cls._initialize_default_config(tenant)

        if config:
            cache.set(cache_key, config, cls.CACHE_TIMEOUT)

        return config

    @classmethod
    def _initialize_default_config(cls, tenant: Tenant) -> PayrollConfiguration:
        config = PayrollConfiguration.objects.create(
            tenant=tenant,
            version=1,
            is_active=True,
            is_archived=False,
            currency='BDT',
            pay_frequency=PayFrequency.MONTHLY,
            payroll_period_type=PayrollPeriodType.CALENDAR_MONTH,
            cutoff_day=25,
            payment_day=30,
            working_day_basis=AbsenceDivisorMode.FIXED_30,
            attendance_source='attendance.AttendanceRecord',
            leave_source='leave.LeaveRequest',
            overtime_source='attendance.AttendanceRecord',
            overtime_multiplier=Decimal('1.50'),
            proration_method=ProrationMethod.CALENDAR_DAYS,
            rounding_rule=RoundingRule.NEAREST_INTEGER,
            negative_net_pay_policy=NegativeNetPayPolicy.ZERO_OUT,
            approval_workflow_steps=2,
            payslip_prefix='PSL-',
            supported_payment_methods=['bank', 'cash', 'split'],
            notes="Initial default payroll configuration for tenant."
        )
        cls._log_audit(
            action='created',
            instance=config,
            actor=None,
            reason="Auto-initialized default tenant payroll configuration v1"
        )
        cls.invalidate_cache(tenant.id)
        return config

    @classmethod
    def get_config_by_version(cls, tenant: Tenant, version: int) -> Optional[PayrollConfiguration]:
        """Retrieves a specific configuration version for a tenant."""
        return PayrollConfiguration.objects.filter(
            tenant=tenant,
            version=version
        ).select_related('tenant', 'created_by', 'archived_by').first()

    @classmethod
    def get_config_history(cls, tenant: Tenant) -> List[PayrollConfiguration]:
        """Returns all versions (active and archived) for a tenant ordered newest first."""
        return list(
            PayrollConfiguration.objects.filter(tenant=tenant)
            .select_related('created_by', 'archived_by')
            .order_by('-version')
        )

    @classmethod
    @transaction.atomic
    def save_or_update_config(
        cls,
        tenant: Tenant,
        user,
        data: Dict[str, Any],
        force_new_version: bool = False
    ) -> Tuple[PayrollConfiguration, bool]:
        """
        Safely saves configuration changes with version integrity.
        Rule: If the active configuration has already been used/referenced by any PayrollRun,
        we NEVER mutate it in-place. We archive it and spawn an incremented effective version (v+1).
        If untouched by historical payroll, it is updated in-place cleanly.
        Returns (configuration_instance, is_new_version: bool).
        """
        if not tenant:
            raise ValidationError("A valid tenant is required to configure payroll.")

        # Concurrency guard: lock current active configuration
        active_config = PayrollConfiguration.objects.select_for_update().filter(
            tenant=tenant,
            is_active=True
        ).first()

        if not active_config:
            active_config = cls._initialize_default_config(tenant)

        has_runs = active_config.payroll_runs.exists()
        should_version = has_runs or force_new_version

        sanitized = cls._sanitize_config_data(data)
        actor = user if getattr(user, 'is_authenticated', False) else None

        if should_version:
            # 1. Archive previous active version
            before_snapshot = active_config.to_snapshot()
            active_config.is_active = False
            active_config.is_archived = True
            active_config.archived_at = timezone.now()
            active_config.archived_by = actor
            active_config.save()

            # 2. Create incremented version
            new_version = active_config.version + 1
            new_config = PayrollConfiguration.objects.create(
                tenant=tenant,
                version=new_version,
                is_active=True,
                is_archived=False,
                created_by=actor,
                **sanitized
            )

            cls.invalidate_cache(tenant.id)
            cls._log_audit(
                action='version_created',
                instance=new_config,
                actor=actor,
                before=before_snapshot,
                after=new_config.to_snapshot(),
                reason=f"Spawned version {new_version} because v{active_config.version} is referenced by finalized/active payroll runs."
            )
            return new_config, True
        else:
            # Update active config in-place
            before_snapshot = active_config.to_snapshot()
            for k, v in sanitized.items():
                setattr(active_config, k, v)
            active_config.save()

            cls.invalidate_cache(tenant.id)
            cls._log_audit(
                action='updated',
                instance=active_config,
                actor=actor,
                before=before_snapshot,
                after=active_config.to_snapshot(),
                reason=f"Updated v{active_config.version} configuration in-place (no finalized payroll runs attached)."
            )
            return active_config, False

    @classmethod
    def attach_to_payroll_run(cls, payroll_run: PayrollRun, config: Optional[PayrollConfiguration] = None) -> PayrollRun:
        """
        Attaches the tenant configuration and immutable snapshot to a payroll run.
        """
        tenant = getattr(payroll_run, 'tenant', None)
        if not config:
            if not tenant:
                # Try to derive tenant from calculations or context
                first_calc = payroll_run.calculations.select_related('employee__branch').first()
                if first_calc and hasattr(first_calc.employee, 'tenant'):
                    tenant = first_calc.employee.tenant
                else:
                    from apps.tenants.context import get_current_tenant
                    tenant = get_current_tenant()
            if tenant:
                config = cls.get_active_config(tenant, create_if_missing=False)

        if config and config.id and PayrollConfiguration.objects.filter(id=config.id).exists():
            payroll_run.configuration = config
            payroll_run.configuration_snapshot = config.to_snapshot()
            payroll_run.save(update_fields=['configuration', 'configuration_snapshot'])

        return payroll_run

    @classmethod
    def simulate_payroll(
        cls,
        tenant: Tenant,
        user,
        sample_employees: Optional[List[Any]] = None,
        overrides: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Pure, zero-side-effect simulation engine for AI agents and preview calculations.
        Applies config or provisional overrides without persisting anything to the database.
        """
        from apps.payroll.services import PayrollCalculationEngine
        from apps.payroll.models import EmployeeSalaryAssignment, SalaryStructureComponent

        config = cls.get_active_config(tenant, create_if_missing=False)
        effective_config = config.to_snapshot() if config else {
            'currency': 'BDT',
            'working_day_basis': AbsenceDivisorMode.FIXED_30,
            'overtime_multiplier': '1.50',
            'cutoff_day': 25,
            'payment_day': 30,
            'version': 1,
        }
        if overrides:
            effective_config.update(overrides)

        # Get active employees for tenant
        from apps.employees.models import Employee, EmployeeStatus
        emp_qs = Employee.objects.filter(status=EmployeeStatus.ACTIVE)
        if hasattr(Employee, 'tenant'):
            emp_qs = emp_qs.filter(tenant=tenant)
        elif tenant:
            # Scope via branch if branch has tenant or all tenant employees
            pass

        if sample_employees:
            emp_qs = emp_qs.filter(pk__in=[e.pk if hasattr(e, 'pk') else e for e in sample_employees])
        else:
            emp_qs = emp_qs[:5]  # Sample up to 5 employees for preview

        results = []
        total_gross = Decimal('0.00')
        total_net = Decimal('0.00')
        total_deductions = Decimal('0.00')
        total_ot = Decimal('0.00')

        divisor_mode = effective_config.get('working_day_basis', AbsenceDivisorMode.FIXED_30)
        divisor = 30
        if divisor_mode == AbsenceDivisorMode.CALENDAR_DAYS:
            divisor = 31
        elif divisor_mode == AbsenceDivisorMode.WORKING_DAYS:
            divisor = 22

        ot_multiplier = Decimal(str(effective_config.get('overtime_multiplier', '1.50')))

        for emp in emp_qs:
            assignment = EmployeeSalaryAssignment.objects.filter(
                employee=emp
            ).order_by('-effective_from').first()

            if not assignment:
                continue

            structure_components = SalaryStructureComponent.objects.filter(
                salary_structure=assignment.salary_structure
            ).select_related('salary_component')

            structure_list = [
                {
                    'code': sc.salary_component.code,
                    'name': sc.salary_component.name,
                    'type': sc.salary_component.type,
                    'value_type': sc.value_type,
                    'value': sc.value,
                    'is_pf': sc.salary_component.is_pf,
                }
                for sc in structure_components
            ]

            calc_result = PayrollCalculationEngine.calculate_employee_payroll(
                gross_salary=assignment.gross_salary,
                structure_components_list=structure_list,
                unpaid_absent_days=Decimal('1.00'),  # Sample 1 day absent
                ot_hours=Decimal('4.00'),             # Sample 4 hrs OT
                ot_policy_callback=lambda gross, hours, d=divisor, m=ot_multiplier: (gross / (Decimal(str(d)) * Decimal('8.00'))) * m * hours,
                payment_mode=assignment.payment_mode,
                bank_limit=assignment.bank_limit,
                absence_divisor=divisor
            )

            total_gross += calc_result['gross_salary']
            total_net += calc_result['net_payable']
            total_deductions += calc_result['total_deductions']
            total_ot += calc_result['ot_amount']

            results.append({
                'employee_id': emp.id,
                'employee_number': emp.employee_number,
                'name': f"{emp.first_name} {emp.last_name}".strip(),
                'gross_salary': str(calc_result['gross_salary']),
                'net_payable': str(calc_result['net_payable']),
                'absence_deduction': str(calc_result['absence_deduction']),
                'ot_amount': str(calc_result['ot_amount']),
                'bank_payable': str(calc_result['bank_payable']),
                'cash_payable': str(calc_result['cash_payable']),
            })

        simulation_payload = {
            'tenant_id': tenant.id if tenant else None,
            'simulated_version': effective_config.get('version', 1),
            'currency': effective_config.get('currency', 'BDT'),
            'working_day_basis': divisor_mode,
            'overtime_multiplier': str(ot_multiplier),
            'cutoff_day': effective_config.get('cutoff_day', 25),
            'payment_day': effective_config.get('payment_day', 30),
            'summary': {
                'employee_count': len(results),
                'total_gross': str(total_gross),
                'total_net': str(total_net),
                'total_deductions': str(total_deductions),
                'total_ot': str(total_ot),
            },
            'sample_breakdowns': results,
            'is_simulation': True,
        }

        cls._log_audit(
            action='simulated',
            instance=config,
            actor=user,
            after=simulation_payload['summary'],
            reason="Executed zero-side-effect payroll simulation"
        )

        return simulation_payload

    @classmethod
    def _sanitize_config_data(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        """Validates and cleans input configuration fields."""
        sanitized = {}
        allowed_fields = [
            'currency', 'pay_frequency', 'payroll_period_type', 'cutoff_day',
            'payment_day', 'working_day_basis', 'attendance_source', 'leave_source',
            'overtime_source', 'overtime_multiplier', 'proration_method',
            'rounding_rule', 'negative_net_pay_policy', 'approval_workflow_steps',
            'payslip_prefix', 'supported_payment_methods', 'effective_from', 'notes'
        ]
        for f in allowed_fields:
            if f in data:
                val = data[f]
                if f in ['cutoff_day', 'payment_day', 'approval_workflow_steps'] and val is not None:
                    val = int(val)
                elif f == 'overtime_multiplier' and val is not None:
                    val = Decimal(str(val))
                elif f == 'supported_payment_methods' and isinstance(val, str):
                    val = [m.strip() for m in val.split(',') if m.strip()]
                sanitized[f] = val

        # Ensure sensible defaults
        if 'cutoff_day' in sanitized:
            sanitized['cutoff_day'] = max(1, min(31, sanitized['cutoff_day']))
        if 'payment_day' in sanitized:
            sanitized['payment_day'] = max(1, min(31, sanitized['payment_day']))
        if 'supported_payment_methods' in sanitized and not sanitized['supported_payment_methods']:
            sanitized['supported_payment_methods'] = ['bank', 'cash', 'split']

        return sanitized

    @classmethod
    def _log_audit(cls, action: str, instance=None, actor=None, before=None, after=None, reason=""):
        try:
            from apps.audit.services import AuditService
            AuditService.log_event(
                actor=actor,
                action=action,
                instance=instance,
                module='payroll',
                object_type='PayrollConfiguration',
                object_id=str(instance.pk) if instance else '',
                object_label=str(instance) if instance else 'PayrollConfiguration',
                before=before,
                after=after,
                reason=reason
            )
        except Exception as e:
            logger.warning("Audit logging failed in PayrollConfigurationService: %s", e)
