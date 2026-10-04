import os
import json
import logging
from urllib.parse import urlparse

from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from pywebpush import webpush, WebPushException

from .models import WebPushSubscription

logger = logging.getLogger(__name__)

ALLOWED_PUSH_NOTIFICATION_TYPES = {
    'schedule_event',
    'task_assigned',
    'task_completed',
    'task_delayed',
}


def send_web_push(subscription: WebPushSubscription, payload_data, ttl: int = 86400, timeout: int = 10) -> bool:
    if not subscription or not subscription.is_active or not subscription.endpoint:
        return False

    private_key = getattr(settings, 'WEB_PUSH_PRIVATE_KEY', '') or os.getenv('WEB_PUSH_PRIVATE_KEY', '')
    subject = getattr(settings, 'WEB_PUSH_SUBJECT', '') or os.getenv('WEB_PUSH_SUBJECT', '')

    if not private_key:
        return False

    claim_sub = subject if (subject.startswith('mailto:') or subject.startswith('https://')) else f'mailto:{subject}'

    subscription_info = {
        'endpoint': subscription.endpoint,
        'keys': {
            'p256dh': subscription.p256dh,
            'auth': subscription.auth,
        }
    }

    if isinstance(payload_data, dict):
        data_str = json.dumps(payload_data)
    elif isinstance(payload_data, str):
        data_str = payload_data
    else:
        data_str = ''

    try:
        response = webpush(
            subscription_info=subscription_info,
            data=data_str,
            vapid_private_key=private_key,
            vapid_claims={'sub': claim_sub},
            ttl=ttl,
            timeout=timeout
        )
        status_code = getattr(response, 'status_code', 200)
        if status_code in (200, 201, 202):
            subscription.last_seen_at = timezone.now()
            subscription.save(update_fields=['last_seen_at'])
            return True
        elif status_code in (404, 410):
            subscription.is_active = False
            subscription.save(update_fields=['is_active'])
            return False
        return False
    except WebPushException as ex:
        status_code = getattr(ex, 'status_code', None)
        if status_code is None and getattr(ex, 'response', None) is not None:
            status_code = getattr(ex.response, 'status_code', None)
        if status_code in (404, 410):
            subscription.is_active = False
            subscription.save(update_fields=['is_active'])
        return False
    except Exception as e:
        logger.warning("Web push delivery failed: %s", type(e).__name__)
        return False


def deliver_notification_web_push(notification) -> int:
    if not notification or not notification.recipient_id:
        return 0

    if notification.notif_type not in ALLOWED_PUSH_NOTIFICATION_TYPES:
        return 0

    recipient = notification.recipient
    subscriptions = WebPushSubscription.objects.filter(
        user=recipient,
        is_active=True
    )

    if not subscriptions.exists():
        return 0

    if notification.notif_type == 'schedule_event':
        try:
            redirect_url = reverse('schedule:month_view')
        except Exception:
            redirect_url = '/schedule/'
    else:
        try:
            redirect_url = reverse('staff:my_tasks')
        except Exception:
            redirect_url = '/notifications/'

    payload = {
        'id': notification.id,
        'type': notification.notif_type,
        'title': notification.title,
        'message': notification.message or notification.title,
        'body': notification.message or notification.title,
        'redirect_url': redirect_url,
        'url': redirect_url,
        'tag': f'ft-notif-{notification.id}',
        'icon': '/static/icons/icon.png',
    }

    success_count = 0
    for sub in subscriptions:
        if send_web_push(sub, payload):
            success_count += 1

    return success_count
