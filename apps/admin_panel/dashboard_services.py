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
    if user.is_superuser or PermissionEngine.evaluate(user, 'accounts.view').allowed or (PermissionEngine.evaluate(user, 'dashboard.view').allowed and PermissionEngine.get_effective_scope(user, 'dashboard.view') == DataScope.GLOBAL):
        return 'admin'

    # HR check (employees.view and leave.approve permission)
    if PermissionEngine.evaluate(user, 'employees.view').allowed and PermissionEngine.evaluate(user, 'leave.approve').allowed:
        return 'hr'

    # Manager check (projects/leave approve permission or user has direct reports)
    is_manager_role = PermissionEngine.evaluate(user, 'leave.approve').allowed or PermissionEngine.evaluate(user, 'projects.approve').allowed
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
    Org-wide HR analytical metrics and alerts.
    """
    today = timezone.localdate()
    data = {}
    from apps.employees.hierarchy_services import OrgHierarchyService
    data['org_analytics'] = OrgHierarchyService.get_org_analytics()

    # Headcount & status breakdown
    data['total_employees'] = Employee.objects.count()
    data['active_employees_count'] = Employee.objects.filter(status=EmployeeStatus.ACTIVE).count()
    data['probation_employees_count'] = Employee.objects.filter(status=EmployeeStatus.PROBATION).count()
    data['draft_employees_count'] = Employee.objects.filter(status=EmployeeStatus.DRAFT).count()
    data['pending_approval_employees_count'] = Employee.objects.filter(status=EmployeeStatus.PENDING_APPROVAL).count()
    data['resigned_employees_count'] = Employee.objects.filter(status=EmployeeStatus.RESIGNED).count()
    data['terminated_employees_count'] = Employee.objects.filter(status=EmployeeStatus.TERMINATED).count()
    data['archived_employees_count'] = Employee.objects.filter(status=EmployeeStatus.ARCHIVED).count()

    # Incomplete profiles
    all_employees = Employee.objects.select_related('department', 'designation', 'branch').all()
    incomplete_employees = [e for e in all_employees if e.get_completion_percentage() < 100]
    data['incomplete_profiles_count'] = len(incomplete_employees)
    data['incomplete_profiles'] = incomplete_employees[:5]

    # Department breakdown
    data['dept_breakdown'] = Employee.objects.values(
        'department__name'
    ).annotate(count=Count('id')).order_by('-count')[:5]

    # Org-wide Attendance Today
    data['today_attendance_count'] = Attendance.objects.filter(date=today, status='present').count()
    data['today_late_count'] = Attendance.objects.filter(date=today, status='late').count()

    # HR Breakdown
    active_profiles_qs = EmployeeProfile.objects.filter(master_employee__status=EmployeeStatus.ACTIVE)
    data['hr_present_count'] = Attendance.objects.filter(date=today, status__in=['on_time', 'present'], attendance_type='check_in', is_expired=False).values('employee').distinct().count()
    data['hr_late_count'] = Attendance.objects.filter(date=today, status='late', attendance_type='check_in', is_expired=False).values('employee').distinct().count()
    data['hr_leave_count'] = LeaveRequest.objects.filter(start_date__lte=today, end_date__gte=today, status='approved').values('employee').distinct().count()
    data['hr_remote_count'] = Attendance.objects.filter(date=today, type='field', is_expired=False).values('employee').distinct().count()
    data['hr_holiday_count'] = Attendance.objects.filter(date=today, status='holiday_attendance', is_expired=False).values('employee').distinct().count()

    from apps.attendance.models import AttendanceCorrectionRequest, ForgotCheckoutRequest
    data['hr_correction_count'] = AttendanceCorrectionRequest.objects.filter(requested_at__date=today).count()
    data['hr_ot_count'] = Attendance.objects.filter(date=today, overtime_minutes__gt=0, is_expired=False).count()
    data['hr_forgot_checkout_count'] = ForgotCheckoutRequest.objects.filter(requested_at__date=today).count()

    checked_in_ids = Attendance.objects.filter(date=today, attendance_type='check_in', is_expired=False).values_list('employee_id', flat=True)
    on_leave_ids = LeaveRequest.objects.filter(start_date__lte=today, end_date__gte=today, status='approved').values_list('employee_id', flat=True)
    data['hr_absent_count'] = active_profiles_qs.exclude(id__in=set(checked_in_ids) | set(on_leave_ids)).count()

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
    data['offline_queue_size'] = Attendance.objects.filter(synced_at__isnull=True).count()
    data['gps_issues_count'] = Attendance.objects.filter(
        Q(gps_quality__in=['poor', 'missing']) |
        Q(note__icontains='GEOFENCE WARNING') |
        Q(note__icontains='outside geofence'),
        date=today,
        is_expired=False
    ).distinct().count()

    # Late %
    today_checkins_total = Attendance.objects.filter(date=today, attendance_type='check_in', is_expired=False).count()
    today_late_total = Attendance.objects.filter(date=today, status='late', attendance_type='check_in', is_expired=False).count()
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
    ).select_related('employee').order_by('-overtime_minutes')[:5]

    # Pending approvals total count
    pending_leaves = LeaveRequest.objects.filter(status='pending').count()
    pending_expenses = Expense.objects.filter(status__in=['pending_manager', 'pending_finance', 'pending_accounts']).count()
    pending_corrections = AttendanceCorrectionRequest.objects.filter(status='pending').count()
    pending_ot = Attendance.objects.filter(ot_status='pending', is_expired=False).count()
    data['pending_approvals_count'] = pending_leaves + pending_expenses + pending_corrections + pending_ot

    # Data Science: Expense & Attendance Analysis
    from apps.expense.models import ExpenseCategory
    from django.db.models import Sum, Q, Avg
    import math

    # 1. Total Expenses Approved vs Pending
    data['total_approved_expenses'] = float(Expense.objects.filter(status='approved').aggregate(total=Sum('amount'))['total'] or 0.0)
    data['total_pending_expenses'] = float(Expense.objects.filter(status__in=['pending_manager', 'pending_finance', 'pending_accounts']).aggregate(total=Sum('amount'))['total'] or 0.0)

    # 2. Expense Category breakdown
    expenses_by_cat = Expense.objects.filter(status='approved').values('category__name').annotate(total=Sum('amount')).order_by('-total')
    category_labels = []
    category_values = []
    for ec in expenses_by_cat:
        category_labels.append(ec['category__name'] or 'Uncategorized')
        category_values.append(float(ec['total']))
    data['expense_category_labels'] = category_labels
    data['expense_category_values'] = category_values

    # 3. Predict/Forecast Tomorrow's Attendance
    seven_days_ago = today - timedelta(days=7)
    past_7_days_atts = Attendance.objects.filter(
        date__range=(seven_days_ago, today),
        attendance_type='check_in',
        is_expired=False
    ).values('date').annotate(count=Count('id')).order_by('date')
    
    n = len(past_7_days_atts)
    if n >= 3:
        xs = list(range(1, n + 1))
        ys = [item['count'] for item in past_7_days_atts]
        sum_x = sum(xs)
        sum_y = sum(ys)
        sum_xy = sum(x * y for x, y in zip(xs, ys))
        sum_x_squared = sum(x**2 for x in xs)
        
        denominator = (n * sum_x_squared - sum_x**2)
        if denominator != 0:
            m = (n * sum_xy - sum_x * sum_y) / denominator
            c = (sum_y - m * sum_x) / n
            forecasted_count = max(0, m * (n + 1) + c)
        else:
            forecasted_count = sum_y / n
    else:
        avg_checkins = Attendance.objects.filter(
            date__range=(today - timedelta(days=5), today),
            attendance_type='check_in',
            is_expired=False
        ).values('date').annotate(count=Count('id')).aggregate(avg=Avg('count'))['avg'] or 0
        forecasted_count = avg_checkins

    total_emp_count = Employee.objects.count() or 1
    data['forecasted_attendance_count'] = round(forecasted_count)
    data['forecasted_attendance_rate'] = min(100.0, round((float(forecasted_count) / total_emp_count) * 100, 1))

    # 4. Correlation between Daily Approved Expenses and Daily Check-ins (over last 10 days)
    ten_days_ago = today - timedelta(days=10)
    daily_expenses_qs = Expense.objects.filter(
        requested_at__date__range=(ten_days_ago, today),
        status='approved'
    ).values('requested_at__date').annotate(total_amount=Sum('amount'))
    expense_map = {item['requested_at__date']: float(item['total_amount']) for item in daily_expenses_qs}

    daily_checkins_qs = Attendance.objects.filter(
        date__range=(ten_days_ago, today),
        attendance_type='check_in',
        is_expired=False
    ).values('date').annotate(count=Count('id'))
    checkin_map = {item['date']: item['count'] for item in daily_checkins_qs}

    x_vals = []
    y_vals = []
    for offset in range(11):
        d_date = ten_days_ago + timedelta(days=offset)
        if d_date in checkin_map:
            x_vals.append(float(checkin_map[d_date]))
            y_vals.append(expense_map.get(d_date, 0.0))

    n_corr = len(x_vals)
    correlation_coefficient = 0.0
    correlation_strength = "No Data"
    if n_corr >= 3:
        sum_x = sum(x_vals)
        sum_y = sum(y_vals)
        sum_x_sq = sum(x**2 for x in x_vals)
        sum_y_sq = sum(y**2 for y in y_vals)
        sum_xy = sum(x * y for x, y in zip(x_vals, y_vals))
        
        num = (n_corr * sum_xy) - (sum_x * sum_y)
        den_val = ((n_corr * sum_x_sq) - (sum_x**2)) * ((n_corr * sum_y_sq) - (sum_y**2))
        if den_val > 0:
            den = math.sqrt(den_val)
            correlation_coefficient = round(num / den, 3)
            
        if correlation_coefficient > 0.6:
            correlation_strength = "Strong Positive Correlation"
        elif correlation_coefficient > 0.2:
            correlation_strength = "Moderate Positive Correlation"
        elif correlation_coefficient < -0.6:
            correlation_strength = "Strong Negative Correlation"
        elif correlation_coefficient < -0.2:
            correlation_strength = "Moderate Negative Correlation"
        else:
            correlation_strength = "Weak / No Correlation"
            
    data['expense_attendance_correlation'] = correlation_coefficient
    data['correlation_strength'] = correlation_strength

    return data
