import logging
import re
from typing import Dict, Any, Optional
from django.db import transaction
from django.core.exceptions import ValidationError, PermissionDenied
from django.utils import timezone
from apps.employees.models import Employee, Bank, BankBranch, EmployeeBankAccount
from apps.payroll.models import PayrollPaymentDestination, PaymentType, MFSProvider
from apps.tenants.models import Tenant
from apps.tenants.context import get_user_tenant, get_current_tenant
from apps.notifications.models import log_audit

logger = logging.getLogger(__name__)


class PayrollPaymentDestinationService:
    """
    Tenant-scoped, atomic management service for employee payroll payment destinations.
    Enforces:
    - Server-side conditional validation across Bank, Cash, and Mobile Financial Services (bKash, Nagad, Rocket).
    - Tenant boundary isolation.
    - Zero sensitive value leakage in logs/audits (masked at rest/audit).
    - Obsolete destination clearing on payment type transitions (e.g. Bank -> Cash).
    - Immutable snapshotting for historical payroll runs.
    """

    BD_WALLET_REGEX = re.compile(r"^01[3-9]\d{8,9}$")

    @classmethod
    def validate_wallet_number(cls, raw_number: str) -> str:
        if not raw_number:
            raise ValidationError({"wallet_number": "Wallet number is required for Mobile Financial Service."})
        cleaned = str(raw_number).strip().replace(" ", "").replace("-", "")
        if not cls.BD_WALLET_REGEX.match(cleaned):
            raise ValidationError({
                "wallet_number": "Invalid Bangladeshi wallet number. Must start with 01 and contain 11 digits (e.g. 017XXXXXXXX, or 12 for Rocket)."
            })
        return cleaned

    @classmethod
    def resolve_tenant_for_employee(cls, employee: Employee, target_tenant: Optional[Tenant] = None) -> Tenant:
        """
        Determines the canonical tenant for an employee with strict fallback guards.
        """
        if employee and employee.user_id:
            membership = employee.user.tenant_memberships.filter(is_active=True).select_related('tenant').first()
            if membership and membership.tenant:
                if target_tenant and target_tenant.id != membership.tenant_id:
                    raise ValidationError("Tenant boundary mismatch: forged cross-tenant operation detected.")
                return membership.tenant

        tenant = getattr(employee, 'tenant', None)
        if tenant:
            if target_tenant and target_tenant.id != tenant.id:
                raise ValidationError("Tenant boundary mismatch: forged cross-tenant operation detected.")
            return tenant

        existing = PayrollPaymentDestination.objects.filter(employee=employee).first()
        if existing and existing.tenant:
            if target_tenant and target_tenant.id != existing.tenant_id:
                raise ValidationError("Tenant boundary mismatch: forged cross-tenant operation detected.")
            return existing.tenant

        if target_tenant:
            return target_tenant

        # Fallback to current request context tenant or default tenant
        current = get_current_tenant()
        if current:
            return current
        from apps.tenants.context import get_default_tenant
        return get_default_tenant()

    @classmethod
    @transaction.atomic
    def save_destination(
        cls,
        employee: Employee,
        payment_type: str,
        data: Optional[Dict[str, Any]] = None,
        actor=None,
        tenant: Optional[Tenant] = None,
        user=None,
        **kwargs
    ) -> PayrollPaymentDestination:
        """
        Atomically configures or updates the employee payment destination.
        Clears obsolete fields on transition to avoid stale payment data.
        """
        if not employee or not employee.pk:
            raise ValidationError("A persisted Employee instance is required.")

        if actor is None and user is not None:
            actor = user

        payload = dict(data or {})
        payload.update(kwargs)

        resolved_tenant = cls.resolve_tenant_for_employee(employee, target_tenant=tenant)
        if not resolved_tenant:
            raise ValidationError("Tenant context could not be resolved for payment destination.")

        # If tenant was explicitly supplied, verify it matches employee's boundary
        if tenant and resolved_tenant.id != tenant.id:
            raise ValidationError("Tenant boundary mismatch: forged cross-tenant operation detected.")

        # Normalize payment type
        payment_type_clean = payment_type.lower().strip() if payment_type else 'bank'
        if payment_type_clean == 'mobile':
            payment_type_clean = PaymentType.MFS
        if payment_type_clean not in PaymentType.values:
            raise ValidationError({"payment_type": f"Unsupported payment type '{payment_type}'. Must be bank, cash, or mfs."})

        destination, _ = PayrollPaymentDestination.objects.select_for_update().get_or_create(
            employee=employee,
            defaults={
                'tenant': resolved_tenant,
                'payment_type': payment_type_clean,
            }
        )
        destination.tenant = resolved_tenant
        destination.payment_type = payment_type_clean

        # Track old state for audit
        old_payment_type = destination.payment_type
        old_masked = destination.get_masked_destination()

        # Handle BANK
        if payment_type_clean == PaymentType.BANK:
            bank = payload.get('bank')
            bank_name = (payload.get('bank_name') or '').strip()
            branch = payload.get('branch')
            branch_name = (payload.get('branch_name') or '').strip()
            account_holder = (payload.get('account_holder_name') or payload.get('account_holder') or employee.get_full_name()).strip()
            raw_acc_num = (payload.get('account_number') or payload.get('bank_account') or '').strip()
            routing_number = (payload.get('routing_number') or '').strip()

            # Resolve bank object from bank_name if needed
            if not bank and bank_name:
                bank = Bank.objects.filter(name__iexact=bank_name, is_active=True).first()

            if bank and not branch:
                # Pick first active branch or matching branch if not provided
                branch = bank.branches.filter(is_active=True).first()
                if branch and not branch_name:
                    branch_name = branch.name

            # Security check: cross-bank branch forging
            if bank and branch and branch.bank_id != bank.id:
                raise ValidationError({"branch": "Selected branch does not belong to the submitted bank."})

            if branch and not routing_number:
                routing_number = branch.routing_number

            destination.bank = bank
            destination.bank_name = bank_name or (bank.name if bank else '')
            destination.branch = branch
            destination.branch_name = branch_name or (branch.name if branch else '')
            destination.account_holder_name = account_holder
            destination.routing_number = routing_number
            if raw_acc_num:
                destination.set_account_number(raw_acc_num)

            # Clear obsolete MFS fields
            destination.mfs_provider = ''
            destination.wallet_number_encrypted = ''
            destination.wallet_number_last4 = ''

        # Handle MFS
        elif payment_type_clean == PaymentType.MFS:
            mfs_prov = (payload.get('mfs_provider') or '').lower().strip()
            if mfs_prov not in MFSProvider.values:
                raise ValidationError({"mfs_provider": f"Invalid MFS provider '{mfs_prov}'. Must be bkash, nagad, or rocket."})
            
            raw_wallet = payload.get('wallet_number') or payload.get('mfs_wallet_number') or payload.get('bank_account') or ''
            cleaned_wallet = cls.validate_wallet_number(raw_wallet)
            destination.mfs_provider = mfs_prov
            destination.set_wallet_number(cleaned_wallet)

            # Clear obsolete Bank fields
            destination.bank = None
            destination.bank_name = ''
            destination.branch = None
            destination.branch_name = ''
            destination.account_holder_name = ''
            destination.account_number_encrypted = ''
            destination.account_number_last4 = ''
            destination.routing_number = ''

        # Handle CASH
        elif payment_type_clean == PaymentType.CASH:
            # Clear all Bank and MFS fields atomically
            destination.bank = None
            destination.bank_name = ''
            destination.branch = None
            destination.branch_name = ''
            destination.account_holder_name = ''
            destination.account_number_encrypted = ''
            destination.account_number_last4 = ''
            destination.routing_number = ''
            destination.mfs_provider = ''
            destination.wallet_number_encrypted = ''
            destination.wallet_number_last4 = ''

        # Validate conditional rules
        destination.clean()
        destination.save()

        # Synchronize backward-compatible legacy fields on Employee model
        cls._sync_legacy_employee_fields(employee, destination)

        # Log sanitized audit event without sensitive account/wallet numbers
        cls._log_destination_audit(actor, employee, destination, old_payment_type, old_masked)

        return destination

    @classmethod
    def _sync_legacy_employee_fields(cls, employee: Employee, destination: PayrollPaymentDestination):
        """
        Updates legacy fields on Employee (`payment_method`, `bank_name`, `bank_account`)
        and maintains EmployeeBankAccount consistency.
        """
        emp_updated = False
        if destination.payment_type == PaymentType.BANK:
            employee.payment_method = 'bank'
            employee.bank_name = destination.bank_name
            employee.bank_account = destination.get_account_number()
            emp_updated = True

            # Sync primary EmployeeBankAccount if bank and branch foreign keys exist
            if destination.bank and destination.branch:
                raw_acc = destination.get_account_number()
                if raw_acc:
                    account = EmployeeBankAccount.objects.filter(employee=employee, is_primary=True).first()
                    if not account:
                        account = EmployeeBankAccount(employee=employee, is_primary=True)
                    account.bank = destination.bank
                    account.branch = destination.branch
                    account.account_holder_name = destination.account_holder_name
                    account.routing_number = destination.routing_number
                    account.set_account_number(raw_acc)
                    account.save()
        elif destination.payment_type == PaymentType.MFS:
            employee.payment_method = 'mobile'
            employee.bank_name = f"MFS - {destination.get_mfs_provider_display()}"
            employee.bank_account = destination.get_wallet_number()
            emp_updated = True
            # Deactivate bank accounts to avoid stale active bank records
            EmployeeBankAccount.objects.filter(employee=employee).update(is_active=False, is_primary=False)
        elif destination.payment_type == PaymentType.CASH:
            employee.payment_method = 'cash'
            employee.bank_name = ''
            employee.bank_account = ''
            emp_updated = True
            # Deactivate bank accounts
            EmployeeBankAccount.objects.filter(employee=employee).update(is_active=False, is_primary=False)

        if emp_updated:
            employee.save(update_fields=['payment_method', 'bank_name', 'bank_account'])

    @classmethod
    def _log_destination_audit(cls, actor, employee: Employee, destination: PayrollPaymentDestination, old_type: str, old_masked: dict):
        new_masked = destination.get_masked_destination()
        summary = (
            f"Updated payroll payment destination for {employee.employee_number} "
            f"to {destination.get_payment_type_display()}."
        )
        try:
            log_audit(
                actor=actor,
                action='payroll_payment_destination_updated',
                target=employee,
                summary=summary,
                metadata={
                    'employee_id': employee.id,
                    'employee_number': employee.employee_number,
                    'previous_payment_type': old_type,
                    'new_payment_type': destination.payment_type,
                    'masked_destination': new_masked,
                }
            )
        except Exception as e:
            logger.warning("Failed to write audit log for payment destination update: %s", e)

        try:
            from apps.audit.services import AuditService
            AuditService.log_event(
                actor=actor,
                action='payroll_payment_destination_updated',
                instance=destination,
                module='payroll',
                object_type='PayrollPaymentDestination',
                object_id=str(destination.pk),
                object_label=f"{employee.employee_number} Payment Destination",
                before=old_masked,
                after=new_masked,
                reason=summary
            )
        except Exception as e:
            logger.warning("Failed to write platform audit event: %s", e)

    @classmethod
    def get_destination(cls, employee: Employee) -> Optional[PayrollPaymentDestination]:
        return PayrollPaymentDestination.objects.filter(employee=employee).first()

    @classmethod
    def get_masked_destination(cls, employee: Employee) -> dict:
        dest = cls.get_destination(employee)
        if dest:
            return dest.get_masked_destination()
        # Fallback from legacy employee fields
        pm = getattr(employee, 'payment_method', 'cash') or 'cash'
        if pm == 'bank':
            raw_acc = getattr(employee, 'bank_account', '') or ''
            last4 = raw_acc[-4:] if len(raw_acc) >= 4 else raw_acc
            return {
                'payment_type': 'bank',
                'payment_type_display': 'Bank Transfer',
                'bank_name': getattr(employee, 'bank_name', ''),
                'account_number_masked': f"**********{last4}" if last4 else '',
                'account_number_last4': last4,
            }
        return {
            'payment_type': pm,
            'payment_type_display': 'Cash' if pm == 'cash' else 'Mobile Banking',
        }

    @classmethod
    def snapshot_destination_for_calculation(cls, employee: Employee) -> dict:
        """
        Creates an immutable, sanitized snapshot of the employee's payout destination
        for storage in EmployeePayrollCalculation.payment_snapshot.
        """
        dest = cls.get_destination(employee)
        if dest:
            return dest.to_snapshot()

        # Fallback snapshot from legacy fields
        masked = cls.get_masked_destination(employee)
        masked['snapshotted_at'] = timezone.now().isoformat()
        return masked
