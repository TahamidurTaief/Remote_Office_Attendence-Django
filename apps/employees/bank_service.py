import re
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.audit.services import AuditService
from .models import Bank, BankBranch, Employee, EmployeeBankAccount
from .bank_crypto import normalize_account_number, mask_account_number


def can_verify_bank_account(user) -> bool:
    """Only authorized HR and finance personnel can verify employee bank accounts."""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False) or getattr(user, "is_staff", False):
        return True
    
    role = getattr(user, "role", "")
    if role in ("admin", "hr", "finance", "accounts"):
        return True
        
    if hasattr(user, "role_assignments"):
        roles = user.role_assignments.filter(role__is_active=True).values_list("role__code", flat=True)
        if any(r in ("admin", "hr", "finance", "accounts") for r in roles):
            return True
            
    return user.has_perm("employees.change_employeebankaccount") or user.has_perm("payroll.change_payrollrun")


class BankService:
    @staticmethod
    def validate_account_number(account_number: str) -> str:
        cleaned = normalize_account_number(account_number)
        if not cleaned:
            raise ValidationError("Account number is required for bank transfer.")
        if len(cleaned) < 6 or len(cleaned) > 30:
            raise ValidationError("Bank account number must be between 6 and 30 characters.")
        if not re.match(r"^[A-Za-z0-9]+$", cleaned):
            raise ValidationError("Bank account number must contain only alphanumeric characters.")
        return cleaned

    @staticmethod
    def validate_bank_and_branch(bank_id, branch_id) -> tuple[Bank, BankBranch]:
        if not bank_id:
            raise ValidationError({"bank": "Please select a bank."})
        if not branch_id:
            raise ValidationError({"branch": "Please select a branch."})

        try:
            bank = Bank.objects.get(pk=bank_id, is_active=True)
        except Bank.DoesNotExist:
            raise ValidationError({"bank": "Selected bank is invalid or inactive."})

        try:
            branch = BankBranch.objects.get(pk=branch_id, is_active=True)
        except BankBranch.DoesNotExist:
            raise ValidationError({"branch": "Selected branch is invalid or inactive."})

        if branch.bank_id != bank.id:
            raise ValidationError({"branch": "Selected branch does not belong to the submitted bank."})

        return bank, branch

    @classmethod
    @transaction.atomic
    def save_employee_bank_account(
        cls,
        employee: Employee,
        bank: Bank,
        branch: BankBranch,
        account_holder_name: str,
        account_number: str,
        is_primary: bool = True,
        actor=None,
    ) -> EmployeeBankAccount:
        """
        Creates or updates an EmployeeBankAccount with server-side validation,
        Fernet encryption, backward compatibility sync, and audit logging.
        """
        if branch.bank_id != bank.id:
            raise ValidationError({"branch": "Branch does not belong to submitted bank."})

        cleaned_number = cls.validate_account_number(account_number)
        holder_name = (account_holder_name or employee.get_full_name()).strip()

        # Check existing primary or matching account
        existing = EmployeeBankAccount.objects.filter(
            employee=employee,
            bank=bank,
            account_number_last4=cleaned_number[-4:] if len(cleaned_number) >= 4 else cleaned_number,
            is_active=True,
        ).first()

        before_state = {}
        if existing:
            account = existing
            before_state = {
                "bank": existing.bank.name,
                "branch": existing.branch.name,
                "routing_number": existing.routing_number,
                "account_number": existing.masked_account_number,
                "verification_status": existing.verification_status,
            }
            # If account number changed, must reset verification status
            if account.get_account_number() != cleaned_number:
                account.verification_status = EmployeeBankAccount.VerificationStatus.PENDING
                account.verified_by = None
                account.verified_at = None
        else:
            account = EmployeeBankAccount(
                employee=employee,
                verification_status=EmployeeBankAccount.VerificationStatus.PENDING,
            )

        account.bank = bank
        account.branch = branch
        account.routing_number = branch.routing_number
        account.account_holder_name = holder_name
        account.is_primary = is_primary
        account.is_active = True
        account.set_account_number(cleaned_number)
        account.save()

        # Audit event
        AuditService.log_event(
            actor=actor,
            action="bank_account_saved",
            instance=account,
            module="employees",
            object_type="EmployeeBankAccount",
            object_id=str(account.pk),
            object_label=f"{holder_name} - {bank.name}",
            before=before_state,
            after={
                "bank": bank.name,
                "branch": branch.name,
                "routing_number": branch.routing_number,
                "account_number": account.masked_account_number,
                "verification_status": account.verification_status,
            },
            reason="Employee bank account updated",
        )

        return account

    @classmethod
    @transaction.atomic
    def verify_bank_account(cls, account: EmployeeBankAccount, actor, note: str = "") -> EmployeeBankAccount:
        """
        Transition account verification state to VERIFIED. Strictly guards against
        unauthorized actors and marks the account payout-ready.
        """
        if not can_verify_bank_account(actor):
            raise PermissionDenied("Only authorized HR or Finance personnel can verify bank accounts.")

        before_status = account.verification_status
        account.verification_status = EmployeeBankAccount.VerificationStatus.VERIFIED
        account.verified_by = actor
        account.verified_at = timezone.now()
        if note:
            account.verification_note = note
        account.save(update_fields=["verification_status", "verified_by", "verified_at", "verification_note", "updated_at"])

        AuditService.log_event(
            actor=actor,
            action="bank_account_verified",
            instance=account,
            module="employees",
            object_type="EmployeeBankAccount",
            object_id=str(account.pk),
            object_label=str(account),
            before={"verification_status": before_status},
            after={"verification_status": account.verification_status, "verified_by": getattr(actor, "username", str(actor))},
            reason=note or "Account verified by authorized personnel",
        )
        return account

    @classmethod
    def map_legacy_bank_account(cls, employee: Employee) -> tuple[bool, str]:
        """
        Attempts backward-compatible mapping of legacy Employee.bank_name & Employee.bank_account
        into canonical Bank and BankBranch models.
        Preserves original values if ambiguous or unmatched.
        """
        raw_name = (employee.bank_name or "").strip()
        raw_acc = (employee.bank_account or "").strip()

        if not raw_name or not raw_acc:
            return False, "No legacy bank details to map."

        # Search for canonical bank
        bank = Bank.objects.filter(
            Q(name__iexact=raw_name) | Q(short_name__iexact=raw_name) | Q(code__iexact=raw_name),
            is_active=True,
        ).first()

        if not bank:
            # Fuzzy fallback for common naming differences like "City Bank Ltd" -> "The City Bank PLC"
            tokens = raw_name.lower().replace("ltd", "").replace("plc", "").replace("bank", "").strip()
            if tokens:
                bank = Bank.objects.filter(name__icontains=tokens, is_active=True).first()

        if not bank:
            return False, f"Unmatched legacy bank '{raw_name}'. Original data preserved for manual mapping."

        # Default to principal branch if branch unknown in legacy data
        branch = bank.branches.filter(is_active=True).order_by("display_order", "id").first()
        if not branch:
            return False, f"Bank '{bank.name}' has no registered branches."

        try:
            account = cls.save_employee_bank_account(
                employee=employee,
                bank=bank,
                branch=branch,
                account_holder_name=employee.get_full_name(),
                account_number=raw_acc,
                is_primary=True,
            )
            return True, f"Successfully mapped to {bank.name} ({branch.name}). Verification status: {account.verification_status}."
        except ValidationError as e:
            return False, f"Validation error during legacy mapping: {e.message_dict if hasattr(e, 'message_dict') else e}"
