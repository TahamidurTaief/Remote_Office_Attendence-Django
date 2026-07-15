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
    created_at = models.DateTimeField(auto_now_add=True)

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
    timestamp = models.DateTimeField(auto_now_add=True)

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
