import logging
from datetime import timedelta
from django.utils import timezone
from django.db.models import Count, Q

from apps.accounts.models import CustomUser, UserSession, TrustedDevice
from apps.employees.models import (
    Employee, EmployeeProfile, EmployeeStatus, EmployeeDocument,
    EmploymentHistory, LifecycleTransitionRequest
)
from apps.attendance.models import Attendance
from apps.leave.models import LeaveRequest
from apps.expense.models import Expense
from apps.projects.models import Project, ProjectTask, DailyProgressLog
from apps.notifications.models import Notification
from apps.schedule.models import ScheduleEvent

logger = logging.getLogger(__name__)


def determine_user_role_variant(user):
    """
    Determines which dashboard variant to render based on PermissionEngine resolved permissions,
    assigned roles, and whether the user is a reporting manager.
    Returns: 'admin', 'hr', 'manager', or 'employee'.
    """
    if not user or not user.is_authenticated:
        return 'employee'

    from apps.accounts.engine import PermissionEngine

    # Admin check (superuser or accounts.view/edit permission or role=='admin')
    if user.is_superuser or PermissionEngine.evaluate(user, 'accounts.view').allowed or getattr(user, 'role', '') == 'admin':
        return 'admin'

    # HR check (hr permission or role in hr roles)
    if PermissionEngine.evaluate(user, 'employees.view').allowed and PermissionEngine.evaluate(user, 'leave.approve').allowed:
        return 'hr'
    if getattr(user, 'role', '') in ('hr', 'hr_manager', 'hr_admin'):
        return 'hr'

    # Manager check (projects/leave approve permission or role=='manager' or user has direct reports)
    is_manager_role = getattr(user, 'role', '') == 'manager' or PermissionEngine.evaluate(user, 'leave.approve').allowed
    emp_master = getattr(user, 'employee_master', None)
    has_direct_reports = False
    if emp_master:
        has_direct_reports = Employee.objects.filter(reporting_manager=emp_master).exists()

    if is_manager_role or has_direct_reports:
        return 'manager'

    return 'employee'


def get_employee_dashboard_data(user):
    """
    Data scoped exclusively to the logged-in employee (self).
    """
    today = timezone.localdate()
    data = {}

    emp_profile = getattr(user, 'employee_profile', None)
    emp_master = getattr(user, 'employee_master', None)

    # Today's attendance
    if emp_profile:
        data['today_attendance'] = Attendance.objects.select_related('employee', 'employee__branch').filter(
            employee=emp_profile, date=today
        ).first()
    else:
        data['today_attendance'] = None

    # Assigned tasks
    if emp_profile:
        data['assigned_tasks'] = ProjectTask.objects.select_related('project', 'responsible_person').filter(
            responsible_person=emp_profile
        ).exclude(status='Completed')[:5]
    else:
        data['assigned_tasks'] = []

    # Leave requests (own)
    if emp_profile:
        data['my_leave_requests'] = LeaveRequest.objects.select_related('leave_type', 'employee').filter(
            employee=emp_profile
        ).order_by('-requested_at')[:5]
    else:
        data['my_leave_requests'] = []

    # Expense claims (own)
    if emp_profile:
        data['my_expenses'] = Expense.objects.select_related('employee').filter(
            employee=emp_profile
        ).order_by('-requested_at')[:5]
    else:
        data['my_expenses'] = []

    # Notifications
    data['my_notifications'] = Notification.objects.filter(
        recipient=user, is_read=False
    ).order_by('-created_at')[:5]

    # Upcoming schedule events
    data['upcoming_holidays'] = ScheduleEvent.objects.filter(
        date__gte=today
    ).order_by('date')[:3]

    # Personal Employment History Timeline
    if emp_master:
        data['personal_timeline'] = EmploymentHistory.objects.select_related(
            'employee', 'approved_by'
        ).filter(employee=emp_master).order_by('-effective_date', '-created_at')[:5]
    else:
        data['personal_timeline'] = []

    return data


def get_manager_dashboard_data(user):
    """
    Data scoped strictly to the manager's direct reports and managed projects.
    """
    today = timezone.localdate()
    data = {}

    emp_master = getattr(user, 'employee_master', None)

    if not emp_master:
        # Fallback if manager doesn't have an Employee Master linked yet
        data['team_count'] = 0
        data['team_attendance_today'] = []
        data['pending_leave_approvals'] = []
        data['pending_expense_approvals'] = []
        data['managed_projects'] = []
        data['team_recent_progress'] = []
        return data

    # Direct reports in Employee Master
    direct_reports_qs = Employee.objects.select_related(
        'user', 'department', 'designation', 'branch'
    ).filter(reporting_manager=emp_master)

    team_users = CustomUser.objects.filter(employee_master__in=direct_reports_qs)
    team_profiles = EmployeeProfile.objects.filter(master_employee__in=direct_reports_qs)

    data['team_count'] = direct_reports_qs.count()

    # Team attendance today
    data['team_attendance_today'] = Attendance.objects.select_related('employee', 'employee__branch').filter(
        employee__in=team_profiles, date=today
    )

    # Pending Leave Approvals for team
    data['pending_leave_approvals'] = LeaveRequest.objects.select_related('employee', 'leave_type').filter(
        employee__in=team_profiles, status='pending'
    ).order_by('-requested_at')[:5]

    # Pending Expense Approvals for team
    data['pending_expense_approvals'] = Expense.objects.select_related('employee').filter(
        employee__in=team_profiles, status='pending'
    ).order_by('-requested_at')[:5]

    # Projects managed by this user
    emp_profile = getattr(user, 'employee_profile', None)
    if emp_profile:
        data['managed_projects'] = Project.objects.select_related('branch').filter(
            project_managers=emp_profile
        )[:5]
    else:
        data['managed_projects'] = []

    # Recent team progress logs
    data['team_recent_progress'] = DailyProgressLog.objects.select_related(
        'project', 'logged_by'
    ).filter(
        logged_by__in=team_users
    ).order_by('-date', '-created_at')[:5]

    return data


def get_hr_dashboard_data(user):
    """
    Org-wide HR analytical metrics and alerts.
    """
    today = timezone.localdate()
    data = {}

    # Headcount & status breakdown
    data['total_employees'] = Employee.objects.count()
    data['active_employees_count'] = Employee.objects.filter(status=EmployeeStatus.ACTIVE).count()
    data['probation_employees_count'] = Employee.objects.filter(status=EmployeeStatus.PROBATION).count()

    # Department breakdown
    data['dept_breakdown'] = Employee.objects.values(
        'department__name'
    ).annotate(count=Count('id')).order_by('-count')[:5]

    # Org-wide Attendance Today
    data['today_attendance_count'] = Attendance.objects.filter(date=today, status='present').count()
    data['today_late_count'] = Attendance.objects.filter(date=today, status='late').count()

    # On Leave Today
    data['on_leave_today'] = LeaveRequest.objects.select_related('employee', 'leave_type').filter(
        start_date__lte=today, end_date__gte=today, status='approved'
    )[:5]

    # Pending Lifecycle Requests Count
    data['pending_lifecycle_requests_count'] = LifecycleTransitionRequest.objects.filter(
        review_status='pending'
    ).count()

    # Expiring Documents
    soon_threshold = today + timedelta(days=30)
    data['expiring_documents'] = EmployeeDocument.objects.select_related(
        'employee_master', 'employee'
    ).filter(
        expiry_date__isnull=False,
        expiry_date__gte=today,
        expiry_date__lte=soon_threshold,
        is_active=True
    ).order_by('expiry_date')[:5]

    # Probation ending soon
    data['probation_list'] = Employee.objects.select_related(
        'department', 'designation', 'branch'
    ).filter(
        status=EmployeeStatus.PROBATION
    ).order_by('joined_date')[:5]

    # Upcoming birthdays this month
    data['upcoming_birthdays'] = Employee.objects.filter(
        dob__month=today.month
    ).order_by('dob__day')[:5]

    # Recent joiners (joined last 30 days)
    thirty_days_ago = today - timedelta(days=30)
    data['recent_joiners'] = Employee.objects.select_related(
        'department', 'designation', 'branch'
    ).filter(
        joined_date__gte=thirty_days_ago
    ).order_by('-joined_date')[:5]

    return data


def get_admin_dashboard_data(user):
    """
    Admin dashboard: HR org-wide metrics + System security & session stats.
    """
    data = get_hr_dashboard_data(user)

    # Active sessions & security metrics
    data['active_sessions_count'] = UserSession.objects.filter(is_active=True).count()
    data['trusted_devices_count'] = TrustedDevice.objects.count()
    data['recent_audit_events_count'] = 0

    return data
