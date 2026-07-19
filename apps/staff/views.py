import datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.utils import timezone
import calendar
from apps.attendance.models import Attendance
from apps.employees.models import EmployeeProfile


def check_staff_role(user):
    return user.is_authenticated and user.role in ['staff', 'manager']


@login_required
def home(request):
    if not check_staff_role(request.user):
        return redirect('accounts:login')

    employee = getattr(request.user, 'employee_profile', None)
    today = timezone.localdate()
    field_visits = Attendance.objects.filter(
        employee=employee,
        date=today,
        attendance_type='field_visit',
        is_expired=False
    ).order_by('-check_in_time')

    # Check if there is an active check-in
    active_session = Attendance.objects.filter(
        employee=employee,
        date=today,
        attendance_type='check_in',
        check_out_time__isnull=True,
        is_expired=False
    ).exists()

    total_leave_left = None
    pending_leave_count = 0
    pending_tasks = []
    if employee:
        total_leave_left = employee.total_leave_left_by_year[today.year]
        from apps.leave.models import LeaveRequest
        pending_leave_count = LeaveRequest.objects.filter(employee=employee, status='pending').count()
        from apps.projects.models import ProjectTask
        pending_tasks = (
            ProjectTask.objects
            .filter(responsible_person=employee)
            .exclude(status='Completed')
            .select_related('project')
            .order_by('planned_finish')[:10]
        )

    return render(request, 'staff/home.html', {
        'employee': employee,
        'field_visits': field_visits,
        'is_checked_in': active_session,
        'total_leave_left': total_leave_left,
        'pending_leave_count': pending_leave_count,
        'pending_tasks': pending_tasks,
    })



@login_required
def attendance_card(request):
    if not check_staff_role(request.user):
        return redirect('accounts:login')

    employee = getattr(request.user, 'employee_profile', None)
    today = timezone.localdate()

    # Active (unclosed) session
    active_session = Attendance.objects.filter(
        employee=employee,
        date=today,
        attendance_type='check_in',
        check_out_time__isnull=True,
        is_expired=False
    ).first()

    # All completed sessions today (for history strip)
    all_sessions_today = Attendance.objects.filter(
        employee=employee,
        date=today,
        attendance_type='check_in',
        is_expired=False
    ).order_by('check_in_time')

    # Monthly stats
    now = timezone.now()
    start_date = now.replace(day=1).date()
    _, last_day = calendar.monthrange(now.year, now.month)
    end_date = now.replace(day=last_day).date()

    attendances_this_month = Attendance.objects.filter(
        employee=employee,
        date__gte=start_date,
        date__lte=end_date,
        attendance_type='check_in',
        is_expired=False
    )

    stats = {
        'present': attendances_this_month.filter(status__in=['on_time', 'late']).values('date').distinct().count(),
        'absent':  attendances_this_month.filter(status='absent').count(),
        'late':    attendances_this_month.filter(status='late').count(),
    }

    context = {
        'employee':           employee,
        'active_session':     active_session,
        'all_sessions_today': all_sessions_today,
        'stats':              stats,
        'now':                timezone.localtime(),
        # Pass tracking interval (minutes) for the JS auto-sync timer
        'tracking_interval':  employee.tracking_interval if employee else 0,
    }
    return render(request, 'staff/partials/attendance_card.html', context)


@login_required
def check_in_page(request):
    if not check_staff_role(request.user):
        return redirect('accounts:login')

    employee = getattr(request.user, 'employee_profile', None)
    today = timezone.localdate()

    # Block only if there is an active (unclosed) session
    active = Attendance.objects.filter(
        employee=employee,
        date=today,
        attendance_type='check_in',
        check_out_time__isnull=True,
        is_expired=False
    ).first()

    if active:
        return redirect('staff:home')

    return render(request, 'staff/check_in.html', {'employee': employee})


@login_required
def attendance_history(request):
    if not check_staff_role(request.user):
        return redirect('accounts:login')

    employee = getattr(request.user, 'employee_profile', None)

    month_str = request.GET.get('month')
    if month_str:
        try:
            target_date = datetime.datetime.strptime(month_str, '%Y-%m').date()
        except ValueError:
            target_date = timezone.localdate().replace(day=1)
    else:
        target_date = timezone.localdate().replace(day=1)

    start_date = target_date
    _, last_day = calendar.monthrange(target_date.year, target_date.month)
    end_date = target_date.replace(day=last_day)

    prev_month = (start_date - datetime.timedelta(days=1)).replace(day=1)
    next_month = (end_date + datetime.timedelta(days=1))

    attendances = Attendance.objects.filter(
        employee=employee,
        date__gte=start_date,
        date__lte=end_date,
        is_expired=False
    ).order_by('-date', '-check_in_time')

    stats = {
        'present': attendances.filter(attendance_type='check_in', status__in=['on_time', 'late']).values('date').distinct().count(),
        'absent':  attendances.filter(status='absent').count(),
        'late':    attendances.filter(attendance_type='check_in', status='late').count(),
        'field':   attendances.filter(type='field').count()
    }

    # Fetch Leave Requests
    from apps.leave.models import LeaveRequest
    leave_requests = LeaveRequest.objects.select_related('leave_type').filter(
        employee=employee,
        status__in=['pending', 'approved'],
        start_date__lte=end_date,
        end_date__gte=start_date
    )
    leave_by_date = {}
    for req in leave_requests:
        curr = req.start_date
        while curr <= req.end_date:
            leave_by_date[curr] = {
                'status': req.status,
                'leave_type_name': req.leave_type.name if req.leave_type else 'Leave'
            }
            curr += datetime.timedelta(days=1)

    # Attach leave info to real attendances and group by date
    from collections import defaultdict
    atts_by_date = defaultdict(list)
    for att in attendances:
        att.leave_info = leave_by_date.get(att.date)
        atts_by_date[att.date].append(att)

    # Build the combined list of day rows to display in the template
    combined_records = []
    today = timezone.localdate()
    curr = end_date
    while curr >= start_date:
        has_atts = curr in atts_by_date
        has_leave = curr in leave_by_date
        
        if has_atts or has_leave or curr <= today:
            if has_atts:
                for att in atts_by_date[curr]:
                    combined_records.append(att)
            else:
                placeholder = {
                    'date': curr,
                    'status': 'absent' if curr < today else 'no_attendance',
                    'check_in_time': None,
                    'check_out_time': None,
                    'total_hours': None,
                    'type': '--',
                    'is_placeholder': True,
                    'leave_info': leave_by_date.get(curr)
                }
                combined_records.append(placeholder)
        curr -= datetime.timedelta(days=1)

    context = {
        'employee':         employee,
        'attendances':      combined_records,
        'leave_by_date':    leave_by_date,
        'stats':            stats,
        'current_month':    target_date.strftime('%B %Y'),
        'current_month_val': target_date.strftime('%Y-%m'),
        'prev_month_val':   prev_month.strftime('%Y-%m'),
        'next_month_val':   next_month.strftime('%Y-%m'),
    }

    if request.headers.get('HX-Request'):
        return render(request, 'staff/partials/attendance_list.html', context)

    return render(request, 'staff/attendance.html', context)


@login_required
def profile(request):
    if not check_staff_role(request.user):
        return redirect('accounts:login')

    employee = getattr(request.user, 'employee_profile', None)

    now = timezone.now()
    start_date = now.replace(day=1).date()
    _, last_day = calendar.monthrange(now.year, now.month)
    end_date = now.replace(day=last_day).date()

    attendances_this_month = Attendance.objects.filter(
        employee=employee,
        date__gte=start_date,
        date__lte=end_date,
        is_expired=False
    )

    stats = {
        'present': attendances_this_month.filter(attendance_type='check_in', status__in=['on_time', 'late']).values('date').distinct().count(),
        'absent':  attendances_this_month.filter(status='absent').count(),
        'late':    attendances_this_month.filter(attendance_type='check_in', status='late').count(),
        'field':   attendances_this_month.filter(type='field').count()
    }

    # Attendance history (last 30 days, paginated)
    today = timezone.localdate()
    thirty_days_ago = today - datetime.timedelta(days=30)
    attendance_history_qs = Attendance.objects.filter(
        employee=employee,
        date__gte=thirty_days_ago,
        date__lte=today,
        is_expired=False
    ).select_related('employee', 'employee__branch').order_by('-date', '-check_in_time')

    from django.core.paginator import Paginator
    paginator = Paginator(attendance_history_qs, 10)
    page_number = request.GET.get('page')
    attendance_page = paginator.get_page(page_number)

    # Leave balances (reuse existing leave logic)
    from apps.leave.models import get_cached_leave_types, LeaveBalance
    leave_types = get_cached_leave_types()
    leave_balances = []
    
    if employee:
        emp_balances = list(LeaveBalance.objects.filter(employee=employee, year=today.year).select_related('leave_type'))
        emp_rules = list(employee.leave_rules.all())
        
        for lt in leave_types:
            balance = next((b for b in emp_balances if b.leave_type_id == lt.id), None)
            if balance:
                leave_balances.append({
                    'type': lt,
                    'total': balance.total_days,
                    'used': balance.used_days,
                    'remaining': balance.remaining_days
                })
            else:
                rule = next((r for r in emp_rules if r.leave_type_id == lt.id), None)
                limit = rule.days_per_year if rule else lt.default_days_per_year
                leave_balances.append({
                    'type': lt,
                    'total': limit,
                    'used': 0,
                    'remaining': limit
                })

    # Assigned ProjectTasks
    from apps.projects.models import ProjectTask
    from django.contrib.contenttypes.models import ContentType
    from apps.notifications.models import ActivityLog
    assigned_tasks = []
    activities = []
    if employee:
        assigned_tasks = ProjectTask.objects.filter(
            responsible_person=employee
        ).select_related('project', 'project__branch').order_by('status', 'planned_finish')
        emp_task_ids = assigned_tasks.values_list('id', flat=True)
        ct_task = ContentType.objects.get_for_model(ProjectTask)
        activities = ActivityLog.objects.filter(
            target_content_type=ct_task,
            target_object_id__in=emp_task_ids
        ).select_related('actor', 'actor__employee_profile').order_by('-created_at')[:20]

    return render(request, 'staff/profile.html', {
        'employee': employee,
        'stats':    stats,
        'attendance_page': attendance_page,
        'leave_balances': leave_balances,
        'assigned_tasks': assigned_tasks,
        'activities': activities,
    })


@login_required
def field_visit_page(request):
    if not check_staff_role(request.user):
        return redirect('accounts:login')

    employee = getattr(request.user, 'employee_profile', None)
    return render(request, 'staff/field_visit.html', {'employee': employee})


@login_required
def staff_change_password(request):
    if not check_staff_role(request.user):
        return redirect('accounts:login')

    from django.contrib import messages
    if request.method == 'POST':
        current_password = request.POST.get('current_password', '')
        new_password = request.POST.get('new_password', '')
        confirm_password = request.POST.get('confirm_password', '')
        
        errors = {}
        
        # Verify current password
        if not request.user.check_password(current_password):
            errors['current_password'] = 'Current password is incorrect.'
        
        # Validate new password
        if len(new_password) < 8:
            errors['new_password'] = 'Password must be at least 8 characters.'
        
        # Check passwords match
        if new_password != confirm_password:
            errors['confirm_password'] = 'Passwords do not match.'
        
        if not errors:
            # Set new password
            request.user.set_password(new_password)
            request.user.save()
            
            # Keep user logged in after password change
            from django.contrib.auth import update_session_auth_hash
            update_session_auth_hash(request, request.user)
            
            messages.success(request, 'Password changed successfully.')
            return redirect('staff:profile')
        
        # Return with errors
        return render(request, 'staff/change_password.html', {'errors': errors})
    
    return render(request, 'staff/change_password.html')


def check_manager_role(user):
    from apps.accounts.permissions import user_can_access_my_projects
    return user_can_access_my_projects(user)


@login_required
def my_projects(request):
    if not check_manager_role(request.user):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied("Only managers or admins can access this page.")

    from apps.projects.models import Project, ProjectTask
    from apps.employees.models import EmployeeProfile

    if request.user.is_superuser:
        projects = Project.objects.all().order_by('name')
    else:
        employee = getattr(request.user, 'employee_profile', None)
        if not employee:
            projects = Project.objects.none()
        else:
            projects = Project.objects.filter(project_managers=employee).order_by('name')

    for project in projects:
        assigned_ids = ProjectTask.objects.filter(project=project, responsible_person__isnull=False).values_list('responsible_person_id', flat=True).distinct()
        project.assigned_employees = EmployeeProfile.objects.filter(id__in=assigned_ids).order_by('full_name')

    return render(request, 'staff/projects/my_projects.html', {
        'projects': projects,
    })


@login_required
def my_project_detail(request, project_id):
    if not check_manager_role(request.user):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied("Only managers or admins can access this page.")

    from apps.projects.models import Project, ProjectTask
    from django.contrib.contenttypes.models import ContentType
    from apps.notifications.models import ActivityLog
    project = get_object_or_404(Project, pk=project_id)

    if not request.user.is_superuser:
        employee = getattr(request.user, 'employee_profile', None)
        if not employee or not project.project_managers.filter(id=employee.id).exists():
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied("You are not the manager of this project.")

    tasks = project.tasks.all().select_related('responsible_person').order_by('order')

    # Fetch assigned employees for the team roster card
    from django.db.models import Q
    from apps.employees.models import EmployeeProfile
    assigned_employees = EmployeeProfile.objects.filter(
        Q(is_active=True) & (
            Q(managed_projects=project) |
            Q(site_engineer_projects=project) |
            Q(member_projects=project)
        )
    ).distinct().order_by('full_name')

    for emp in assigned_employees:
        emp.project_tasks = project.tasks.filter(responsible_person=emp).order_by('order')

    task_ids = project.tasks.values_list('id', flat=True)
    ct_task = ContentType.objects.get_for_model(ProjectTask)
    activities = ActivityLog.objects.filter(
        target_content_type=ct_task,
        target_object_id__in=task_ids
    ).select_related('actor', 'actor__employee_profile').order_by('-created_at')[:20]

    return render(request, 'staff/projects/my_project_detail.html', {
        'project': project,
        'tasks': tasks,
        'assigned_employees': assigned_employees,
        'activities': activities,
        'all_count': tasks.count(),
        'not_started_count': tasks.filter(status='Not Started').count(),
        'in_progress_count': tasks.filter(status='In Progress').count(),
        'delayed_count': tasks.filter(status='Delayed').count(),
        'completed_count': tasks.filter(status='Completed').count(),
    })


@login_required
def my_project_add_task(request, project_id):
    if not check_manager_role(request.user):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied("Only managers or admins can access this page.")

    from apps.projects.models import Project, ProjectTask
    from apps.employees.models import EmployeeProfile
    project = get_object_or_404(Project, pk=project_id)

    if not request.user.is_superuser:
        employee = getattr(request.user, 'employee_profile', None)
        if not employee or not project.project_managers.filter(id=employee.id).exists():
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied("You are not the manager of this project.")

    from django.db.models import Q
    eligible_employees = EmployeeProfile.objects.filter(
        Q(is_active=True) & (
            Q(managed_projects=project) |
            Q(site_engineer_projects=project) |
            Q(member_projects=project)
        )
    ).distinct().order_by('full_name')

    from django.contrib import messages

    if request.method == 'POST':
        activity = request.POST.get('activity', '').strip()
        responsible_person_id = request.POST.get('responsible_person', '')
        points_str = request.POST.get('points', '10')
        planned_start_str = request.POST.get('planned_start', '')
        planned_finish_str = request.POST.get('planned_finish', '')
        remarks = request.POST.get('remarks', '').strip()
        assignment_attachments = request.FILES.getlist('assignment_attachments')

        errors = {}
        if not activity:
            errors['activity'] = 'Activity description is required.'

        responsible_person = None
        if responsible_person_id:
            try:
                responsible_person = eligible_employees.get(pk=responsible_person_id)
            except EmployeeProfile.DoesNotExist:
                errors['responsible_person'] = 'Selected employee is not eligible or active.'

        try:
            points = int(points_str)
            if points < 0:
                errors['points'] = 'Points cannot be negative.'
        except ValueError:
            errors['points'] = 'Invalid number format for points.'

        planned_start = None
        planned_finish = None
        if planned_start_str:
            try:
                planned_start = datetime.datetime.strptime(planned_start_str, '%Y-%m-%d').date()
            except ValueError:
                errors['planned_start'] = 'Invalid date format.'

        if planned_finish_str:
            try:
                planned_finish = datetime.datetime.strptime(planned_finish_str, '%Y-%m-%d').date()
            except ValueError:
                errors['planned_finish'] = 'Invalid date format.'

        if planned_start and planned_finish and planned_finish < planned_start:
            errors['planned_finish'] = 'Planned finish date cannot be before planned start date.'

        for attachment in assignment_attachments:
            from django.core.exceptions import ValidationError
            from apps.projects.models import validate_task_attachment
            try:
                validate_task_attachment(attachment)
            except ValidationError as e:
                errors['assignment_attachment'] = f"File {attachment.name} validation failed: {e.message}"

        if not errors:
            from django.db.models import Max
            max_order = project.tasks.aggregate(Max('order'))['order__max'] or 0
            
            task = ProjectTask.objects.create(
                project=project,
                order=max_order + 1,
                activity=activity,
                responsible_person=responsible_person,
                planned_start=planned_start,
                planned_finish=planned_finish,
                points=points,
                remarks=remarks,
                status='Not Started',
            )
            if assignment_attachments:
                from apps.projects.models import TaskAttachment
                for index, attachment in enumerate(assignment_attachments):
                    if index == 0:
                        task.assignment_attachment = attachment
                        task.save(update_fields=['assignment_attachment'])
                    TaskAttachment.objects.create(
                        task=task,
                        file=attachment,
                        attachment_type='assignment'
                    )
            project.recalculate_progress()
            
            # Send notification email if assignee exists
            if task.responsible_person and task.responsible_person.user:
                from apps.notifications.dispatch import log_activity
                subject = f"New Task Assigned: {task.activity}"
                notif_msg = f"You have been assigned to task '{task.activity}' for project '{project.name}'."
                message_text = (
                    f"Hello {task.responsible_person.full_name},\n\n"
                    f"You have been assigned to the following task in project '{project.name}':\n"
                    f"Task: {task.activity}\n"
                    f"Planned: {task.planned_start or '—'} to {task.planned_finish or '—'}\n"
                    f"Status: {task.status}\n\n"
                )
                if task.assignment_attachment:
                    message_text += f"See attached reference file: {task.assignment_attachment.url}\n\n"
                message_text += "Regards,\nFieldTrack System"

                log_activity(
                    actor=request.user,
                    verb='task_assigned',
                    target=task,
                    metadata={
                        'title': subject,
                        'message': notif_msg,
                        'email_subject': subject,
                        'email_message': message_text,
                        'notif_type': 'field_visit'
                    },
                    notify_users=[task.responsible_person.user],
                    email_also=True
                )
            
            messages.success(request, f"Task '{activity}' added successfully.")
            return redirect('staff:my_project_detail', project_id=project.id)
        
        return render(request, 'staff/projects/add_task_form.html', {
            'project': project,
            'employees': eligible_employees,
            'errors': errors,
            'data': request.POST,
        })

    return render(request, 'staff/projects/add_task_form.html', {
        'project': project,
        'employees': eligible_employees,
    })


@login_required
def my_project_edit_task(request, task_id):
    if not check_manager_role(request.user):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied("Only managers or admins can access this page.")

    from apps.projects.models import Project, ProjectTask
    from apps.employees.models import EmployeeProfile
    from django.contrib import messages

    task = get_object_or_404(ProjectTask, pk=task_id)
    project = task.project

    if not request.user.is_superuser:
        employee = getattr(request.user, 'employee_profile', None)
        if not employee or not project.project_managers.filter(id=employee.id).exists():
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied("You are not the manager of this project.")

    from django.db.models import Q
    eligible_employees = EmployeeProfile.objects.filter(
        Q(is_active=True) & (
            Q(managed_projects=project) |
            Q(site_engineer_projects=project) |
            Q(member_projects=project)
        )
    ).distinct().order_by('full_name')

    if request.method == 'POST':
        activity = request.POST.get('activity', '').strip()
        responsible_person_id = request.POST.get('responsible_person', '')
        points_str = request.POST.get('points', '10')
        planned_start_str = request.POST.get('planned_start', '')
        planned_finish_str = request.POST.get('planned_finish', '')
        remarks = request.POST.get('remarks', '').strip()
        status = request.POST.get('status', 'Not Started')
        progress_percent_str = request.POST.get('progress_percent', '0')
        assignment_attachments = request.FILES.getlist('assignment_attachments')

        errors = {}
        if not activity:
            errors['activity'] = 'Activity description is required.'

        responsible_person = None
        if responsible_person_id:
            try:
                responsible_person = eligible_employees.get(pk=responsible_person_id)
            except EmployeeProfile.DoesNotExist:
                errors['responsible_person'] = 'Selected employee is not eligible or active.'

        try:
            points = int(points_str)
            if points < 0:
                errors['points'] = 'Points cannot be negative.'
        except ValueError:
            errors['points'] = 'Invalid number format for points.'

        planned_start = None
        planned_finish = None
        if planned_start_str:
            try:
                planned_start = datetime.datetime.strptime(planned_start_str, '%Y-%m-%d').date()
            except ValueError:
                errors['planned_start'] = 'Invalid date format.'

        if planned_finish_str:
            try:
                planned_finish = datetime.datetime.strptime(planned_finish_str, '%Y-%m-%d').date()
            except ValueError:
                errors['planned_finish'] = 'Invalid date format.'

        if planned_start and planned_finish and planned_finish < planned_start:
            errors['planned_finish'] = 'Planned finish date cannot be before planned start date.'

        try:
            progress_percent = int(progress_percent_str)
            if progress_percent < 0 or progress_percent > 100:
                errors['progress_percent'] = 'Progress must be between 0 and 100.'
        except ValueError:
            errors['progress_percent'] = 'Invalid number format for progress.'

        for attachment in assignment_attachments:
            from django.core.exceptions import ValidationError
            from apps.projects.models import validate_task_attachment
            try:
                validate_task_attachment(attachment)
            except ValidationError as e:
                errors['assignment_attachment'] = f"File {attachment.name} validation failed: {e.message}"

        if not errors:
            old_resp = task.responsible_person
            
            task.activity = activity
            task.responsible_person = responsible_person
            task.points = points
            task.planned_start = planned_start
            task.planned_finish = planned_finish
            task.remarks = remarks
            
            task.progress_percent = progress_percent
            if progress_percent == 100:
                task.status = 'Completed'
                if not task.completed_at:
                    task.completed_at = timezone.now()
            else:
                if status == 'Completed':
                    task.status = 'In Progress'
                else:
                    task.status = status
                    
            if assignment_attachments:
                from apps.projects.models import TaskAttachment
                for index, attachment in enumerate(assignment_attachments):
                    if index == 0 and not task.assignment_attachment:
                        task.assignment_attachment = attachment
                    TaskAttachment.objects.create(
                        task=task,
                        file=attachment,
                        attachment_type='assignment'
                    )
            
            task.save()
            project.recalculate_progress()

            # Send notification email if assignee changed
            if task.responsible_person and task.responsible_person != old_resp and task.responsible_person.user:
                from apps.notifications.dispatch import log_activity
                subject = f"Task Assigned: {task.activity}"
                notif_msg = f"You have been assigned to task '{task.activity}' for project '{project.name}'."
                message_text = (
                    f"Hello {task.responsible_person.full_name},\n\n"
                    f"You have been assigned to the following task in project '{project.name}':\n"
                    f"Task: {task.activity}\n"
                    f"Planned: {task.planned_start or '—'} to {task.planned_finish or '—'}\n"
                    f"Status: {task.status}\n\n"
                )
                if task.assignment_attachment:
                    message_text += f"See attached reference file: {task.assignment_attachment.url}\n\n"
                message_text += "Regards,\nFieldTrack System"

                log_activity(
                    actor=request.user,
                    verb='task_assigned',
                    target=task,
                    metadata={
                        'title': subject,
                        'message': notif_msg,
                        'email_subject': subject,
                        'email_message': message_text,
                        'notif_type': 'field_visit'
                    },
                    notify_users=[task.responsible_person.user],
                    email_also=True
                )

            messages.success(request, f"Task '{activity}' updated successfully.")
            return redirect('staff:my_project_detail', project_id=project.id)

        form_data = {
            'activity': activity,
            'responsible_person': responsible_person_id,
            'points': points,
            'planned_start': planned_start_str,
            'planned_finish': planned_finish_str,
            'remarks': remarks,
            'status': status,
            'progress_percent': progress_percent,
        }
        return render(request, 'staff/projects/edit_task_form.html', {
            'project': project,
            'task': task,
            'employees': eligible_employees,
            'errors': errors,
            'data': form_data,
        })

    # GET request: populate data dictionary with task values
    form_data = {
        'activity': task.activity,
        'responsible_person': task.responsible_person.id if task.responsible_person else '',
        'points': task.points,
        'planned_start': task.planned_start.strftime('%Y-%m-%d') if task.planned_start else '',
        'planned_finish': task.planned_finish.strftime('%Y-%m-%d') if task.planned_finish else '',
        'remarks': task.remarks,
        'status': task.status,
        'progress_percent': task.progress_percent,
    }
    return render(request, 'staff/projects/edit_task_form.html', {
        'project': project,
        'task': task,
        'employees': eligible_employees,
        'data': form_data,
    })


@login_required
def my_project_delete_task(request, task_id):
    if not check_manager_role(request.user):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied("Only managers or admins can access this page.")

    from apps.projects.models import Project, ProjectTask
    from django.contrib import messages

    task = get_object_or_404(ProjectTask, pk=task_id)
    project = task.project

    if not request.user.is_superuser:
        employee = getattr(request.user, 'employee_profile', None)
        if not employee or not project.project_managers.filter(id=employee.id).exists():
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied("You are not the manager of this project.")

    task_activity = task.activity
    task.delete()
    project.recalculate_progress()
    messages.success(request, f"Task '{task_activity}' deleted successfully.")
    return redirect('staff:my_project_detail', project_id=project.id)


@login_required
def my_tasks(request):
    if not check_staff_role(request.user):
        return redirect('accounts:login')

    employee = getattr(request.user, 'employee_profile', None)
    if not employee:
        from django.contrib import messages
        messages.error(request, 'Employee profile not found.')
        return redirect('staff:home')

    from apps.projects.models import ProjectTask

    # Query tasks assigned to this employee
    tasks = ProjectTask.objects.filter(responsible_person=employee).select_related('project', 'project__branch').prefetch_related('attachments').order_by('status', 'planned_finish')

    status_filter = request.GET.get('status', 'pending')

    if status_filter == 'pending':
        tasks = tasks.exclude(status='Completed')
    elif status_filter == 'completed':
        tasks = tasks.filter(status='Completed')

    return render(request, 'staff/my_tasks.html', {
        'employee': employee,
        'assigned_tasks': tasks,
        'status_filter': status_filter,
    })
