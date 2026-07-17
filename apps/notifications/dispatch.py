import logging
from django.core.mail import send_mail
from django.conf import settings

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
