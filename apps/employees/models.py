import uuid
from datetime import timedelta
from django.db import models
from django.conf import settings
from django.utils import timezone
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
    master_employee = models.OneToOneField('Employee', on_delete=models.SET_NULL, null=True, blank=True, related_name='legacy_profile')
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


from datetime import timedelta

class DocumentType(models.TextChoices):
    NID = 'nid', 'National ID (NID)'
    PASSPORT = 'passport', 'Passport'
    PHOTO = 'photo', 'Profile Photo'
    RESUME = 'resume', 'Resume / CV'
    APPOINTMENT_LETTER = 'appointment_letter', 'Appointment Letter'
    CONTRACT = 'contract', 'Employment Contract'
    EDUCATION = 'education', 'Education Certificate'
    CERTIFICATE = 'certificate', 'Professional Certificate'
    MEDICAL = 'medical', 'Medical Report'
    POLICE_CLEARANCE = 'police_clearance', 'Police Clearance'
    OTHER = 'other', 'Other Document'

SENSITIVE_DOCUMENT_TYPES = [
    DocumentType.NID,
    DocumentType.PASSPORT,
    DocumentType.MEDICAL,
    DocumentType.POLICE_CLEARANCE,
]

class EmployeeDocument(models.Model):
    employee_master = models.ForeignKey(
        'Employee',
        on_delete=models.CASCADE,
        related_name='documents',
        null=True, blank=True
    )
    employee = models.ForeignKey(
        EmployeeProfile,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='documents'
    )
    document_type = models.CharField(max_length=50, choices=DocumentType.choices, default=DocumentType.OTHER)
    title = models.CharField(max_length=255, blank=True)
    file = models.FileField(upload_to='employees/documents/', null=True, blank=True)
    version = models.IntegerField(default=1)
    expiry_date = models.DateField(null=True, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-is_active', '-version', '-uploaded_at']

    def is_expiring_soon(self, days=30):
        if not self.expiry_date:
            return False
        today = timezone.localdate()
        return today <= self.expiry_date <= (today + timedelta(days=days))

    def is_expired(self):
        if not self.expiry_date:
            return False
        return self.expiry_date < timezone.localdate()

    def is_sensitive(self):
        return self.document_type in SENSITIVE_DOCUMENT_TYPES

    def save(self, *args, **kwargs):
        if not self.pk and self.employee_master:
            previous_docs = EmployeeDocument.objects.filter(
                employee_master=self.employee_master,
                document_type=self.document_type
            )
            if previous_docs.exists():
                max_ver = previous_docs.order_by('-version').first().version
                self.version = max_ver + 1
                previous_docs.update(is_active=False)
        super().save(*args, **kwargs)

    def __str__(self):
        emp_name = self.employee_master.get_full_name() if self.employee_master else (self.employee.full_name if self.employee else 'Unknown')
        return f"{emp_name} - {self.get_document_type_display()} v{self.version}"


class DocumentDownloadLog(models.Model):
    document = models.ForeignKey(EmployeeDocument, on_delete=models.CASCADE, related_name='download_logs')
    downloaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    downloaded_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ['-downloaded_at']


class AssetType(models.TextChoices):
    LAPTOP = 'laptop', 'Laptop'
    MOBILE = 'mobile', 'Mobile Phone'
    SIM = 'sim', 'SIM Card'
    TABLET = 'tablet', 'Tablet'
    TOKEN = 'token', 'Security Token / Dongle'
    OTHER = 'other', 'Other Asset'


class AssetCondition(models.TextChoices):
    NEW = 'new', 'Brand New'
    GOOD = 'good', 'Good'
    FAIR = 'fair', 'Fair'
    DAMAGED = 'damaged', 'Damaged / Needs Repair'


class Asset(models.Model):
    asset_type = models.CharField(max_length=30, choices=AssetType.choices)
    asset_tag = models.CharField(max_length=50, unique=True, db_index=True)
    name = models.CharField(max_length=150)
    serial_number = models.CharField(max_length=100, blank=True)
    condition = models.CharField(max_length=30, choices=AssetCondition.choices, default=AssetCondition.GOOD)
    warranty_expiry = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['asset_tag']

    def current_assignment(self):
        return self.assignments.filter(returned_date__isnull=True).select_related('employee').first()

    def is_assigned(self):
        return self.assignments.filter(returned_date__isnull=True).exists()

    def __str__(self):
        return f"{self.asset_tag} - {self.name} ({self.get_asset_type_display()})"


class AssetAssignment(models.Model):
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name='assignments')
    employee = models.ForeignKey('Employee', on_delete=models.CASCADE, related_name='asset_assignments')
    assigned_date = models.DateField(default=timezone.now)
    returned_date = models.DateField(null=True, blank=True)
    condition_at_assignment = models.CharField(max_length=30, choices=AssetCondition.choices, default=AssetCondition.GOOD)
    condition_at_return = models.CharField(max_length=30, choices=AssetCondition.choices, null=True, blank=True)
    notes = models.TextField(blank=True)
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['-assigned_date']

    def clean(self):
        super().clean()
        if not self.returned_date:
            active_qs = AssetAssignment.objects.filter(asset=self.asset, returned_date__isnull=True)
            if self.pk:
                active_qs = active_qs.exclude(pk=self.pk)
            if active_qs.exists():
                curr_assign = active_qs.first()
                raise ValidationError(f"Asset '{self.asset.asset_tag}' is currently assigned to {curr_assign.employee.get_full_name()}. Must return first.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        status = f"Returned {self.returned_date}" if self.returned_date else "Active"
        return f"{self.asset.asset_tag} -> {self.employee.get_full_name()} ({status})"


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

    # ── Lifecycle helpers ────────────────────────────────────────────────────
    def can_transition_to(self, target_status: str) -> bool:
        """True if moving from current status to target_status is allowed."""
        from apps.employees.lifecycle import is_valid_transition
        return is_valid_transition(self.status, target_status)

    def get_allowed_transitions(self) -> list:
        """List of valid to-status strings from current status."""
        from apps.employees.lifecycle import get_allowed_targets
        return sorted(get_allowed_targets(self.status))

    # ── Validation ───────────────────────────────────────────────────────────
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
        # Enforce transition map when status changes on existing records.
        # Skip on new records (pk is None) — initial status is always allowed.
        if self.pk:
            try:
                db_status = Employee.objects.filter(pk=self.pk).values_list('status', flat=True).first()
            except Exception:
                db_status = None
            if db_status and db_status != self.status:
                from apps.employees.lifecycle import is_valid_transition, describe_allowed
                if not is_valid_transition(db_status, self.status):
                    allowed = describe_allowed(db_status)
                    raise ValidationError({
                        'status': (
                            f"Invalid transition: '{db_status}' → '{self.status}'. "
                            f"Allowed next states from '{db_status}': {allowed}."
                        )
                    })

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.pk:
            Employee.objects.filter(pk=self.pk).update(status=EmployeeStatus.ARCHIVED, updated_at=timezone.now())
            self.status = EmployeeStatus.ARCHIVED


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


class LifecycleTransitionRequest(models.Model):
    """
    Queued status-change request requiring admin approval (HIGH_RISK transitions).
    Status is NOT changed on Employee until an admin approves.
    """

    class ReviewStatus(models.TextChoices):
        PENDING  = 'pending',  'Pending'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'

    employee        = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name='lifecycle_requests'
    )
    from_status     = models.CharField(max_length=30)
    to_status       = models.CharField(max_length=30)
    reason          = models.TextField()
    # Optional org-change fields bundled with Promote / Transfer requests
    new_department  = models.ForeignKey(
        Department, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+'
    )
    new_designation = models.ForeignKey(
        Designation, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+'
    )
    requested_by    = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='lifecycle_requests_made'
    )
    requested_at    = models.DateTimeField(auto_now_add=True)
    review_status   = models.CharField(
        max_length=20, choices=ReviewStatus.choices, default=ReviewStatus.PENDING,
        db_index=True
    )
    reviewed_by     = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='lifecycle_requests_reviewed'
    )
    reviewed_at     = models.DateTimeField(null=True, blank=True)
    review_note     = models.TextField(blank=True)
    effective_date  = models.DateField(default=timezone.now)

    class Meta:
        db_table = 'lifecycle_transition_request'
        ordering = ['-requested_at']
        indexes = [
            models.Index(fields=['review_status']),
            models.Index(fields=['employee', 'review_status']),
        ]

    def __str__(self):
        return (
            f"[{self.review_status}] {self.employee.get_full_name()} "
            f"{self.from_status} → {self.to_status}"
        )

    def is_pending(self):
        return self.review_status == self.ReviewStatus.PENDING

