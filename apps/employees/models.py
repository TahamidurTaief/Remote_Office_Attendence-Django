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

    @property
    def canonical_designation(self):
        if self.master_employee_id and self.master_employee.designation:
            return self.master_employee.designation.name
        return self.designation

    @property
    def canonical_department(self):
        if self.master_employee_id and self.master_employee.department:
            return self.master_employee.department.name
        return self.department

    @property
    def canonical_branch(self):
        if self.master_employee_id and self.master_employee.branch:
            return self.master_employee.branch
        return self.branch

    @property
    def canonical_phone(self):
        if self.master_employee_id and self.master_employee.phone:
            return self.master_employee.phone
        return self.phone

    @property
    def canonical_full_name(self):
        if self.master_employee_id:
            return f"{self.master_employee.first_name} {self.master_employee.last_name}".strip()
        return self.full_name

    @property
    def canonical_is_active(self):
        if self.master_employee_id:
            return self.master_employee.status == 'active' and not self.master_employee.is_suspended
        return self.is_active

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
    is_verified = models.BooleanField(default=False)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='verified_documents'
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    is_archived = models.BooleanField(default=False)

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
    reassigned_to = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='reassigned_from')

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
    SUSPENDED = 'suspended', 'Suspended'
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

    EMPLOYMENT_TYPE_CHOICES = (
        ('full_time', 'Full Time'),
        ('part_time', 'Part Time'),
        ('contract', 'Contract'),
        ('intern', 'Intern'),
        ('probationary', 'Probationary'),
    )

    PAYMENT_METHOD_CHOICES = (
        ('bank', 'Bank Transfer'),
        ('cash', 'Cash'),
        ('mobile', 'Mobile Banking'),
    )

    DATA_SCOPE_CHOICES = (
        ('branch', 'Branch Level'),
        ('department', 'Department Level'),
        ('global', 'Global / Organization Wide'),
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
    emergency_contact_relation = models.CharField(max_length=50, blank=True)
    emergency_contact_phone = models.CharField(max_length=50, blank=True)
    emergency_contact_address = models.TextField(blank=True)

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
    employment_type = models.CharField(max_length=30, choices=EMPLOYMENT_TYPE_CHOICES, default='full_time', blank=True)
    joined_date = models.DateField(null=True, blank=True)
    shift = models.CharField(max_length=50, blank=True, default='Day Shift')
    weekly_holiday_policy = models.CharField(max_length=100, blank=True, default='Friday, Saturday')

    # Payroll fields
    basic_salary = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    salary_structure = models.CharField(max_length=100, blank=True, default='Standard Salary Structure')
    bank_name = models.CharField(max_length=100, blank=True)
    bank_account = models.CharField(max_length=50, blank=True)
    payment_method = models.CharField(max_length=30, choices=PAYMENT_METHOD_CHOICES, default='bank', blank=True)
    tax_profile = models.CharField(max_length=100, blank=True)
    pf_enabled = models.BooleanField(default=False)
    overtime_policy = models.CharField(max_length=50, blank=True, default='Standard Overtime (1.5x)')

    # Security & User Account
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='employee_master'
    )
    data_scope = models.CharField(max_length=30, choices=DATA_SCOPE_CHOICES, default='branch', blank=True)
    mfa_required = models.BooleanField(default=False)

    status = models.CharField(
        max_length=30, choices=EmployeeStatus.choices,
        default=EmployeeStatus.DRAFT, db_index=True
    )

    is_suspended = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def get_completion_percentage(self) -> int:
        score = 0
        # Step 1: Basic info
        if self.employee_number and self.first_name and self.last_name:
            score += 20
        # Step 2: Org info
        if self.department_id and self.designation_id and self.joined_date:
            score += 20
        # Step 3: Payroll
        if self.basic_salary is not None:
            score += 20
        # Step 4: Security user link
        if self.user_id:
            score += 20
        # Step 5-7: Documents/Emergency/Assets
        has_docs = self.documents.filter(is_active=True).exists()
        has_emergency = bool(self.emergency_contact_name and self.emergency_contact_phone)
        has_assets = self.asset_assignments.filter(returned_date__isnull=True).exists()
        if has_docs or has_emergency or has_assets:
            score += 20
        return score

    def get_next_wizard_step(self) -> int:
        if not (self.employee_number and self.first_name and self.last_name):
            return 1
        if not (self.department_id and self.designation_id and self.joined_date):
            return 2
        if self.basic_salary is None:
            return 3
        if not self.user_id:
            return 4
        if not self.documents.filter(is_active=True).exists():
            return 5
        if not (self.emergency_contact_name and self.emergency_contact_phone):
            return 6
        if not self.asset_assignments.filter(returned_date__isnull=True).exists():
            return 7
        return 8

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

    @property
    def canonical_designation(self):
        return self.designation.name if self.designation else ''

    @property
    def canonical_department(self):
        return self.department.name if self.department else ''

    @property
    def canonical_branch(self):
        return self.branch

    @property
    def canonical_phone(self):
        return self.phone

    @property
    def canonical_full_name(self):
        return self.get_full_name()

    @property
    def canonical_is_active(self):
        return self.status == 'active' and not self.is_suspended

    @property
    def business_status(self) -> str:
        if self.status == EmployeeStatus.ARCHIVED:
            return 'archived'
        if self.status == EmployeeStatus.TERMINATED:
            return 'terminated'
        if self.status == EmployeeStatus.RESIGNED:
            return 'notice_period'
        if self.status == EmployeeStatus.SUSPENDED:
            return 'suspended'
        if self.status == EmployeeStatus.PROBATION:
            return 'on_probation'
        
        today = timezone.localdate()
        profile = getattr(self, 'legacy_profile', None)
        if profile and profile.leave_requests.filter(status='approved', start_date__lte=today, end_date__gte=today).exists():
            return 'on_leave'
            
        if self.status in (EmployeeStatus.DRAFT, EmployeeStatus.PENDING_APPROVAL):
            return 'inactive'
            
        return 'active'

    @property
    def business_status_display(self) -> str:
        choices = {
            'active': 'Active',
            'inactive': 'Inactive',
            'suspended': 'Suspended',
            'on_leave': 'On Leave',
            'on_probation': 'On Probation',
            'notice_period': 'Notice Period',
            'resigned': 'Resigned',
            'terminated': 'Terminated',
            'archived': 'Archived',
        }
        return choices.get(self.business_status, self.status.capitalize())

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
                db_record = Employee.objects.filter(pk=self.pk).values('status', 'is_suspended').first()
                db_status = db_record['status']
                db_is_suspended = db_record['is_suspended']
            except Exception:
                db_status = None
                db_is_suspended = False

            if db_status == EmployeeStatus.ARCHIVED:
                raise ValidationError("Archived employees are read-only and cannot be modified.")

            # Bidirectional sync based on which field changed
            if self.is_suspended != db_is_suspended:
                if self.is_suspended:
                    self.status = EmployeeStatus.SUSPENDED
                else:
                    self.status = EmployeeStatus.ACTIVE
            elif self.status != db_status:
                if self.status == EmployeeStatus.SUSPENDED:
                    self.is_suspended = True
                elif db_status == EmployeeStatus.SUSPENDED:
                    self.is_suspended = False

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
            db_status = Employee.objects.filter(pk=self.pk).values_list('status', flat=True).first()
            if db_status != EmployeeStatus.ARCHIVED:
                Employee.objects.filter(pk=self.pk).update(status=EmployeeStatus.ARCHIVED, updated_at=timezone.now())
                EmployeeActivityLog.objects.create(
                    employee=self,
                    action_description=f"Transitioned status from '{db_status}' to 'archived' via delete",
                    field_changed='status'
                )
                EmployeeAuditLog.objects.create(
                    employee=self,
                    old_value={'status': db_status},
                    new_value={'status': EmployeeStatus.ARCHIVED}
                )
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


class EmployeeActivityLog(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='activity_logs')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    action_description = models.TextField()
    field_changed = models.CharField(max_length=100, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.employee.get_full_name()} - {self.action_description} by {self.actor} ({self.timestamp})"


class EmployeeAuditLog(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='audit_logs')
    old_value = models.JSONField(default=dict, blank=True)
    new_value = models.JSONField(default=dict, blank=True)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"Audit log for {self.employee.get_full_name()} on {self.timestamp}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("EmployeeAuditLog records are immutable and cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("EmployeeAuditLog records are immutable and cannot be deleted.")


class ManagerDelegation(models.Model):
    manager = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='delegations_made')
    delegate_to = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='delegations_received')
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-start_date']

    def clean(self):
        super().clean()
        if self.manager_id == self.delegate_to_id:
            raise ValidationError("You cannot delegate tasks to yourself.")
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValidationError("Start date must be before or equal to end date.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.manager.get_full_name()} -> {self.delegate_to.get_full_name()} ({self.start_date} to {self.end_date})"


