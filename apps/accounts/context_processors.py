def notifications(request):
    expired_count = 0
    unread_count = 0
    if request.user.is_authenticated and getattr(request.user, 'role', '') == 'admin':
        try:
            from apps.attendance.models import Attendance
            expired_count = Attendance.objects.filter(is_expired=True).count()
        except Exception:
            expired_count = 0

        try:
            from apps.notifications.models import Notification
            unread_count = Notification.objects.filter(
                recipient=request.user, is_read=False
            ).count()
        except Exception:
            unread_count = 0

    return {
        'unread_notifications': unread_count,
        'expired_data_count': expired_count
    }
