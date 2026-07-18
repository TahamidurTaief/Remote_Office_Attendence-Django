from django.db import models
from django.conf import settings


class Notification(models.Model):
    NOTIF_TYPES = [
        ('check_in', 'Check In'),
        ('check_out', 'Check Out'),
        ('field_visit', 'Field Visit'),
        ('late', 'Late Alert'),
        ('missing', 'Missing Employee'),
        ('document_expiry', 'Document Expiry'),
        ('task_assigned', 'Task Assigned'),
        ('task_completed', 'Task Completed'),
        ('task_delayed', 'Task Delayed'),
        ('role_changed', 'Role/Group Changed'),
        ('permission_changed', 'Permission Changed'),
    ]
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notifications'
    )
    employee = models.ForeignKey(
        'employees.EmployeeProfile',
        on_delete=models.SET_NULL,
        null=True, blank=True
    )
    title = models.CharField(max_length=200)
    message = models.TextField(blank=True)
    notif_type = models.CharField(max_length=50, choices=NOTIF_TYPES)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        recipient_name = self.recipient.email or self.recipient.phone or "Unknown"
        return f"{self.title} → {recipient_name}"


from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes.fields import GenericForeignKey


class ActivityLog(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='activity_logs'
    )
    verb = models.CharField(max_length=100)
    target_content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        null=True, blank=True
    )
    target_object_id = models.PositiveIntegerField(null=True, blank=True)
    target = GenericForeignKey('target_content_type', 'target_object_id')
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        actor_name = self.actor.email or self.actor.phone if self.actor else "System"
        return f"{actor_name} {self.verb} on {self.target} at {self.created_at}"

