from django.contrib.auth import get_user_model

from .models import Notification

User = get_user_model()


def notify_admins(employee, notif_type, location=''):
    admins = User.objects.filter(role='admin', is_active=True)
    if not admins.exists():
        return

    titles = {
        'check_in': f"{employee.full_name} checked in",
        'check_out': f"{employee.full_name} checked out",
        'field_visit': f"{employee.full_name} field visit",
        'late': f"{employee.full_name} is late",
        'missing': f"{employee.full_name} not checked in",
    }
    messages = {
        'check_in': f"Location: {location}",
        'check_out': f"Location: {location}",
        'field_visit': f"Visit at: {location}",
        'late': f"Late check-in. Location: {location}",
        'missing': "No check-in recorded today.",
    }

    notifications = [
        Notification(
            recipient=admin,
            employee=employee,
            title=titles.get(notif_type, 'New Event'),
            message=messages.get(notif_type, ''),
            notif_type=notif_type,
        )
        for admin in admins
    ]
    Notification.objects.bulk_create(notifications)
