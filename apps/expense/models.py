import uuid
from django.db import models
from django.conf import settings
from apps.employees.models import EmployeeProfile
from apps.projects.models import Project

class ExpenseCategory(models.Model):
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "Expense Categories"
        ordering = ['name']

    def __str__(self):
        return self.name

class Expense(models.Model):
    STATUS_CHOICES = (
        ('draft', 'Draft'),
        ('pending_manager', 'Pending Manager Approval'),
        ('pending_finance', 'Pending Finance Approval'),
        ('pending_accounts', 'Pending Accounts Approval'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('returned', 'Returned for Correction'),
        ('returned_by_manager', 'Returned by Manager'),
        ('returned_by_finance', 'Returned by Finance'),
    )

    employee = models.ForeignKey(
        EmployeeProfile,
        on_delete=models.CASCADE,
        related_name='expenses'
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='expenses'
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    category = models.ForeignKey(
        ExpenseCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='expenses'
    )
    description = models.TextField()
    attachment = models.FileField(upload_to='expenses/', null=True, blank=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='draft')
    requested_at = models.DateTimeField(auto_now_add=True)
    
    sync_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    client_event_time = models.DateTimeField(null=True, blank=True)
    synced_at = models.DateTimeField(null=True, blank=True)

    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_expenses'
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(null=True, blank=True)

    class Meta:
        ordering = ['-requested_at']
        permissions = [
            ('approve_expense', 'Can approve or reject expense requests'),
        ]
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['employee']),
            models.Index(fields=['category']),
            models.Index(fields=['requested_at']),
        ]

    def __str__(self):
        cat_name = self.category.name if self.category else "Uncategorized"
        return f"{self.employee.full_name} - {cat_name} ({self.amount}) - {self.status}"

class ExpenseReturnEvent(models.Model):
    expense = models.ForeignKey(Expense, on_delete=models.CASCADE, related_name='return_events')
    returned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    returned_from_status = models.CharField(max_length=50)
    reason = models.TextField()
    fields_to_correct = models.JSONField(default=list, blank=True)
    due_date = models.DateField(null=True, blank=True)
    attachment = models.FileField(upload_to='expenses/returns/', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

class ExpenseHistory(models.Model):
    expense = models.ForeignKey(Expense, on_delete=models.CASCADE, related_name='history')
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    category = models.ForeignKey(ExpenseCategory, on_delete=models.SET_NULL, null=True)
    description = models.TextField()
    attachment = models.FileField(upload_to='expenses/history/', null=True, blank=True)
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-changed_at']

