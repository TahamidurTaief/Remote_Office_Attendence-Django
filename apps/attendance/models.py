from django.db import models
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
    )
    ATTENDANCE_TYPE_CHOICES = (
        ('check_in', 'Check In/Out Session'),
        ('field_visit', 'Field Visit'),
    )

    employee = models.ForeignKey(EmployeeProfile, on_delete=models.CASCADE, related_name='attendances')
    date = models.DateField()
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
    
    is_expired = models.BooleanField(
        default=False,
        help_text='Data older than 3 months'
    )
    expired_at = models.DateTimeField(
        null=True, blank=True,
        help_text='When this record was marked expired'
    )
    
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-check_in_time']

    def __str__(self):
        return f"{self.employee.full_name} - {self.date} ({self.attendance_type})"

    @property
    def is_active_session(self):
        """True if employee is currently checked-in (no check_out yet)."""
        return self.attendance_type == 'check_in' and self.check_in_time and not self.check_out_time


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

