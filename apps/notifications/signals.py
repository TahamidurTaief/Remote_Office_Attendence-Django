import logging
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Notification
from .web_push import deliver_notification_web_push, ALLOWED_PUSH_NOTIFICATION_TYPES

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Notification)
def on_notification_created(sender, instance, created, **kwargs):
    if created and instance.notif_type in ALLOWED_PUSH_NOTIFICATION_TYPES:
        def _deliver():
            try:
                deliver_notification_web_push(instance)
            except Exception as e:
                logger.warning("Notification web push dispatch error: %s", type(e).__name__)

        transaction.on_commit(_deliver)
