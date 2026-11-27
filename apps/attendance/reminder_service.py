from datetime import datetime, timedelta
import logging

from django.db import transaction
from django.utils import timezone

from apps.attendance.models import Attendance
from apps.attendance.schedule_utils import resolve_checkout_window
from apps.employees.models import EmployeeProfile
from apps.notifications.models import ActivityLog, Notification, WebPushSubscription
from apps.notifications.web_push import send_web_push

logger = logging.getLogger(__name__)

FIRST_REMINDER_DELAY_MINUTES = 15
REPEAT_INTERVAL_MINUTES = 60
MAX_REMINDERS_PER_LOCAL_DAY = 4
SNOOZE_DURATION_MINUTES = 60


def snooze_missing_checkout_reminder(employee, now=None, minutes=SNOOZE_DURATION_MINUTES):
    """Durably snooze missing-checkout reminders for an employee across tabs/workers/devices."""
    ref_now = now or timezone.now()
    snoozed_until = ref_now + timedelta(minutes=minutes)
    return ActivityLog.objects.create(
        actor=getattr(employee, 'user', None),
        verb='missing_checkout_snooze',
        target=employee,
        metadata={
            'snoozed_until': snoozed_until.isoformat(),
            'local_date': timezone.localdate(ref_now).isoformat(),
        },
    )


def _deliver_reminder_web_push(user, payload):
    """Best-effort background Web Push delivery; never called inside DB transactions."""
    if not user:
        return 0
    sent = 0
    subs = WebPushSubscription.objects.filter(user=user, is_active=True)
    for sub in subs:
        try:
            if send_web_push(sub, payload):
                sent += 1
        except Exception as exc:
            logger.warning("Missing checkout web push failed for user %s: %s", user.id, type(exc).__name__)
    return sent


def evaluate_and_send_missing_checkout_reminder(employee, now=None, send_push=True):
    """
    Evaluate open sessions for one employee and send at most one deduplicated grouped
    reminder for the active 60-minute window.
    - Starts 15 minutes after shift_end_at
    - Repeats every 60 minutes, capped at 4 per employee-local-day
    - Respects 60-minute durable snooze
    - Offers checkout before deadline, correction workflow at/after deadline
    - Dispatches Web Push outside transaction.atomic()
    """
    ref_now = now or timezone.now()
    local_now = timezone.localtime(ref_now)
    local_date_str = local_now.date().isoformat()

    notification_to_push = None
    push_payload = None
    result = {
        'sent': False,
        'reason': None,
        'action': None,
        'session_ids': [],
        'push_sent': 0,
    }

    with transaction.atomic():
        emp_locked = EmployeeProfile.objects.select_for_update().get(pk=employee.pk)
        if not emp_locked.is_active:
            result['reason'] = 'inactive_employee'
            return result

        open_sessions = list(
            Attendance.objects.filter(
                employee=emp_locked,
                attendance_type='check_in',
                check_out_time__isnull=True,
                is_expired=False,
            ).order_by('check_in_time')
        )
        if not open_sessions:
            result['reason'] = 'no_open_sessions'
            return result

        eligible_sessions = []
        resolved_count = 0
        any_past_deadline = False
        latest_window = None

        for sess in open_sessions:
            if not sess.check_in_time:
                continue
            win = resolve_checkout_window(sess.check_in_time, employee=emp_locked)
            if not win['resolved']:
                continue
            resolved_count += 1
            first_alert_at = win['shift_end_at'] + timedelta(minutes=FIRST_REMINDER_DELAY_MINUTES)
            if ref_now >= first_alert_at:
                eligible_sessions.append((sess, win, first_alert_at))
                if ref_now >= win['checkout_deadline_at']:
                    any_past_deadline = True
                latest_window = (sess, win, first_alert_at)

        if not eligible_sessions:
            result['reason'] = 'unresolved_schedule' if resolved_count == 0 else 'before_first_reminder'
            return result

        # Check active durable snooze
        latest_snooze = (
            ActivityLog.objects.filter(
                verb='missing_checkout_snooze',
                target_object_id=emp_locked.pk,
            )
            .order_by('-created_at')
            .first()
        )
        if latest_snooze and isinstance(latest_snooze.metadata, dict):
            raw_until = latest_snooze.metadata.get('snoozed_until')
            if raw_until:
                try:
                    until_dt = datetime.fromisoformat(raw_until)
                    if timezone.is_naive(until_dt):
                        until_dt = timezone.make_aware(until_dt, timezone.get_current_timezone())
                    if ref_now < until_dt:
                        result['reason'] = 'snoozed'
                        return result
                except ValueError:
                    pass

        # Enforce daily cap of 4 reminders per employee-local-day
        day_logs = list(
            ActivityLog.objects.filter(
                verb='missing_checkout_reminder',
                target_object_id=emp_locked.pk,
                metadata__local_date=local_date_str,
            )
        )
        if len(day_logs) >= MAX_REMINDERS_PER_LOCAL_DAY:
            result['reason'] = 'daily_cap_reached'
            return result

        # Window index relative to the most recent eligible session's first_alert_at
        _, _, anchor_first_alert = latest_window
        window_index = int((ref_now - anchor_first_alert).total_seconds() // (REPEAT_INTERVAL_MINUTES * 60))
        dedupe_key = f"{emp_locked.pk}:{local_date_str}:w{window_index}"

        if any((log.metadata or {}).get('dedupe_key') == dedupe_key for log in day_logs):
            result['reason'] = 'deduplicated'
            return result

        session_ids = [s.id for s, _, _ in eligible_sessions]
        action = 'correction' if any_past_deadline else 'checkout'
        target_url = '/attendance/forgot-checkout/' if action == 'correction' else '/staff/check-in/'
        if len(session_ids) > 1:
            title = f"Missing Checkout ({len(session_ids)} open sessions)"
            message = (
                f"You have {len(session_ids)} unresolved check-in sessions. "
                + ("Submit a missing-checkout correction." if action == 'correction' else "Please check out or review.")
            )
        else:
            title = "Missing Checkout Reminder"
            message = (
                "Your shift checkout window has ended. Please submit a missing-checkout correction."
                if action == 'correction'
                else "Your shift has ended. Please remember to check out."
            )

        notif = Notification.objects.create(
            recipient=emp_locked.user,
            employee=emp_locked,
            title=title,
            message=message,
            notif_type='missing',
        )
        ActivityLog.objects.create(
            actor=emp_locked.user,
            verb='missing_checkout_reminder',
            target=emp_locked,
            metadata={
                'dedupe_key': dedupe_key,
                'local_date': local_date_str,
                'window_index': window_index,
                'session_ids': session_ids,
                'action': action,
                'notification_id': notif.id,
            },
        )
        notification_to_push = notif
        push_payload = {
            'id': notif.id,
            'type': 'missing',
            'title': title,
            'message': message,
            'body': message,
            'url': target_url,
            'redirect_url': target_url,
            'tag': f"ft-missing-checkout-{emp_locked.pk}-{local_date_str}",
            'icon': '/static/icons/icon.png',
        }
        result.update({
            'sent': True,
            'action': action,
            'session_ids': session_ids,
            'dedupe_key': dedupe_key,
        })

    # Outside atomic transaction: dispatch background Web Push (never inside punch transactions)
    if send_push and notification_to_push is not None:
        result['push_sent'] = _deliver_reminder_web_push(notification_to_push.recipient, push_payload)

    return result
