import uuid
from django.db import models
from django.conf import settings
from apps.branches.models import Branch
from imagekit.models import ProcessedImageField
from imagekit.processors import ResizeToFit, Transpose

class EmployeeProfile(models.Model):
    TRACKING_INTERVAL_CHOICES = (
        (0,  'Disabled'),
        (5,  'Every 5 minutes'),
        (10, 'Every 10 minutes'),
        (15, 'Every 15 minutes'),
        (30, 'Every 30 minutes'),
        (60, 'Every 1 hour'),
        (90, 'Every 1.5 hours'),
        (120, 'Every 2 hours'),
    )

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='employee_profile')
    branch = models.ForeignKey(Branch, on_delete=models.SET_NULL, null=True, blank=True, related_name='employees')
    employee_id = models.CharField(max_length=50, unique=True)
    full_name = models.CharField(max_length=255)
    department = models.CharField(max_length=100, null=True, blank=True)
    designation = models.CharField(max_length=100, null=True, blank=True)
    phone = models.CharField(max_length=20, unique=True)
    emergency_contact = models.CharField(max_length=20, null=True, blank=True)
    profile_photo = ProcessedImageField(
        upload_to='employees/photos/',
        processors=[
            Transpose(),
            ResizeToFit(400, 400)
        ],
        format='WEBP',
        options={'quality': 82},
        null=True,
        blank=True
    )
    is_active = models.BooleanField(default=True)
    joined_date = models.DateField()
    # Admin sets how often (in minutes) to auto-sync this employee's location.
    # 0 = disabled.
    tracking_interval = models.IntegerField(
        choices=TRACKING_INTERVAL_CHOICES,
        default=0,
        help_text='How often (minutes) to auto-sync location when employee is checked in. 0 = disabled.'
    )
    overtime_enabled = models.BooleanField(
        default=False,
        help_text='Enable overtime tracking for this employee'
    )
    is_project_manager = models.BooleanField(
        default=False,
        help_text='Allow this employee to work as a Project Manager and manage projects'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        permissions = [
            ('view_reports', 'Can view analytical reports'),
        ]

    def __str__(self):
        return f"{self.employee_id} - {self.full_name}"


class EmployeeLocationSync(models.Model):
    """Stores periodic background location pings from an employee's device."""
    employee = models.ForeignKey(
        EmployeeProfile, on_delete=models.CASCADE, related_name='location_syncs'
    )
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    accuracy = models.FloatField(default=0)
    address = models.CharField(max_length=500, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    
    sync_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    client_event_time = models.DateTimeField(null=True, blank=True)
    synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.employee.full_name} @ {self.timestamp:%Y-%m-%d %H:%M}"


class EmployeeLeaveRule(models.Model):
    employee = models.ForeignKey(EmployeeProfile, on_delete=models.CASCADE, related_name='leave_rules')
    leave_type = models.ForeignKey('leave.LeaveType', on_delete=models.CASCADE)
    days_per_year = models.IntegerField()

    class Meta:
        unique_together = ('employee', 'leave_type')

    def __str__(self):
        return f"{self.employee.full_name} - {self.leave_type.name}: {self.days_per_year} days"


class EmployeeDocument(models.Model):
    employee = models.ForeignKey(
        EmployeeProfile,
        on_delete=models.CASCADE,
        related_name='documents'
    )
    document_type = models.CharField(max_length=100)
    expiry_date = models.DateField()
    file = models.FileField(
        upload_to='employees/documents/',
        null=True,
        blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['expiry_date']

    def __str__(self):
        return f"{self.employee.full_name} - {self.document_type} (Expires: {self.expiry_date})"


from django.core.exceptions import ValidationError
from django.utils import timezone


class Department(models.Model):
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=20, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.code})"


class Designation(models.Model):
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=20, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.code})"


class EmployeeStatus(models.TextChoices):
    DRAFT = 'draft', 'Draft'
    PENDING_APPROVAL = 'pending_approval', 'Pending Approval'
    ACTIVE = 'active', 'Active'
    PROBATION = 'probation', 'Probation'
    CONFIRMED = 'confirmed', 'Confirmed'
    TRANSFERRED = 'transferred', 'Transferred'
    PROMOTED = 'promoted', 'Promoted'
    DEMOTED = 'demoted', 'Demoted'
    NOTICE_PERIOD = 'notice_period', 'Notice Period'
    RESIGNED = 'resigned', 'Resigned'
    TERMINATED = 'terminated', 'Terminated'
    RETIRED = 'retired', 'Retired'
    ARCHIVED = 'archived', 'Archived'


ALLOWED_LOGIN_STATUSES = [
    EmployeeStatus.ACTIVE,
    EmployeeStatus.PROBATION,
    EmployeeStatus.CONFIRMED,
    EmployeeStatus.TRANSFERRED,
    EmployeeStatus.PROMOTED,
    EmployeeStatus.DEMOTED,
    EmployeeStatus.NOTICE_PERIOD,
]



class Employee(models.Model):
    GENDER_CHOICES = (
        ('male', 'Male'),
        ('female', 'Female'),
        ('other', 'Other'),
    )

    employee_number = models.CharField(max_length=50, unique=True, db_index=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    dob = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=20, choices=GENDER_CHOICES, blank=True)
    national_id = models.CharField(max_length=100, blank=True, help_text="National ID reference link")

    phone = models.CharField(max_length=30, blank=True)
    personal_email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    emergency_contact_name = models.CharField(max_length=255, blank=True)
    emergency_contact_phone = models.CharField(max_length=50, blank=True)

    branch = models.ForeignKey(
        Branch, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='master_employees', db_index=True
    )
    department = models.ForeignKey(
        Department, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='employees', db_index=True
    )
    designation = models.ForeignKey(
        Designation, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='employees', db_index=True
    )
    reporting_manager = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='direct_reports', db_index=True
    )

    status = models.CharField(
        max_length=30, choices=EmployeeStatus.choices,
        default=EmployeeStatus.DRAFT, db_index=True
    )
    joined_date = models.DateField(null=True, blank=True)

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='employee_master'
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['employee_number']
        indexes = [
            models.Index(fields=['employee_number']),
            models.Index(fields=['status']),
            models.Index(fields=['department']),
            models.Index(fields=['designation']),
            models.Index(fields=['reporting_manager']),
            models.Index(fields=['branch']),
        ]

    def get_full_name(self):
        full = f"{self.first_name} {self.last_name}".strip()
        return full or self.employee_number

    def __str__(self):
        return f"{self.employee_number} - {self.get_full_name()}"

    def is_login_allowed(self):
        return self.status in ALLOWED_LOGIN_STATUSES

    def check_circular_reporting(self):
        if not self.reporting_manager:
            return
        if self.pk and self.reporting_manager_id == self.pk:
            raise ValidationError({'reporting_manager': "An employee cannot report to themselves."})
        curr = self.reporting_manager
        visited = {self.pk} if self.pk else set()
        while curr:
            if curr.pk in visited:
                raise ValidationError({'reporting_manager': f"Circular reporting structure detected involving {curr.get_full_name()}."})
            if self.pk:
                visited.add(curr.pk)
            curr = curr.reporting_manager

    def clean(self):
        super().clean()
        self.check_circular_reporting()

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self.status = EmployeeStatus.ARCHIVED
        self.save(update_fields=['status', 'updated_at'])


class EmploymentHistory(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='employment_history')
    field_changed = models.CharField(max_length=100)
    old_value = models.TextField(blank=True)
    new_value = models.TextField(blank=True)
    reason = models.TextField(blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    effective_date = models.DateField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.employee.get_full_name()} - {self.field_changed} ({self.effective_date})"

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("EmploymentHistory records are immutable and cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("EmploymentHistory records are immutable and cannot be deleted.")

