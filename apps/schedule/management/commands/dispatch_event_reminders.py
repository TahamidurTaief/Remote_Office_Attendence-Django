import datetime
import logging
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.notifications.dispatch import send_email_notification
from apps.notifications.models import Notification
from apps.schedule.models import ScheduleEvent

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Idempotently dispatches in-app notifications and email reminders for due scheduled events. "
        "Evaluates timed events using the project's configured timezone (Asia/Dhaka). "
        "Due rule: An event is eligible if start_time is set (non-all-day), reminder_sent_at is null, "
        "and the timezone-aware event start datetime falls within the bounded grace window "
        "[now - grace_window, now] (default grace window: 15 minutes). "
        "Skips all-day events (no start_time), future events (start > now), and expired events "
        "older than the grace window. Suitable for execution via a 1-minute Coolify cron."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--grace-minutes',
            type=int,
            default=15,
            help='Grace window in minutes for due event start times (default: 15).'
        )
        parser.add_argument(
            '--now',
            type=str,
            default=None,
            help='Override current reference timestamp (ISO format, e.g. 2026-10-03T10:00:00).'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Simulate dispatch without claiming events or persisting notifications/emails.'
        )

    def handle(self, *args, **options):
        grace_minutes = options['grace_minutes']
        now_override_str = options['now']
        dry_run = options['dry_run']

        current_tz = timezone.get_current_timezone()

        if now_override_str:
            parsed = parse_datetime(now_override_str)
            if parsed is None:
                parsed = datetime.datetime.fromisoformat(now_override_str)
            if timezone.is_naive(parsed):
                ref_now = timezone.make_aware(parsed, current_tz)
            else:
                ref_now = parsed.astimezone(current_tz)
        else:
            ref_now = timezone.now().astimezone(current_tz)

        window_start = ref_now - datetime.timedelta(minutes=grace_minutes)
        window_end = ref_now

        self.stdout.write(
            f"Checking due events in window [{window_start.isoformat()} to {window_end.isoformat()}] "
            f"in timezone {current_tz}..."
        )

        # Date range for candidate lookup in database
        candidate_min_date = window_start.date()
        candidate_max_date = window_end.date()

        candidate_ids = list(
            ScheduleEvent.objects.filter(
                reminder_sent_at__isnull=True,
                start_time__isnull=False,
                date__gte=candidate_min_date,
                date__lte=candidate_max_date,
            ).order_by('date', 'start_time', 'id').values_list('id', flat=True)
        )

        dispatched_count = 0
        total_notifications = 0
        total_emails = 0
        skipped_count = 0

        for event_id in candidate_ids:
            try:
                with transaction.atomic():
                    # PostgreSQL row-level lock; works across supported backends
                    event = (
                        ScheduleEvent.objects.select_for_update()
                        .filter(pk=event_id, reminder_sent_at__isnull=True)
                        .first()
                    )
                    if not event or not event.start_time:
                        skipped_count += 1
                        continue

                    # Construct timezone-aware event start datetime
                    naive_dt = datetime.datetime.combine(event.date, event.start_time)
                    event_start_aware = timezone.make_aware(naive_dt, current_tz)

                    # Verify due window: must be due now and not older than grace window
                    if not (window_start <= event_start_aware <= window_end):
                        skipped_count += 1
                        continue

                    if dry_run:
                        self.stdout.write(
                            f"[DRY-RUN] Would dispatch reminder for Event #{event.pk}: '{event.title}' "
                            f"at {event_start_aware.isoformat()}"
                        )
                        dispatched_count += 1
                        continue

                    # Conditional update ensures atomic claim even across concurrent workers
                    claimed = ScheduleEvent.objects.filter(
                        pk=event.pk,
                        reminder_sent_at__isnull=True,
                    ).update(reminder_sent_at=ref_now)

                    if not claimed:
                        # Already claimed by another concurrent worker
                        skipped_count += 1
                        continue

                    # Build in-app notifications and email payloads
                    notifications = []
                    email_payloads = []
                    seen_user_ids = set()
                    seen_emails = set()

                    event_title = str(event.title)
                    event_date_str = event.date.strftime('%d/%m/%Y')
                    event_time_str = event.start_time.strftime('%I:%M %p')
                    event_desc = str(event.description or 'No description')

                    assigned_employees = event.assigned_to.select_related('user').all()

                    for employee in assigned_employees:
                        user = getattr(employee, 'user', None)
                        if not user:
                            # Missing user must not fail the batch
                            continue

                        if user.pk not in seen_user_ids:
                            seen_user_ids.add(user.pk)
                            notifications.append(
                                Notification(
                                    recipient=user,
                                    employee=employee,
                                    title=f"Reminder: {event_title}"[:200],
                                    message=f"Reminder: '{event_title}' starts at {event_time_str} today ({event_date_str}).",
                                    notif_type='field_visit',
                                )
                            )

                        recipient_email = (getattr(user, 'email', None) or '').strip()
                        if recipient_email and recipient_email not in seen_emails:
                            seen_emails.add(recipient_email)
                            emp_name = str(
                                getattr(employee, 'full_name', '')
                                or getattr(user, 'get_full_name', lambda: '')()
                                or 'Team Member'
                            )
                            subject = f"Event Reminder: {event_title}"
                            body = (
                                f"Hello {emp_name},\n\n"
                                f"This is a reminder that your scheduled event is starting soon:\n"
                                f"Title: {event_title}\n"
                                f"Date: {event_date_str}\n"
                                f"Time: {event_time_str}\n"
                                f"Description: {event_desc}\n\n"
                                f"Regards,\nFieldTrack System"
                            )
                            email_payloads.append((recipient_email, subject, body))

                    if notifications:
                        Notification.objects.bulk_create(notifications)

                    if email_payloads:
                        payloads_tuple = tuple(email_payloads)

                        def _dispatch_reminder_emails(payloads=payloads_tuple):
                            for email_addr, subj, msg in payloads:
                                try:
                                    send_email_notification(email_addr, subj, msg)
                                except Exception as exc:
                                    logger.warning("Failed sending reminder email to %s: %s", email_addr, exc)

                        transaction.on_commit(_dispatch_reminder_emails)

                    dispatched_count += 1
                    total_notifications += len(notifications)
                    total_emails += len(email_payloads)
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"Dispatched reminder for Event #{event.pk}: '{event.title}' "
                            f"({len(notifications)} notifications, {len(email_payloads)} emails)"
                        )
                    )

            except Exception as e:
                logger.exception("Error dispatching reminder for Event #%s: %s", event_id, e)
                self.stdout.write(
                    self.style.ERROR(f"Error processing Event #{event_id}: {e}")
                )

        status_msg = (
            f"Done. Dispatched: {dispatched_count}, Notifications: {total_notifications}, "
            f"Emails: {total_emails}, Skipped: {skipped_count}."
        )
        self.stdout.write(self.style.SUCCESS(status_msg))
