from django.db import models
from django.conf import settings
from apps.employees.models import EmployeeProfile
from apps.projects.models import Project

class ScheduleEvent(models.Model):
    EVENT_TYPE_CHOICES = (
        ('Meeting', 'Meeting'),
        ('Task Deadline', 'Task Deadline'),
        ('Site Visit', 'Site Visit'),
        ('Reminder', 'Reminder'),
        ('Other', 'Other'),
    )

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    date = models.DateField()
    start_time = models.TimeField(null=True, blank=True, help_text="Leave blank for all-day events")
    end_time = models.TimeField(null=True, blank=True)
    event_type = models.CharField(max_length=50, choices=EVENT_TYPE_CHOICES, default='Other')
    assigned_to = models.ManyToManyField(EmployeeProfile, blank=True, related_name='schedule_events')
    project = models.ForeignKey(Project, on_delete=models.SET_NULL, null=True, blank=True, related_name='schedule_events')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='created_schedule_events')
    created_at = models.DateTimeField(auto_now_add=True)
    version = models.IntegerField(default=1)


    class Meta:
        ordering = ['date', 'start_time', 'title']

    def __str__(self):
        return f"{self.title} ({self.date})"

    @property
    def is_overnight(self):
        """Returns True if the event start and end time imply it spans into the next day."""
        if self.start_time and self.end_time:
            return self.end_time <= self.start_time
        return False

    def clean(self):
        from django.core.exceptions import ValidationError
        # Time boundary logic: if end_time is set, start_time must also be set
        if self.end_time and not self.start_time:
            raise ValidationError("Start time must be provided if end time is specified.")


        # Project completion validation
        if self.project and self.project.status == 'Completed':
            raise ValidationError("Cannot schedule events for a completed project.")

        # Self clean is called during form validation, assigned_to checks are handled there or in save

    def save(self, *args, **kwargs):
        self.full_clean()
        # Optimistic concurrency increment:
        if self.pk:
            # We don't increment here directly if we do it in views, but let's increment on successful update.
            # However, view handles the optimistic locking comparison. Let's increment here.
            self.version += 1
        super().save(*args, **kwargs)

    @property
    def color_tag(self):
        mapping = {
            'Meeting': 'green',
            'Task Deadline': 'blue',
            'Site Visit': 'amber',
            'Reminder': 'red',
            'Other': 'gray',
        }
        return mapping.get(self.event_type, 'gray')

    @property
    def color_classes(self):
        mapping = {
            'Meeting': 'bg-green-50 text-green-700 border-green-200/50 hover:bg-green-200',
            'Task Deadline': 'bg-blue-50 text-blue-700 border-blue-200/50 hover:bg-blue-200',
            'Site Visit': 'bg-amber-50 text-amber-700 border-amber-200/50 hover:bg-amber-200',
            'Reminder': 'bg-red-50 text-red-700 border-red-200/50 hover:bg-red-200',
            'Other': 'bg-gray-50 text-gray-700 border-gray-200/50 hover:bg-gray-200',
        }
        return mapping.get(self.event_type, 'bg-gray-50 text-gray-700 border-gray-200/50')

    @property
    def dot_color_class(self):
        mapping = {
            'Meeting': 'bg-green-500',
            'Task Deadline': 'bg-blue-500',
            'Site Visit': 'bg-amber-500',
            'Reminder': 'bg-red-500',
            'Other': 'bg-gray-500',
        }
        return mapping.get(self.event_type, 'bg-gray-500')

