import uuid
from django.db import models
from django.conf import settings
from apps.employees.models import EmployeeProfile
from apps.projects.models import Project

class Expense(models.Model):
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    )

    CATEGORY_CHOICES = (
        ('travel', 'Travel'),
        ('food', 'Food'),
        ('accommodation', 'Accommodation'),
        ('materials', 'Materials'),
        ('other', 'Other'),
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
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='other')
    description = models.TextField()
    attachment = models.FileField(upload_to='expenses/', null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
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

    def __str__(self):
        return f"{self.employee.full_name} - {self.category} ({self.amount}) - {self.status}"
