import uuid
from django.db import models
from django.conf import settings
from apps.employees.models import EmployeeProfile
from imagekit.models import ProcessedImageField
from imagekit.processors import ResizeToFit, Transpose


class Attendance(models.Model):
    """
    Represents a single attendance event for an employee on a given day.
    - attendance_type='check_in'   → a check-in session (has check_in_time, optionally check_out_time)
    - attendance_type='field_visit' → a standalone field visit
    Multiple check_in records per day are allowed (employee can check in/out multiple times).
    """
    TYPE_CHOICES = (
        ('office', 'Office'),
        ('field', 'Field'),
    )
    STATUS_CHOICES = (
        ('on_time', 'On Time'),
        ('late', 'Late'),
        ('absent', 'Absent'),
        ('holiday_attendance', 'Holiday Attendance'),
    )
    ATTENDANCE_TYPE_CHOICES = (
        ('check_in', 'Check In/Out Session'),
        ('field_visit', 'Field Visit'),
    )

    employee = models.ForeignKey(EmployeeProfile, on_delete=models.CASCADE, related_name='attendances')
    project = models.ForeignKey(
        'projects.Project',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='attendances'
    )
    date = models.DateField(db_index=True)
    check_in_time = models.DateTimeField(null=True, blank=True)
    check_out_time = models.DateTimeField(null=True, blank=True)
    type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='office')
    attendance_type = models.CharField(max_length=20, choices=ATTENDANCE_TYPE_CHOICES, default='check_in')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='on_time')
    note = models.TextField(blank=True)
    total_hours = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    visit_title = models.CharField(max_length=200, blank=True)
    client_name = models.CharField(max_length=200, blank=True)
    site_address = models.TextField(blank=True)
    photo = ProcessedImageField(
        upload_to='attendance/photos/%Y/%m/%d/',
        processors=[
            Transpose(),
            ResizeToFit(1280, 1280)
        ],
        format='WEBP',
        options={'quality': 80},
        null=True,
        blank=True
    )
    overtime_minutes = models.IntegerField(default=0)
    is_early_checkout = models.BooleanField(default=False)
    ot_status = models.CharField(
        max_length=20,
        choices=(
            ('none', 'No Overtime'),
            ('pending', 'Pending Approval'),
            ('approved', 'Approved'),
            ('rejected', 'Rejected'),
        ),
        default='none'
    )
    
    is_expired = models.BooleanField(
        default=False,
        db_index=True,
        help_text='Data older than 3 months'
    )
    expired_at = models.DateTimeField(
        null=True, blank=True,
        help_text='When this record was marked expired'
    )
    
    is_policy_exception = models.BooleanField(default=False)
    is_outside_geofence = models.BooleanField(
        default=False,
        db_index=True,
        help_text='Flagged when check-in occurred outside permitted geofence radius'
    )
    gps_quality = models.CharField(
        max_length=10,
        choices=[('good', 'Good'), ('poor', 'Poor'), ('missing', 'Missing')],
        default='good'
    )
    
    sync_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    client_event_time = models.DateTimeField(null=True, blank=True)
    synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-check_in_time']

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.employee.full_name} - {self.date} ({self.attendance_type})"

    @property
    def is_active_session(self):
        """True if employee is currently checked-in (no check_out yet)."""
        return self.attendance_type == 'check_in' and self.check_in_time and not self.check_out_time

    @property
    def total_daily_hours(self):
        """Sum of total_hours for all sessions of this employee on the same date."""
        sessions = Attendance.objects.filter(
            employee=self.employee,
            date=self.date,
            attendance_type='check_in',
            is_expired=False
        )
        import decimal
        total = decimal.Decimal('0.00')
        for s in sessions:
            if s.total_hours:
                total += s.total_hours
        return total



class AttendanceLocation(models.Model):
    EVENT_CHOICES = (
        ('check_in', 'Check In'),
        ('check_out', 'Check Out'),
        ('auto_track', 'Auto Track'),
    )

    attendance = models.ForeignKey(Attendance, on_delete=models.CASCADE, related_name='locations')
    event = models.CharField(max_length=20, choices=EVENT_CHOICES)
    is_expired = models.BooleanField(default=False)
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    address = models.CharField(max_length=255)
    accuracy = models.FloatField()
    event_photo = models.ImageField(upload_to='attendance/photos/%Y/%m/%d/', null=True, blank=True)
    timestamp = models.DateTimeField()
    
    sync_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    client_event_time = models.DateTimeField(null=True, blank=True)
    synced_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.attendance} - {self.event}"


class AttendanceAbsentLog(models.Model):
    employee = models.ForeignKey(
        EmployeeProfile, 
        on_delete=models.CASCADE, 
        related_name='absent_logs'
    )
    date = models.DateField()
    leave_type_deducted = models.ForeignKey(
        'leave.LeaveType', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('employee', 'date')

    def __str__(self):
        return f"{self.employee.full_name} - Absent on {self.date}"


def get_default_deduction_leave_type(employee=None):
    from apps.leave.models import LeaveType
    if employee:
        from apps.employees.models import EmployeeLeaveRule
        rule = EmployeeLeaveRule.objects.filter(employee=employee, leave_type__is_active=True, leave_type__is_default=True).select_related('leave_type').first()
        if not rule:
            rule = EmployeeLeaveRule.objects.filter(employee=employee, leave_type__is_active=True, leave_type__category='casual').select_related('leave_type').first()
        if not rule:
            rule = EmployeeLeaveRule.objects.filter(employee=employee, leave_type__is_active=True, leave_type__category='sick').select_related('leave_type').first()
        if not rule:
            rule = EmployeeLeaveRule.objects.filter(employee=employee, leave_type__is_active=True).select_related('leave_type').order_by('leave_type__id').first()
        if rule:
            return rule.leave_type

    leave_type = LeaveType.objects.filter(is_active=True, is_default=True).first()
    if not leave_type:
        leave_type = LeaveType.objects.filter(is_active=True, category='casual').first()
    if not leave_type:
        leave_type = LeaveType.objects.filter(is_active=True, category='sick').first()
    if not leave_type:
        leave_type = LeaveType.objects.filter(is_active=True).order_by('id').first()
    return leave_type


class SyncLog(models.Model):
    sync_batch_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    employee = models.ForeignKey(EmployeeProfile, on_delete=models.CASCADE, related_name='sync_logs')
    started_at = models.DateTimeField()
    completed_at = models.DateTimeField()
    records_total = models.PositiveIntegerField()
    records_success = models.PositiveIntegerField()
    records_failed = models.PositiveIntegerField()
    failure_reason = models.TextField(null=True, blank=True)

    def __str__(self):
        return f"SyncLog - {self.employee.full_name} - {self.sync_batch_id}"


class AttendancePolicy(models.Model):
    branch = models.OneToOneField(
        'branches.Branch',
        on_delete=models.CASCADE,
        related_name='attendance_policy',
        null=True,
        blank=True,
        help_text="If null, this is the global default policy"
    )
    photo_required = models.BooleanField(default=True)
    gps_required = models.CharField(
        max_length=20,
        choices=(
            ('required', 'Required'),
            ('optional', 'Optional'),
            ('warn_only', 'Warn Only'),
        ),
        default='required'
    )
    max_gps_accuracy_meters = models.IntegerField(default=100)
    allow_holiday_attendance = models.BooleanField(default=True)
    allow_outside_geofence = models.BooleanField(default=True)
    late_grace_minutes = models.IntegerField(default=15)
    geofencing_policy = models.CharField(
        max_length=20,
        choices=(
            ('disabled', 'Disabled'),
            ('warning', 'Warning Only'),
            ('block', 'Block Check-in'),
        ),
        default='warning'
    )

    class Meta:
        verbose_name_plural = "Attendance Policies"

    def __str__(self):
        return f"Policy - {self.branch.name if self.branch else 'Global Default'}"


class ForgotCheckoutRequest(models.Model):
    STATUS_CHOICES = (
        ('pending_manager', 'Pending Manager Approval'),
        ('pending_hr', 'Pending HR Approval'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    )

    attendance = models.ForeignKey(
        Attendance,
        on_delete=models.CASCADE,
        related_name='forgot_checkout_requests'
    )
    reason = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending_manager'
    )
    check_out_time = models.DateTimeField()
    requested_at = models.DateTimeField(auto_now_add=True)
    reviewed_by_manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='manager_forgot_checkouts'
    )
    reviewed_by_hr = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='hr_forgot_checkouts'
    )
    rejection_reason = models.TextField(null=True, blank=True)

    class Meta:
        ordering = ['-requested_at']

    def __str__(self):
        return f"Forgot Checkout Request - {self.attendance.employee.full_name} - {self.status}"


class AttendanceCorrectionRequest(models.Model):
    STATUS_CHOICES = (
        ('pending', 'Pending Approval'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    )

    attendance = models.ForeignKey(
        Attendance,
        on_delete=models.CASCADE,
        related_name='correction_requests'
    )
    reason = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending'
    )
    check_in_time = models.DateTimeField(null=True, blank=True)
    check_out_time = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True)
    attachment = models.FileField(upload_to='attendance_corrections/', null=True, blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_corrections'
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(null=True, blank=True)

    class Meta:
        ordering = ['-requested_at']

    def __str__(self):
        return f"Correction Request - {self.attendance.employee.full_name} - {self.status}"


class AttendanceAuditLog(models.Model):
    attendance = models.ForeignKey(
        Attendance,
        on_delete=models.CASCADE,
        related_name='audit_logs'
    )
    action = models.CharField(max_length=50)  # e.g., 'create', 'update', 'correction', 'forgot_checkout'
    old_check_in_time = models.DateTimeField(null=True, blank=True)
    old_check_out_time = models.DateTimeField(null=True, blank=True)
    old_status = models.CharField(max_length=20, null=True, blank=True)
    new_check_in_time = models.DateTimeField(null=True, blank=True)
    new_check_out_time = models.DateTimeField(null=True, blank=True)
    new_status = models.CharField(max_length=20, null=True, blank=True)
    reason = models.TextField(blank=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"AuditLog - {self.attendance.employee.full_name} - {self.action} on {self.timestamp}"


class AttendanceActivityLog(models.Model):
    employee = models.ForeignKey(
        EmployeeProfile,
        on_delete=models.CASCADE,
        related_name='attendance_activities'
    )
    action = models.CharField(max_length=100) # e.g., 'check_in', 'check_out', 'forgot_checkout_request', 'correction_request'
    description = models.TextField() # e.g., 'Checked In at 09:02 AM'
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.employee.full_name} - {self.description} ({self.timestamp})"


class OvertimeRequest(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('manager_approved', 'Manager Approved'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('cancelled', 'Cancelled'),
    ]

    employee = models.ForeignKey(EmployeeProfile, on_delete=models.CASCADE, related_name='overtime_requests')
    date = models.DateField()
    attendance = models.ForeignKey(Attendance, on_delete=models.CASCADE, null=True, blank=True, related_name='overtime_requests')
    ot_minutes = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('employee', 'date')

    def __str__(self):
        return f"Overtime Request - {self.employee.full_name} - {self.date} ({self.status})"

    @property
    def workflow_instance(self):
        from apps.workflow.models import WorkflowInstance
        return WorkflowInstance.objects.filter(object_type='overtime_request', object_id=str(self.id)).first()


from django.db.models.signals import post_save
from django.dispatch import receiver
from apps.workflow.models import WorkflowInstance

@receiver(post_save, sender=OvertimeRequest)
def create_overtime_workflow_instance(sender, instance, created, **kwargs):
    if created:
        from apps.workflow.models import WorkflowDefinition, WorkflowInstance
        definition = WorkflowDefinition.objects.filter(code='ot_approval').first()
        if definition:
            if not WorkflowInstance.objects.filter(object_type='overtime_request', object_id=str(instance.id)).exists():
                user = getattr(instance.employee, 'user', None)
                wf_instance = WorkflowInstance.objects.create(
                    definition=definition,
                    object_type='overtime_request',
                    object_id=str(instance.id),
                    initiated_by=user
                )
                wf_instance.start_workflow()


@receiver(post_save, sender=WorkflowInstance)
def sync_overtime_request_status(sender, instance, **kwargs):
    if instance.object_type == 'overtime_request':
        try:
            ot_req = OvertimeRequest.objects.get(pk=instance.object_id)
            if ot_req.status != instance.current_status:
                ot_req.status = instance.current_status
                last_action = instance.actions.order_by('-timestamp').first()
                if last_action:
                    ot_req.reviewed_by = last_action.actor
                    ot_req.reviewed_at = last_action.timestamp
                ot_req.save()
        except OvertimeRequest.DoesNotExist:
            pass
