from django.db.models import Count, Q
from django.utils import timezone
from datetime import timedelta
from dateutil.relativedelta import relativedelta
from .models import Attendance
import logging

logger = logging.getLogger(__name__)

RETENTION_DISABLED_MESSAGE = (
    'Attendance retention mutation is disabled by policy; '
    'no records were marked expired or deleted.'
)


def mark_expired_records():
    """
    No-op: destructive/expiring attendance retention is disabled by default.
    Preserves all historical Attendance and AttendanceLocation records.
    """
    logger.info(RETENTION_DISABLED_MESSAGE)
    return 0


def delete_old_expired_records():
    """
    No-op: destructive attendance purge is disabled by default.
    Preserves all historical Attendance, AttendanceLocation, and media files.
    """
    logger.info(RETENTION_DISABLED_MESSAGE)
    return 0, 0


def get_retention_stats():
    """Returns database-side bounded retention candidate statistics (read-only)."""
    now = timezone.now()
    two_months_ago = now - relativedelta(months=2)
    soon_window_start = two_months_ago
    soon_window_end = soon_window_start + timedelta(days=30)

    agg = Attendance.objects.aggregate(
        active_count=Count('id', filter=Q(is_expired=False)),
        expired_count=Count('id', filter=Q(is_expired=True)),
        deleting_soon=Count(
            'id',
            filter=Q(
                is_expired=True,
                expired_at__gte=soon_window_start,
                expired_at__lte=soon_window_end,
            ),
        ),
        to_be_deleted=Count(
            'id',
            filter=Q(
                is_expired=True,
                expired_at__lt=two_months_ago,
            ),
        ),
    )

    deleting_soon = agg['deleting_soon'] or 0
    return {
        'active_count': agg['active_count'] or 0,
        'expired_count': agg['expired_count'] or 0,
        'expiring_soon': deleting_soon,
        'deleting_soon': deleting_soon,
        'to_be_deleted': agg['to_be_deleted'] or 0,
    }
