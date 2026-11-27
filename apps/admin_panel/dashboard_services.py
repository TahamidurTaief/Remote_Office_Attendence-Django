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

    from apps.accounts.rbac_models import DataScope
    # Admin check (superuser or accounts.view/edit permission or global dashboard scope)
    if (user.is_superuser and getattr(user, 'role', '') != 'manager') or getattr(user, 'role', '') in ['admin', 'system_owner', 'super_admin'] or PermissionEngine.evaluate(user, 'accounts.view').allowed or (PermissionEngine.evaluate(user, 'dashboard.view').allowed and PermissionEngine.get_effective_scope(user, 'dashboard.view') == DataScope.GLOBAL):
        return 'admin'

    # HR check (employees.view and leave.approve permission)
    if getattr(user, 'role', '') == 'hr' or (PermissionEngine.evaluate(user, 'employees.view').allowed and PermissionEngine.evaluate(user, 'leave.approve').allowed):
        return 'hr'

    # Manager check (projects/leave approve permission or user has direct reports)
    is_manager_role = getattr(user, 'role', '') == 'manager' or PermissionEngine.evaluate(user, 'leave.approve').allowed or PermissionEngine.evaluate(user, 'projects.approve').allowed
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

    from apps.attendance.views import get_employee

    emp_profile = get_employee(user) or getattr(user, 'employee_profile', None)
    emp_master = getattr(user, 'employee_master', None)

    # Today's attendance
    if emp_profile:
        data['today_attendance'] = Attendance.objects.select_related('employee', 'employee__branch').filter(
            employee=emp_profile, date=today
        ).first()
        data['active_session'] = Attendance.objects.filter(
            employee=emp_profile,
            date=today,
            attendance_type='check_in',
            check_out_time__isnull=True,
            is_expired=False
        ).first()
        data['today_sessions_count'] = Attendance.objects.filter(
            employee=emp_profile,
            date=today,
            attendance_type='check_in',
            is_expired=False
        ).count()
        today_sessions = Attendance.objects.filter(
            employee=emp_profile,
            date=today,
            attendance_type='check_in',
            is_expired=False
        ).order_by('check_in_time')
        data['today_sessions'] = today_sessions
        
        import decimal
        total_hours = decimal.Decimal('0.00')
        for s in today_sessions:
            if s.total_hours:
                total_hours += s.total_hours
        data['today_total_hours'] = total_hours

        from apps.attendance.models import AttendanceCorrectionRequest
        data['pending_correction_count'] = AttendanceCorrectionRequest.objects.filter(
            attendance__employee=emp_profile,
            status='pending'
        ).count()
        from apps.attendance.schedule_utils import get_branch_schedule
        schedule = get_branch_schedule(emp_profile)
        if schedule:
            start_t = schedule.office_start_time
            end_t = schedule.office_end_time
            start_str = start_t if isinstance(start_t, str) else start_t.strftime('%I:%M %p')
            end_str = end_t if isinstance(end_t, str) else end_t.strftime('%I:%M %p')
            data['shift_info'] = f"{start_str} - {end_str}"
        else:
            data['shift_info'] = "Standard Shift"
        data['recent_attendance'] = Attendance.objects.filter(
            employee=emp_profile,
            attendance_type='check_in',
            is_expired=False
        ).order_by('-date', '-check_in_time')[:5]
    else:
        data['today_attendance'] = None
        data['active_session'] = None
        data['today_sessions_count'] = 0
        data['today_sessions'] = []
        data['today_total_hours'] = 0.0
        data['pending_correction_count'] = 0
        data['shift_info'] = "Standard Shift"
        data['recent_attendance'] = []

    from apps.branches.models import Holiday
    data['next_holiday'] = Holiday.objects.filter(date__gte=today).order_by('date').first()

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
        
        # Profile completion & expiring documents alerts for self
        data['my_completion_percentage'] = emp_master.get_completion_percentage()
        data['my_next_wizard_step'] = emp_master.get_next_wizard_step()
        
        soon_threshold = today + timedelta(days=30)
        data['my_expiring_documents'] = EmployeeDocument.objects.filter(
            employee_master=emp_master,
            expiry_date__isnull=False,
            expiry_date__gte=today,
            expiry_date__lte=soon_threshold,
            is_active=True
        ).order_by('expiry_date')
    else:
        data['personal_timeline'] = []
        data['my_completion_percentage'] = 100
        data['my_next_wizard_step'] = 8
        data['my_expiring_documents'] = []

    # Unified keys for shared <c-staff-dashboard> composition
    data['employee'] = emp_profile
    data['is_checked_in'] = bool(data.get('active_session'))
    data['pending_tasks'] = data.get('assigned_tasks', [])
    data['unread_notifications'] = len(data.get('my_notifications', []))
    if emp_profile:
        try:
            data['total_leave_left'] = emp_profile.total_leave_left_by_year[today.year]
        except Exception:
            data['total_leave_left'] = 0
        data['pending_leave_count'] = LeaveRequest.objects.filter(employee=emp_profile, status='pending').count()
        data['field_visits'] = Attendance.objects.filter(
            employee=emp_profile,
            date=today,
            attendance_type='field_visit',
            is_expired=False
        ).order_by('-check_in_time')
    else:
        data['total_leave_left'] = 0
        data['pending_leave_count'] = 0
        data['field_visits'] = []

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
    from apps.employees.hierarchy_services import OrgHierarchyService
    data['subordinate_count_direct'] = OrgHierarchyService.get_direct_reports(emp_master).count()
    data['subordinate_count_all'] = OrgHierarchyService.get_all_subordinates(emp_master).count()

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
        employee__in=team_profiles, status='pending_manager'
    ).order_by('-requested_at')[:5]

    # Projects managed by this user
    emp_profile = getattr(user, 'employee_profile', None)
    if emp_profile:
        data['managed_projects'] = Project.objects.select_related('branch').filter(
            project_managers=emp_profile
        )[:5]
    else:
        data['managed_projects'] = []

    # Counts
    present_set = Attendance.objects.filter(employee__in=team_profiles, date=today, status='on_time', attendance_type='check_in', is_expired=False).values_list('employee_id', flat=True)
    late_set = Attendance.objects.filter(employee__in=team_profiles, date=today, status='late', attendance_type='check_in', is_expired=False).values_list('employee_id', flat=True)
    
    data['team_present_count'] = len(set(present_set))
    data['team_late_count'] = len(set(late_set))
    
    checked_in_ids = set(present_set) | set(late_set)
    data['team_absent_count'] = team_profiles.exclude(id__in=checked_in_ids).count()

    # Corrections & OT
    from apps.attendance.models import AttendanceCorrectionRequest
    data['pending_corrections_count'] = AttendanceCorrectionRequest.objects.filter(
        attendance__employee__in=team_profiles,
        status='pending'
    ).count()
    data['pending_ot_count'] = Attendance.objects.filter(
        employee__in=team_profiles,
        ot_status='pending',
        is_expired=False
    ).count()

    # Live Locations
    from apps.attendance.models import AttendanceLocation
    active_sessions = Attendance.objects.filter(
        employee__in=team_profiles,
        date=today,
        check_out_time__isnull=True,
        is_expired=False
    )
    data['live_locations'] = AttendanceLocation.objects.filter(
        attendance__in=active_sessions
    ).select_related('attendance', 'attendance__employee').order_by('-timestamp')[:10]

    # Today's Timeline
    data['team_timeline'] = Attendance.objects.filter(
        employee__in=team_profiles,
        date=today,
        is_expired=False
    ).select_related('employee').order_by('-check_in_time')[:15]

    # Heatmap (distribution of statuses for the last 7 days)
    seven_days_ago = today - timedelta(days=7)
    data['heatmap_data'] = list(Attendance.objects.filter(
        employee__in=team_profiles,
        date__gte=seven_days_ago,
        date__lte=today,
        is_expired=False
    ).values('date', 'status').annotate(count=Count('id')).order_by('date'))

    return data


def get_hr_dashboard_data(user):
    """
    Scoped HR analytical metrics and alerts aligned with canonical employee-day reporting.
    """
    today = timezone.localdate()
    data = {}

    from apps.admin_panel.analytics_service import _get_authorized_employees
    from apps.attendance.reporting_service import get_daily_report_data
    from apps.attendance.scoping import get_scoped_employee_queryset
    from apps.accounts.engine import PermissionEngine
    from apps.accounts.models import DataScope
    from apps.employees.hierarchy_services import OrgHierarchyService

    authorized_profiles = _get_authorized_employees(user)
    is_global_scope = user.is_superuser
    if not is_global_scope:
        for perm_code in ('reports.view', 'attendance.view', 'dashboard.view'):
            res = PermissionEngine.evaluate(user, perm_code, action_type='view')
            if res.allowed and res.data_scope in (DataScope.GLOBAL, DataScope.COMPANY):
                is_global_scope = True
                break

    if is_global_scope:
        scoped_master_qs = Employee.objects.all()
        data['org_analytics'] = OrgHierarchyService.get_org_analytics()
    else:
        master_ids = authorized_profiles.filter(master_employee_id__isnull=False).values('master_employee_id')
        scoped_master_qs = Employee.objects.filter(id__in=master_ids)
        data['org_analytics'] = {}

    # Headcount & status breakdown (scoped)
    counts = scoped_master_qs.aggregate(
        total=Count('id'),
        active=Count('id', filter=Q(status=EmployeeStatus.ACTIVE)),
        probation=Count('id', filter=Q(status=EmployeeStatus.PROBATION)),
        draft=Count('id', filter=Q(status=EmployeeStatus.DRAFT)),
        pending_approval=Count('id', filter=Q(status=EmployeeStatus.PENDING_APPROVAL)),
        resigned=Count('id', filter=Q(status=EmployeeStatus.RESIGNED)),
        terminated=Count('id', filter=Q(status=EmployeeStatus.TERMINATED)),
        archived=Count('id', filter=Q(status=EmployeeStatus.ARCHIVED)),
    )
    data['total_employees'] = counts['total']
    data['active_employees_count'] = counts['active']
    data['probation_employees_count'] = counts['probation']
    data['draft_employees_count'] = counts['draft']
    data['pending_approval_employees_count'] = counts['pending_approval']
    data['resigned_employees_count'] = counts['resigned']
    data['terminated_employees_count'] = counts['terminated']
    data['archived_employees_count'] = counts['archived']

    # Incomplete profiles (scoped)
    all_employees = list(scoped_master_qs.select_related('department', 'designation', 'branch'))
    emp_ids = [e.pk for e in all_employees]
    if emp_ids:
        from apps.employees.models import AssetAssignment
        doc_emp_ids = set(EmployeeDocument.objects.filter(
            employee_master_id__in=emp_ids,
            is_active=True,
            is_archived=False
        ).filter(
            Q(expiry_date__isnull=True) | Q(expiry_date__gte=today)
        ).values_list('employee_master_id', flat=True))

        asset_emp_ids = set(AssetAssignment.objects.filter(
            employee_id__in=emp_ids,
            returned_date__isnull=True
        ).values_list('employee_id', flat=True))

        for e in all_employees:
            e._has_valid_docs = e.pk in doc_emp_ids
            e._has_unreturned_assets = e.pk in asset_emp_ids
    incomplete_employees = [e for e in all_employees if e.get_completion_percentage() < 100]
    data['incomplete_profiles_count'] = len(incomplete_employees)
    data['incomplete_profiles'] = incomplete_employees[:5]

    # Department breakdown (scoped)
    data['dept_breakdown'] = scoped_master_qs.values(
        'department__name'
    ).annotate(count=Count('id')).order_by('-count')[:5]

    # Canonical employee-day attendance summary for today (single bulk-loaded call)
    daily_report = get_daily_report_data(
        report_date=today,
        employee_queryset=authorized_profiles,
    )
    rows = daily_report['rows']

    present_count = sum(1 for r in rows if r['is_present'])
    late_count = sum(1 for r in rows if r['is_late'])
    absent_count = sum(1 for r in rows if r['is_absent'])
    on_leave_count = sum(1 for r in rows if r['status'] == 'on_leave')
    pending_count = sum(1 for r in rows if r['status'] == 'scheduled' and r['status_display'] == 'Pending')
    review_needed_count = sum(1 for r in rows if r.get('needs_review') or r.get('eligibility_unresolved'))
    missing_checkout_count = sum(1 for r in rows if r['is_present'] and r.get('is_missing_checkout'))
    leave_conflict_count = sum(1 for r in rows if r['is_present'] and r.get('has_leave_conflict'))
    holiday_work_count = sum(1 for r in rows if r['is_present'] and r['day_calc'].get('is_holiday'))
    remote_count = sum(1 for r in rows if r['is_present'] and r['field_visits'])
    ot_count = sum(1 for r in rows if (r['day_calc'].get('overtime_minutes') or 0) > 0)

    data['today_attendance_count'] = present_count
    data['today_late_count'] = late_count

    data['hr_present_count'] = present_count
    data['hr_late_count'] = late_count
    data['hr_remote_count'] = remote_count
    data['hr_holiday_count'] = holiday_work_count
    data['hr_ot_count'] = ot_count
    data['hr_leave_count'] = on_leave_count
    data['hr_absent_count'] = absent_count
    data['hr_pending_count'] = pending_count
    data['hr_review_needed_count'] = review_needed_count
    data['hr_missing_checkout_count'] = missing_checkout_count
    data['hr_leave_conflict_count'] = leave_conflict_count

    # On Leave Today (scoped to leave.view if available, else authorized_profiles)
    leave_scoped_profiles = get_scoped_employee_queryset(
        user, permission_code='leave.view', action='view', base_qs=authorized_profiles
    )
    today_leaves = list(LeaveRequest.objects.select_related('employee', 'leave_type').filter(
        employee__in=leave_scoped_profiles,
        start_date__lte=today,
        end_date__gte=today,
        status='approved',
    ))
    data['on_leave_today'] = today_leaves[:5]

    from apps.attendance.models import AttendanceCorrectionRequest, ForgotCheckoutRequest
    data['hr_correction_count'] = AttendanceCorrectionRequest.objects.filter(
        attendance__employee__in=authorized_profiles,
        requested_at__date=today,
    ).count()
    data['hr_forgot_checkout_count'] = ForgotCheckoutRequest.objects.filter(
        attendance__employee__in=authorized_profiles,
        requested_at__date=today,
    ).count()

    # Pending Lifecycle Requests Count (scoped)
    data['pending_lifecycle_requests_count'] = LifecycleTransitionRequest.objects.filter(
        employee__in=scoped_master_qs,
        review_status='pending',
    ).count()

    # Expiring Documents (scoped)
    soon_threshold = today + timedelta(days=30)
    data['expiring_documents'] = EmployeeDocument.objects.select_related(
        'employee_master', 'employee'
    ).filter(
        employee_master__in=scoped_master_qs,
        expiry_date__isnull=False,
        expiry_date__gte=today,
        expiry_date__lte=soon_threshold,
        is_active=True
    ).order_by('expiry_date')[:5]

    # Probation ending soon (scoped)
    data['probation_list'] = scoped_master_qs.select_related(
        'department', 'designation', 'branch'
    ).filter(
        status=EmployeeStatus.PROBATION
    ).order_by('joined_date')[:5]

    # Upcoming birthdays this month (scoped)
    data['upcoming_birthdays'] = scoped_master_qs.filter(
        dob__month=today.month
    ).order_by('dob__day')[:5]

    # Recent joiners (joined last 30 days, scoped)
    thirty_days_ago = today - timedelta(days=30)
    data['recent_joiners'] = scoped_master_qs.select_related(
        'department', 'designation', 'branch'
    ).filter(
        joined_date__gte=thirty_days_ago
    ).order_by('-joined_date')[:5]

    return data


def get_admin_dashboard_data(user, request=None):
    """
    Admin dashboard: HR org-wide metrics + System security & session stats.
    """
    data = get_hr_dashboard_data(user)
    today = timezone.localdate()

    # Active sessions & security metrics
    data['active_sessions_count'] = UserSession.objects.filter(is_active=True).count()
    data['trusted_devices_count'] = TrustedDevice.objects.count()
    from apps.notifications.models import AuditLog
    last_24h = timezone.now() - timedelta(hours=24)
    data['recent_audit_events_count'] = AuditLog.objects.filter(timestamp__gte=last_24h).count()

    # Sync and GPS metrics
    from apps.attendance.models import SyncLog, AttendanceLocation, AttendanceCorrectionRequest
    from django.db.models import Sum, Q
    data['sync_failed_count'] = SyncLog.objects.aggregate(total_failed=Sum('records_failed'))['total_failed'] or 0
    # Attendance pending OT (synced_at IS NULL is normal online attendance, not an offline queue)
    att_aggs = Attendance.objects.aggregate(
        pending_ot=Count('id', filter=Q(ot_status='pending', is_expired=False))
    )
    data['offline_queue_size'] = None
    data['offline_queue_unavailable'] = True
    pending_ot = att_aggs['pending_ot']
    data['gps_issues_count'] = Attendance.objects.filter(
        Q(gps_quality__in=['poor', 'missing']) |
        Q(note__icontains='GEOFENCE WARNING') |
        Q(note__icontains='outside geofence'),
        date=today,
        is_expired=False
    ).distinct().count()

    # Late %
    today_attendances = data.pop('_today_attendances', None)
    if today_attendances is None:
        today_attendances = list(Attendance.objects.filter(date=today).values(
            'employee_id', 'status', 'attendance_type', 'type', 'overtime_minutes', 'is_expired'
        ))
    today_checkins_total = sum(1 for a in today_attendances if not a['is_expired'] and a['attendance_type'] == 'check_in')
    today_late_total = sum(1 for a in today_attendances if not a['is_expired'] and a['status'] == 'late' and a['attendance_type'] == 'check_in')
    data['late_percentage'] = int((today_late_total / today_checkins_total * 100)) if today_checkins_total > 0 else 0

    # Trend (Last 15 days)
    fifteen_days_ago = today - timedelta(days=15)
    data['attendance_trend'] = list(Attendance.objects.filter(
        date__gte=fifteen_days_ago,
        date__lte=today,
        attendance_type='check_in',
        is_expired=False
    ).values('date').annotate(count=Count('id')).order_by('date'))

    # Top OT today
    data['top_ot_employees'] = Attendance.objects.filter(
        date=today,
        overtime_minutes__gt=0,
        is_expired=False
    ).select_related('employee', 'employee__user', 'employee__master_employee').order_by('-overtime_minutes')[:5]

    # Expense & Attendance Analysis
    from apps.expense.models import ExpenseCategory
    from django.db.models import Sum, Q, Avg
    import math

    # 1. Total Expenses Approved vs Pending + pending count in 1 query
    expense_aggs = Expense.objects.aggregate(
        approved=Sum('amount', filter=Q(status='approved')),
        pending=Sum('amount', filter=Q(status__in=['pending_manager', 'pending_finance', 'pending_accounts'])),
        pending_count=Count('id', filter=Q(status__in=['pending_manager', 'pending_finance', 'pending_accounts']))
    )
    data['total_approved_expenses'] = float(expense_aggs['approved'] or 0.0)
    data['total_pending_expenses'] = float(expense_aggs['pending'] or 0.0)
    pending_expenses = expense_aggs['pending_count'] or 0

    # Pending approvals total count
    if request and hasattr(request, '_pending_leave_count'):
        pending_leaves = request._pending_leave_count
    else:
        pending_leaves = LeaveRequest.objects.filter(status='pending').count()
        if request:
            request._pending_leave_count = pending_leaves
    pending_corrections = AttendanceCorrectionRequest.objects.filter(status='pending').count()
    data['pending_approvals_count'] = pending_leaves + pending_expenses + pending_corrections + pending_ot

    # 2. Expense Category breakdown
    expenses_by_cat = Expense.objects.filter(status='approved').values('category__name').annotate(total=Sum('amount')).order_by('-total')
    category_labels = []
    category_values = []
    for ec in expenses_by_cat:
        category_labels.append(ec['category__name'] or 'Uncategorized')
        category_values.append(float(ec['total']))
    data['expense_category_labels'] = category_labels
    data['expense_category_values'] = category_values

    return data
