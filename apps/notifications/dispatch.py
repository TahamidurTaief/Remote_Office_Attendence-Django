import logging
from django.core.mail import send_mail
from django.conf import settings
from .models import ActivityLog, Notification

logger = logging.getLogger(__name__)

def send_email_notification(user, subject, message):
    """
    Sends an email to the user (can be a user instance, raw email string, or any object with email attr).
    Handles exceptions gracefully, logging failures without crashing the caller.
    """
    if not user:
        logger.warning("Attempted to send email with no user/recipient specified.")
        return False
        
    email = user if isinstance(user, str) else getattr(user, 'email', None)
    if not email:
        logger.warning("Attempted to send email to recipient without an email address.")
        return False
        
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@fieldtrack.com'),
            recipient_list=[email],
            fail_silently=False,
        )
        logger.info(f"Email sent successfully to {email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email to {email}: {e}")
        return False


def log_activity(actor, verb, target, metadata=None, notify_users=None, email_also=False):
    """
    Generic logging layer dispatch helper.
    Always creates an ActivityLog row.
    If notify_users is provided, creates a Notification row per user.
    If email_also is True, also calls send_email_notification for notify_users.
    """
    if metadata is None:
        metadata = {}

    log = ActivityLog.objects.create(
        actor=actor,
        verb=verb,
        target=target,
        metadata=metadata
    )

    if notify_users:
        title = metadata.get('title') or verb.replace('_', ' ').title()
        message = metadata.get('message') or f"{verb} on {target}"
        notif_type = metadata.get('notif_type') or verb[:20]

        for user in notify_users:
            if user:
                emp = getattr(user, 'employee_profile', None)
                Notification.objects.create(
                    recipient=user,
                    employee=emp,
                    title=title,
                    message=message,
                    notif_type=notif_type
                )

    if email_also and notify_users:
        email_subject = metadata.get('email_subject') or metadata.get('title') or verb.replace('_', ' ').title()
        email_message = metadata.get('email_message') or metadata.get('message') or f"{verb} on {target}"
        for user in notify_users:
            if user:
                send_email_notification(user, email_subject, email_message)

    return log

