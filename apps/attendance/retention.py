from django.utils import timezone
from datetime import timedelta
from dateutil.relativedelta import relativedelta
from .models import Attendance, AttendanceLocation
import logging

logger = logging.getLogger(__name__)

def mark_expired_records():
    """
    Mark attendance records older than 3 months as expired.
    Runs daily via cron.
    """
    three_months_ago = (
        timezone.now() - relativedelta(months=3)).date()
    
    # Mark attendance records as expired
    expired_count = Attendance.objects.filter(
        date__lt=three_months_ago,
        is_expired=False
    ).update(
        is_expired=True,
        expired_at=timezone.now()
    )
    
    # Mark related locations as expired
    AttendanceLocation.objects.filter(
        attendance__is_expired=True,
        is_expired=False
    ).update(is_expired=True)
    
    logger.info(
        f'Marked {expired_count} records as expired')
    return expired_count

def delete_old_expired_records():
    """
    Permanently delete records expired more than 2 months ago.
    Runs daily via cron.
    """
    two_months_ago = timezone.now() - relativedelta(months=2)
    
    # Delete locations first (FK constraint)
    loc_deleted, _ = AttendanceLocation.objects.filter(
        attendance__expired_at__lt=two_months_ago
    ).delete()
    
    # Delete attendance records
    att_deleted, _ = Attendance.objects.filter(
        is_expired=True,
        expired_at__lt=two_months_ago
    ).delete()
    
    logger.info(
        f'Deleted {att_deleted} expired records, '
        f'{loc_deleted} locations'
    )
    return att_deleted, loc_deleted

def get_retention_stats():
    """Returns stats for admin dashboard"""
    now = timezone.now()
    two_months_ago = now - relativedelta(months=2)
    soon_window_start = two_months_ago
    soon_window_end = soon_window_start + timedelta(days=30)

    deleting_soon = Attendance.objects.filter(
        is_expired=True,
        expired_at__gte=soon_window_start,
        expired_at__lte=soon_window_end
    ).count()

    return {
        'active_count': Attendance.objects.filter(
            is_expired=False).count(),
        'expired_count': Attendance.objects.filter(
            is_expired=True).count(),
        'expiring_soon': deleting_soon,
        'deleting_soon': deleting_soon,
        'to_be_deleted': Attendance.objects.filter(
            is_expired=True,
            expired_at__lt=two_months_ago
        ).count(),
    }
