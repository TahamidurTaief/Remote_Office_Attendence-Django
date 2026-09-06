import uuid
from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone
from apps.employees.models import Employee
from apps.tenants.models import TenantBaseModel
from decimal import Decimal

class SalaryComponentType(models.TextChoices):
    EARNING = 'earning', 'Earning'
    DEDUCTION = 'deduction', 'Deduction'

class SalaryComponentValueType(models.TextChoices):
    PERCENTAGE = 'percentage', 'Percentage of Gross'
    FIXED = 'fixed', 'Fixed Amount'

class SalaryComponent(models.Model):
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=50, unique=True, db_index=True)
    type = models.CharField(max_length=20, choices=SalaryComponentType.choices, default=SalaryComponentType.EARNING)
    value_type = models.CharField(max_length=20, choices=SalaryComponentValueType.choices, default=SalaryComponentValueType.PERCENTAGE)
    value = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal('0.0000'))
    is_pf = models.BooleanField(default=False, help_text="Flag to identify Provident Fund deduction component")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.code})"

class SalaryStructure(models.Model):
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    @property
    def earning_percentage_sum(self):
        return sum(
            sc.value for sc in self.structure_components.select_related('salary_component').all()
            if sc.salary_component.type == 'earning' and sc.value_type == 'percentage'
        )

class SalaryStructureComponent(models.Model):
    salary_structure = models.ForeignKey(SalaryStructure, on_delete=models.CASCADE, related_name='structure_components')
    salary_component = models.ForeignKey(SalaryComponent, on_delete=models.CASCADE)
    # The percentage or fixed value override for this structure
    value_type = models.CharField(max_length=20, choices=SalaryComponentValueType.choices, default=SalaryComponentValueType.PERCENTAGE)
    value = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal('0.0000'))

    class Meta:
        unique_together = ('salary_structure', 'salary_component')

    def __str__(self):
        return f"{self.salary_structure.name} - {self.salary_component.code}: {self.value}"

class PaymentMode(models.TextChoices):
    BANK = 'bank', 'Bank Transfer'
    CASH = 'cash', 'Cash / Cheque'
    SPLIT = 'split', 'Split (Bank + Cash)'

class EmployeeSalaryAssignment(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='salary_assignments')
    salary_structure = models.ForeignKey(SalaryStructure, on_delete=models.PROTECT)
    gross_salary = models.DecimalField(max_digits=12, decimal_places=2)
    effective_from = models.DateField(db_index=True)
    effective_to = models.DateField(null=True, blank=True, db_index=True)
    payment_mode = models.CharField(max_length=20, choices=PaymentMode.choices, default=PaymentMode.BANK)
    bank_limit = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), help_text="Limit for bank transfer in split mode")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.employee.employee_number} - Gross: {self.gross_salary} ({self.effective_from} to {self.effective_to or 'Present'})"

class PayrollRunStatus(models.TextChoices):
    DRAFT = 'draft', 'Draft'
    REVIEW = 'review', 'Review'
    APPROVED_LOCKED = 'approved_locked', 'Approved / Locked'
    DISBURSED = 'disbursed', 'Disbursed'

class PayrollRun(models.Model):
    name = models.CharField(max_length=100, blank=True)
    period_start = models.DateField(db_index=True)
    period_end = models.DateField(db_index=True)
    status = models.CharField(max_length=20, choices=PayrollRunStatus.choices, default=PayrollRunStatus.DRAFT)
    configuration = models.ForeignKey(
        'payroll.PayrollConfiguration',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='payroll_runs',
        help_text="Configuration version snapshot used for this payroll run"
    )
    configuration_snapshot = models.JSONField(
        default=dict,
        blank=True,
        help_text="Immutable snapshot of the configuration at the time of calculation/finalization"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-period_start']

    def __str__(self):
        return f"Payroll Run {self.period_start} to {self.period_end} ({self.status})"

class EmployeePayrollCalculation(models.Model):
    payroll_run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name='calculations')
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='payroll_calculations')
    
    # Snapshots
    gross_salary = models.DecimalField(max_digits=12, decimal_places=2)
    payment_mode = models.CharField(max_length=20, choices=PaymentMode.choices)
    bank_limit = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    
    # Inputs/Metrics
    unpaid_absent_days = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'))
    absence_deduction = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    other_deduction = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    ot_hours = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('0.00'))
    ot_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    
    # Sync Info & Source Totals Snapshots
    synced_at = models.DateTimeField(null=True, blank=True)
    source_total_present_days = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'))
    source_total_approved_leave_days = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'))
    source_total_approved_ot_hours = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('0.00'))
    
    # Final Calculated Fields
    total_earnings = models.DecimalField(max_digits=12, decimal_places=2)
    total_deductions = models.DecimalField(max_digits=12, decimal_places=2)
    net_payable = models.DecimalField(max_digits=12, decimal_places=2)
    bank_payable = models.DecimalField(max_digits=12, decimal_places=2)
    cash_payable = models.DecimalField(max_digits=12, decimal_places=2)
    
    # Structural Breakdown Snapshot
    structure_snapshot = models.JSONField(help_text="Detailed JSON snapshot of salary components configuration and calculation results")
    payment_snapshot = models.JSONField(default=dict, blank=True, help_text="Immutable snapshot of employee payment destination at calculation time")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('payroll_run', 'employee')

    def __str__(self):
        return f"{self.employee.employee_number} - Net: {self.net_payable} ({self.payroll_run.period_start})"

class PayrollAdjustment(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='payroll_adjustments')
    payroll_run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name='adjustments')
    component = models.ForeignKey(SalaryComponent, on_delete=models.PROTECT, related_name='adjustments')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    type = models.CharField(max_length=20, choices=SalaryComponentType.choices)
    reason = models.TextField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    sync_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.employee.employee_number} - {self.component.code}: {self.amount} ({self.type})"

class PayrollWorkflowAudit(models.Model):
    payroll_run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name='workflow_audits')
    from_status = models.CharField(max_length=50)
    to_status = models.CharField(max_length=50)
    action_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    action_at = models.DateTimeField(auto_now_add=True)
    note = models.TextField(blank=True)
    snapshot_data = models.JSONField(help_text="Complete calculation state snapshot at the time of this transition")

    class Meta:
        ordering = ['-action_at']

    def __str__(self):
        return f"Payroll {self.payroll_run.id} from {self.from_status} to {self.to_status} at {self.action_at}"


class AbsenceDivisorMode(models.TextChoices):
    FIXED_30 = 'fixed_30', 'Fixed 30'
    CALENDAR_DAYS = 'calendar_days', 'Calendar Days'
    WORKING_DAYS = 'working_days', 'Working Days'


class PayrollPolicy(models.Model):
    branch = models.OneToOneField(
        'branches.Branch',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='payroll_policy',
        help_text="Branch for this policy. Null represents company-wide default."
    )
    absence_divisor_mode = models.CharField(
        max_length=20,
        choices=AbsenceDivisorMode.choices,
        default=AbsenceDivisorMode.FIXED_30
    )
    default_ot_multiplier = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        default=Decimal('1.50')
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        branch_name = self.branch.name if self.branch else "Company-wide Default"
        return f"Payroll Policy ({branch_name}): {self.get_absence_divisor_mode_display()}, OT: {self.default_ot_multiplier}x"


class PayrollPeriodType(models.TextChoices):
    CALENDAR_MONTH = 'calendar_month', 'Calendar Month'
    CUSTOM_CYCLE = 'custom_cycle', 'Custom Cutoff Cycle'


class PayFrequency(models.TextChoices):
    MONTHLY = 'monthly', 'Monthly'
    BIWEEKLY = 'biweekly', 'Bi-Weekly'
    WEEKLY = 'weekly', 'Weekly'


class ProrationMethod(models.TextChoices):
    CALENDAR_DAYS = 'calendar_days', 'Calendar Days Basis'
    WORKING_DAYS = 'working_days', 'Working Days Basis'
    NONE = 'none', 'No Proration'


class RoundingRule(models.TextChoices):
    NEAREST_INTEGER = 'nearest_integer', 'Round to Nearest Integer (Half Up)'
    ROUND_UP = 'round_up', 'Round Up (Ceiling)'
    ROUND_DOWN = 'round_down', 'Round Down (Floor)'
    TWO_DECIMAL = 'two_decimal', 'Preserve Two Decimals'


class NegativeNetPayPolicy(models.TextChoices):
    ZERO_OUT = 'zero_out', 'Zero Out and Carry Forward'
    PREVENT_FINALIZE = 'prevent_finalize', 'Prevent Finalization if Negative'
    ALLOW_NEGATIVE = 'allow_negative', 'Allow Negative Net Pay'


class PayrollConfiguration(TenantBaseModel):
    version = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True, db_index=True)
    is_archived = models.BooleanField(default=False, db_index=True)

    currency = models.CharField(max_length=10, default='BDT')
    pay_frequency = models.CharField(
        max_length=20,
        choices=PayFrequency.choices,
        default=PayFrequency.MONTHLY
    )
    payroll_period_type = models.CharField(
        max_length=30,
        choices=PayrollPeriodType.choices,
        default=PayrollPeriodType.CALENDAR_MONTH
    )
    cutoff_day = models.PositiveIntegerField(
        default=25,
        validators=[MinValueValidator(1), MaxValueValidator(31)],
        help_text="Day of the month for attendance cutoff (1-31)"
    )
    payment_day = models.PositiveIntegerField(
        default=30,
        validators=[MinValueValidator(1), MaxValueValidator(31)],
        help_text="Day of the month for salary disbursement (1-31)"
    )
    working_day_basis = models.CharField(
        max_length=20,
        choices=AbsenceDivisorMode.choices,
        default=AbsenceDivisorMode.FIXED_30,
        help_text="Divisor basis for salary absence and proration calculations"
    )
    attendance_source = models.CharField(
        max_length=100,
        default='attendance.AttendanceRecord',
        help_text="Data source identifier for employee attendance logs"
    )
    leave_source = models.CharField(
        max_length=100,
        default='leave.LeaveRequest',
        help_text="Data source identifier for approved employee leaves"
    )
    overtime_source = models.CharField(
        max_length=100,
        default='attendance.AttendanceRecord',
        help_text="Data source identifier for approved employee overtime"
    )
    overtime_multiplier = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        default=Decimal('1.50')
    )
    proration_method = models.CharField(
        max_length=30,
        choices=ProrationMethod.choices,
        default=ProrationMethod.CALENDAR_DAYS
    )
    rounding_rule = models.CharField(
        max_length=30,
        choices=RoundingRule.choices,
        default=RoundingRule.NEAREST_INTEGER
    )
    negative_net_pay_policy = models.CharField(
        max_length=30,
        choices=NegativeNetPayPolicy.choices,
        default=NegativeNetPayPolicy.ZERO_OUT
    )
    approval_workflow_steps = models.PositiveIntegerField(
        default=2,
        choices=[(1, 'Single-Step (Direct Approve)'), (2, 'Two-Step (Review then Approve)')],
        help_text="Number of workflow approval steps required"
    )
    payslip_prefix = models.CharField(
        max_length=20,
        default='PSL-',
        help_text="Prefix used in generated payslip numbers"
    )
    supported_payment_methods = models.JSONField(
        default=list,
        help_text="Supported payment methods, e.g. ['bank', 'cash', 'split']"
    )
    effective_from = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='payroll_configs_created'
    )
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='payroll_configs_archived'
    )

    class Meta:
        ordering = ['-version']
        unique_together = ('tenant', 'version')
        indexes = [
            models.Index(fields=['tenant', 'is_active']),
            models.Index(fields=['tenant', 'version']),
        ]

    def __str__(self):
        status = 'Active' if self.is_active else ('Archived' if self.is_archived else 'Inactive')
        tenant_name = self.tenant.name if getattr(self, 'tenant_id', None) else 'No Tenant'
        return f"Payroll Config v{self.version} ({tenant_name}) - {status}"

    def clean(self):
        if not self.supported_payment_methods:
            self.supported_payment_methods = ['bank', 'cash', 'split']

    def delete(self, *args, **kwargs):
        if self.payroll_runs.exists():
            raise ValidationError("Cannot hard-delete a payroll configuration referenced by payroll runs. Archive it instead.")
        super().delete(*args, **kwargs)

    def to_snapshot(self) -> dict:
        return {
            'id': self.id,
            'tenant_id': self.tenant_id,
            'tenant_name': self.tenant.name if self.tenant_id else '',
            'version': self.version,
            'is_active': self.is_active,
            'is_archived': self.is_archived,
            'currency': self.currency,
            'pay_frequency': self.pay_frequency,
            'payroll_period_type': self.payroll_period_type,
            'cutoff_day': self.cutoff_day,
            'payment_day': self.payment_day,
            'working_day_basis': self.working_day_basis,
            'attendance_source': self.attendance_source,
            'leave_source': self.leave_source,
            'overtime_source': self.overtime_source,
            'overtime_multiplier': str(self.overtime_multiplier),
            'proration_method': self.proration_method,
            'rounding_rule': self.rounding_rule,
            'negative_net_pay_policy': self.negative_net_pay_policy,
            'approval_workflow_steps': self.approval_workflow_steps,
            'payslip_prefix': self.payslip_prefix,
            'supported_payment_methods': list(self.supported_payment_methods or ['bank', 'cash', 'split', 'mfs']),
            'effective_from': str(self.effective_from) if self.effective_from else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class PaymentType(models.TextChoices):
    BANK = 'bank', 'Bank Transfer'
    CASH = 'cash', 'Cash'
    MFS = 'mfs', 'Mobile Financial Service'
    SPLIT = 'split', 'Multiple Methods (Bank & MFS)'


class MFSProvider(models.TextChoices):
    BKASH = 'bkash', 'bKash'
    NAGAD = 'nagad', 'Nagad'
    ROCKET = 'rocket', 'Rocket'


class PayrollPaymentDestination(TenantBaseModel):
    employee = models.OneToOneField(
        'employees.Employee',
        on_delete=models.CASCADE,
        related_name='payment_destination',
        db_index=True
    )
    payment_type = models.CharField(
        max_length=20,
        choices=PaymentType.choices,
        default=PaymentType.BANK,
        db_index=True
    )

    # Bank Transfer fields
    bank = models.ForeignKey(
        'employees.Bank',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='payroll_destinations'
    )
    bank_name = models.CharField(max_length=150, blank=True)
    branch = models.ForeignKey(
        'employees.BankBranch',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='payroll_destinations'
    )
    branch_name = models.CharField(max_length=150, blank=True)
    account_holder_name = models.CharField(max_length=255, blank=True)
    account_number_encrypted = models.TextField(blank=True, help_text="Encrypted bank account number")
    account_number_last4 = models.CharField(max_length=4, blank=True, db_index=True)
    routing_number = models.CharField(max_length=9, blank=True, db_index=True)

    # Mobile Financial Service (MFS) fields
    mfs_provider = models.CharField(
        max_length=20,
        choices=MFSProvider.choices,
        blank=True,
        db_index=True
    )
    wallet_number_encrypted = models.TextField(blank=True, help_text="Encrypted mobile wallet number")
    wallet_number_last4 = models.CharField(max_length=4, blank=True, db_index=True)

    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = 'Payroll Payment Destination'
        verbose_name_plural = 'Payroll Payment Destinations'
        indexes = [
            models.Index(fields=['tenant', 'payment_type']),
            models.Index(fields=['tenant', 'is_active']),
        ]

    def __str__(self):
        emp_num = self.employee.employee_number if self.employee else 'Unknown'
        return f"{emp_num} - {self.get_payment_type_display()}"

    def set_account_number(self, raw_number: str):
        from apps.employees.bank_crypto import encrypt_account_number, normalize_account_number
        cleaned = normalize_account_number(raw_number)
        if not cleaned:
            self.account_number_encrypted = ''
            self.account_number_last4 = ''
            return
        self.account_number_encrypted = encrypt_account_number(cleaned)
        self.account_number_last4 = cleaned[-4:] if len(cleaned) >= 4 else cleaned

    def get_account_number(self) -> str:
        from apps.employees.bank_crypto import decrypt_account_number
        if not self.account_number_encrypted:
            return ''
        return decrypt_account_number(self.account_number_encrypted)

    @property
    def masked_account_number(self) -> str:
        if self.account_number_last4:
            return f"**********{self.account_number_last4}"
        return ""

    def set_wallet_number(self, raw_wallet: str):
        import re
        from apps.employees.bank_crypto import encrypt_account_number, normalize_account_number
        cleaned = normalize_account_number(raw_wallet)
        if not cleaned:
            self.wallet_number_encrypted = ''
            self.wallet_number_last4 = ''
            return
        if not re.match(r"^01[3-9]\d{8,9}$", cleaned):
            raise ValidationError({"wallet_number": "Invalid Bangladeshi wallet number. Must start with 01 and have 11 digits (or 12 for Rocket)."})
        self.wallet_number_encrypted = encrypt_account_number(cleaned)
        self.wallet_number_last4 = cleaned[-4:] if len(cleaned) >= 4 else cleaned

    def get_wallet_number(self) -> str:
        from apps.employees.bank_crypto import decrypt_account_number
        if not self.wallet_number_encrypted:
            return ''
        return decrypt_account_number(self.wallet_number_encrypted)

    @property
    def masked_account(self) -> str:
        if self.payment_type == PaymentType.BANK and self.account_number_last4:
            return f"••••{self.account_number_last4}"
        elif self.payment_type == PaymentType.MFS and self.wallet_number_last4:
            return f"••••{self.wallet_number_last4}"
        elif self.payment_type == PaymentType.SPLIT:
            parts = []
            if self.account_number_last4:
                parts.append(f"Bank: ••••{self.account_number_last4}")
            if self.wallet_number_last4:
                parts.append(f"MFS: ••••{self.wallet_number_last4}")
            return " | ".join(parts) or "Multiple"
        return ""

    @property
    def masked_wallet_number(self) -> str:
        if self.wallet_number_last4:
            return f"*******{self.wallet_number_last4}"
        return ""

    @property
    def account_number(self) -> str:
        return self.get_account_number()

    @property
    def account_holder(self) -> str:
        return self.account_holder_name

    @property
    def mfs_wallet_number(self) -> str:
        return self.get_wallet_number()

    def get_masked_destination(self) -> dict:
        data = {
            'payment_type': self.payment_type,
            'payment_type_display': self.get_payment_type_display(),
            'is_active': self.is_active,
            'masked_account': self.masked_account,
        }
        if self.payment_type == PaymentType.BANK:
            data.update({
                'bank_name': self.bank_name or (self.bank.name if self.bank else ''),
                'branch_name': self.branch_name or (self.branch.name if self.branch else ''),
                'account_holder_name': self.account_holder_name,
                'account_number_masked': self.masked_account_number,
                'account_number_last4': self.account_number_last4,
                'routing_number': self.routing_number,
            })
        elif self.payment_type == PaymentType.MFS:
            data.update({
                'mfs_provider': self.mfs_provider,
                'mfs_provider_display': self.get_mfs_provider_display(),
                'wallet_number_masked': self.masked_wallet_number,
                'wallet_number_last4': self.wallet_number_last4,
            })
        elif self.payment_type == PaymentType.SPLIT:
            data.update({
                'bank_name': self.bank_name or (self.bank.name if self.bank else ''),
                'branch_name': self.branch_name or (self.branch.name if self.branch else ''),
                'account_holder_name': self.account_holder_name,
                'account_number_masked': self.masked_account_number,
                'account_number_last4': self.account_number_last4,
                'routing_number': self.routing_number,
                'mfs_provider': self.mfs_provider,
                'mfs_provider_display': self.get_mfs_provider_display() if self.mfs_provider else '',
                'wallet_number_masked': self.masked_wallet_number,
                'wallet_number_last4': self.wallet_number_last4,
            })
        elif self.payment_type == PaymentType.CASH:
            data.update({
                'disbursement': 'Cash / Physical voucher',
            })
        return data

    def to_snapshot(self) -> dict:
        snapshot = self.get_masked_destination()
        snapshot['snapshotted_at'] = timezone.now().isoformat()
        return snapshot

    def clean(self):
        super().clean()
        if self.payment_type == 'mobile':
            self.payment_type = PaymentType.MFS

        if self.payment_type == PaymentType.BANK:
            # Clear obsolete MFS data atomically
            self.mfs_provider = ''
            self.wallet_number_encrypted = ''
            self.wallet_number_last4 = ''

            # Validate Bank fields
            if not self.bank and not self.bank_name:
                raise ValidationError({"bank": "Bank is required for bank transfer destinations."})
            if not self.account_holder_name:
                raise ValidationError({"account_holder_name": "Account holder name is required for bank transfer destinations."})
            if not self.account_number_encrypted:
                raise ValidationError({"account_number": "Account number is required for bank transfer destinations."})
            if not self.branch and not self.branch_name:
                raise ValidationError({"branch": "Branch is required for bank transfer destinations."})

            if self.branch and self.bank:
                if self.branch.bank_id != self.bank_id:
                    raise ValidationError({"branch": "Selected branch does not belong to the selected bank."})
                if not self.routing_number:
                    self.routing_number = self.branch.routing_number
                if not self.bank_name:
                    self.bank_name = self.bank.name
                if not self.branch_name:
                    self.branch_name = self.branch.name

        elif self.payment_type == PaymentType.MFS:
            # Clear obsolete Bank data atomically
            self.bank = None
            self.bank_name = ''
            self.branch = None
            self.branch_name = ''
            self.account_holder_name = ''
            self.account_number_encrypted = ''
            self.account_number_last4 = ''
            self.routing_number = ''

            # Validate MFS fields
            if not self.mfs_provider:
                raise ValidationError({"mfs_provider": "MFS provider (bKash, Nagad, or Rocket) is required."})
            if self.mfs_provider not in MFSProvider.values:
                raise ValidationError({"mfs_provider": f"Invalid MFS provider. Choose from: {', '.join(MFSProvider.labels)}."})
            if not self.wallet_number_encrypted:
                raise ValidationError({"wallet_number": "Valid wallet number is required for Mobile Financial Service."})

        elif self.payment_type == PaymentType.SPLIT:
            # Multi-method / Split payment retains both Bank and MFS data
            if self.branch and self.bank:
                if self.branch.bank_id != self.bank_id:
                    raise ValidationError({"branch": "Selected branch does not belong to the selected bank."})
                if not self.routing_number:
                    self.routing_number = self.branch.routing_number
                if not self.bank_name:
                    self.bank_name = self.bank.name
                if not self.branch_name:
                    self.branch_name = self.branch.name

        elif self.payment_type == PaymentType.CASH:
            # Clear both Bank and MFS data atomically
            self.bank = None
            self.bank_name = ''
            self.branch = None
            self.branch_name = ''
            self.account_holder_name = ''
            self.account_number_encrypted = ''
            self.account_number_last4 = ''
            self.routing_number = ''
            self.mfs_provider = ''
            self.wallet_number_encrypted = ''
            self.wallet_number_last4 = ''


