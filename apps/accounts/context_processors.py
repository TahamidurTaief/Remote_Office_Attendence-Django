def notifications(request):
    expired_count = 0
    unread_count = 0
    pending_leave_count = 0
    can_access_my_projects = False

    if request.user.is_authenticated:
        from apps.accounts.permissions import user_can_access_my_projects
        can_access_my_projects = user_can_access_my_projects(request.user)

        from apps.accounts.engine import PermissionEngine
        if request.user.is_superuser or PermissionEngine.evaluate(request.user, 'dashboard.view').allowed:
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

            try:
                from apps.leave.models import LeaveRequest
                pending_leave_count = LeaveRequest.objects.filter(status='pending').count()
            except Exception:
                pending_leave_count = 0

    return {
        'unread_notifications': unread_count,
        'expired_data_count': expired_count,
        'pending_leave_count': pending_leave_count,
        'can_access_my_projects': can_access_my_projects,
    }
