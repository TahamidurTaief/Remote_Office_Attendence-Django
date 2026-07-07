from django.db import models
from django.conf import settings


class Notification(models.Model):
    NOTIF_TYPES = [
        ('check_in', 'Check In'),
        ('check_out', 'Check Out'),
        ('field_visit', 'Field Visit'),
        ('late', 'Late Alert'),
        ('missing', 'Missing Employee'),
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
    notif_type = models.CharField(max_length=20, choices=NOTIF_TYPES)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        recipient_name = self.recipient.email or self.recipient.phone or "Unknown"
        return f"{self.title} → {recipient_name}"
