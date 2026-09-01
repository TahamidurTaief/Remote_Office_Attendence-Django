import uuid
from django.db import models
from django.conf import settings
from apps.employees.models import Employee
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

