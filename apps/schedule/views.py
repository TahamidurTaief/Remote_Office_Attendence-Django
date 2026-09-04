from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse_lazy, reverse
from django.views.generic import CreateView, UpdateView, DeleteView, View
from django.utils import timezone
from django.db.models import Q
from collections import defaultdict
import calendar as pycal
from datetime import date, datetime, timedelta

from apps.accounts.mixins import RoleRequiredMixin
from .models import ScheduleEvent
from .forms import ScheduleEventForm

# Aggregation imports
from apps.projects.models import ProjectTask, DailyProgressLog
from apps.leave.models import LeaveRequest
from apps.branches.models import Holiday, Branch
from apps.notifications.models import Notification
from apps.notifications.dispatch import send_email_notification

class CalendarMonthView(RoleRequiredMixin, View):
    allowed_roles = ['admin', 'system_owner', 'manager', 'staff', 'employee']

    def get(self, request, *args, **kwargs):
        today = timezone.localdate()
        
        # Get query parameters
        year_str = request.GET.get('year')
        month_str = request.GET.get('month')
        
        try:
            year = int(year_str) if year_str else today.year
            month = int(month_str) if month_str else today.month
            
            # Bound month between 1 and 12
            if month < 1 or month > 12:
                month = today.month
                year = today.year
        except (ValueError, TypeError):
            year = today.year
            month = today.month

        # Generate month grid using python calendar
        cal = pycal.Calendar(firstweekday=6) # 6 = Sunday
        try:
            weeks = cal.monthdatescalendar(year, month)
        except pycal.IllegalMonthError:
            year = today.year
            month = today.month
            weeks = cal.monthdatescalendar(year, month)

        start_date = weeks[0][0]
        end_date = weeks[-1][-1]

        # Get employee profile and branch for scoping
        profile = None
        user_branch = None
        if request.user.is_authenticated:
            master_emp = getattr(request.user, 'employee_master', None)
            if master_emp:
                profile = getattr(master_emp, 'legacy_profile', None)
                user_branch = getattr(master_emp, 'branch', None)
            if not profile:
                profile = getattr(request.user, 'employee_profile', None)
            if not user_branch and profile:
                user_branch = getattr(profile, 'branch', None)

        from apps.accounts.engine import PermissionEngine
        res = PermissionEngine.evaluate(request.user, 'schedule.manage')
        is_admin_or_manager = request.user.is_superuser or res.allowed or getattr(request.user, 'role', '') in ('admin', 'system_owner', 'manager')
        is_admin = request.user.is_superuser or getattr(request.user, 'role', '') in ('admin', 'system_owner')

        # Role-based scoping:
        # Admin / System Owner: global access across all active branches.
        # Managers: manage schedule events and tasks, but MUST only access holidays and schedules of their assigned branch.
        # Staff / Employees: strictly scoped to their own assignments, tasks, leaves, and branch holidays.
        is_staff_or_employee = not is_admin_or_manager

        # 1. Holidays (Government Holiday where branch=None; Office Holiday where branch matches user's branch)
        # Both managers and employees must only see their assigned branch office holidays.
        holidays_qs = Holiday.objects.filter(date__range=(start_date, end_date))
        if not is_admin:
            if user_branch:
                holidays_qs = holidays_qs.filter(Q(branch__isnull=True) | Q(branch=user_branch))
            else:
                holidays_qs = holidays_qs.filter(branch__isnull=True)
        holidays = holidays_qs.select_related('branch')

        # 2. Manual Schedule Events
        events_qs = ScheduleEvent.objects.filter(date__range=(start_date, end_date))
        if is_staff_or_employee:
            if profile:
                events_qs = events_qs.filter(assigned_to=profile)
            else:
                events_qs = events_qs.none()
        events = events_qs.prefetch_related('assigned_to', 'assigned_to__user', 'project')

        # 3. Project Tasks
        tasks_qs = ProjectTask.objects.filter(
            Q(planned_start__range=(start_date, end_date)) |
            Q(planned_finish__range=(start_date, end_date))
        )
        if is_staff_or_employee:
            if profile:
                tasks_qs = tasks_qs.filter(responsible_person=profile)
            else:
                tasks_qs = tasks_qs.none()
        tasks = tasks_qs.select_related('project', 'responsible_person')

        # 4. Approved Leaves
        leaves_qs = LeaveRequest.objects.filter(
            status='approved',
            start_date__lte=end_date,
            end_date__gte=start_date
        )
        if is_staff_or_employee:
            if profile:
                leaves_qs = leaves_qs.filter(employee=profile)
            else:
                leaves_qs = leaves_qs.none()
        leaves = leaves_qs.select_related('employee', 'employee__user', 'leave_type')

        # 5. Daily Progress Logs
        logs_qs = DailyProgressLog.objects.filter(date__range=(start_date, end_date))
        if is_staff_or_employee:
            logs_qs = logs_qs.filter(logged_by=request.user)
        logs = logs_qs.select_related('project')

        # Group all items by date
        events_by_date = defaultdict(list)

        # Add holidays
        for hol in holidays:
            is_gov = hol.branch is None
            source_type = 'gov_holiday' if is_gov else 'office_holiday'
            title = f"{'Govt: ' if is_gov else 'Office: '}{hol.name}"
            color_classes = (
                'bg-rose-50 dark:bg-rose-950/30 text-rose-700 dark:text-rose-300 border-rose-200/50 dark:border-rose-800/40 hover:bg-rose-100 dark:hover:bg-rose-900/40'
                if is_gov else
                'bg-amber-50 dark:bg-amber-950/30 text-amber-700 dark:text-amber-300 border-amber-200/50 dark:border-amber-800/40 hover:bg-amber-100 dark:hover:bg-amber-900/40'
            )
            dot_color_class = 'bg-rose-500' if is_gov else 'bg-amber-500'
            category_label = 'Government Holiday' if is_gov else f"Office Holiday ({hol.branch.name if hol.branch else 'All Branches'})"
            events_by_date[hol.date].append({
                'id': f"holiday_{hol.pk}",
                'title': title,
                'raw_title': hol.name,
                'description': f"{category_label} scheduled on {hol.date.strftime('%B %d, %Y')}.",
                'source_type': source_type,
                'category_label': category_label,
                'edit_url': '',
                'time_str': 'All Day',
                'date_str': hol.date.strftime('%Y-%m-%d'),
                'project_name': hol.branch.name if hol.branch else 'Public / Nationwide',
                'assigned_employees': 'All employees' if is_gov else f"Branch {hol.branch.name if hol.branch else ''}",
                'color_classes': color_classes,
                'dot_color_class': dot_color_class,
                'is_all_day': True,
                'time_obj': datetime.min.time(),
            })

        # Add manual events
        for event in events:
            time_str = event.start_time.strftime('%I:%M %p') if event.start_time else ""
            assigned_names = [emp.full_name for emp in event.assigned_to.all()]
            project_name = event.project.name if event.project else "None"
            events_by_date[event.date].append({
                'id': f"event_{event.pk}",
                'title': event.title,
                'raw_title': event.title,
                'description': event.description or "No description provided.",
                'source_type': 'manual_event',
                'category_label': f"Event: {event.event_type}",
                'edit_url': reverse('schedule:edit', args=[event.pk]),
                'time_str': time_str or "All Day",
                'date_str': event.date.strftime('%Y-%m-%d'),
                'assigned_employees': ", ".join(assigned_names) if assigned_names else "None",
                'project_name': project_name,
                'color_classes': event.color_classes,
                'dot_color_class': event.dot_color_class,
                'is_all_day': event.start_time is None,
                'time_obj': event.start_time or datetime.min.time(),
            })

        # Add project tasks
        for task in tasks:
            proj_name = task.project.name if task.project else "Unassigned / General Task"
            proj_detail_url = reverse('projects:project_detail', args=[task.project.pk]) if task.project else ""
            resp_person = task.responsible_person.full_name if task.responsible_person else "Unassigned"
            start_date_str = task.planned_start.strftime('%d/%m/%Y') if task.planned_start else "—"
            finish_date_str = task.planned_finish.strftime('%d/%m/%Y') if task.planned_finish else "—"
            
            if task.planned_start and start_date <= task.planned_start <= end_date:
                title = f"Start: {proj_name} - {task.activity} ({task.status})"
                events_by_date[task.planned_start].append({
                    'id': f"task_start_{task.pk}",
                    'title': title,
                    'raw_title': task.activity,
                    'source_type': 'task_deadline',
                    'category_label': f"Task Start ({task.status})",
                    'edit_url': '',
                    'time_str': 'All Day',
                    'project_name': proj_name,
                    'project_url': proj_detail_url,
                    'responsible_person': resp_person,
                    'status': task.status,
                    'planned_dates': f"Planned: {start_date_str} to {finish_date_str}",
                    'color_classes': 'bg-indigo-50 dark:bg-indigo-950/30 text-indigo-700 dark:text-indigo-300 border-indigo-200/50 dark:border-indigo-800/40 hover:bg-indigo-100 dark:hover:bg-indigo-900/40',
                    'dot_color_class': 'bg-indigo-500',
                    'is_all_day': True,
                    'time_obj': datetime.min.time(),
                })
            if task.planned_finish and start_date <= task.planned_finish <= end_date:
                if task.planned_finish != task.planned_start:
                    title = f"Finish: {proj_name} - {task.activity} ({task.status})"
                    events_by_date[task.planned_finish].append({
                        'id': f"task_finish_{task.pk}",
                        'title': title,
                        'raw_title': task.activity,
                        'source_type': 'task_deadline',
                        'category_label': f"Task Finish ({task.status})",
                        'edit_url': '',
                        'time_str': 'All Day',
                        'project_name': proj_name,
                        'project_url': proj_detail_url,
                        'responsible_person': resp_person,
                        'status': task.status,
                        'planned_dates': f"Planned: {start_date_str} to {finish_date_str}",
                        'color_classes': 'bg-indigo-50 dark:bg-indigo-950/30 text-indigo-700 dark:text-indigo-300 border-indigo-200/50 dark:border-indigo-800/40 hover:bg-indigo-100 dark:hover:bg-indigo-900/40',
                        'dot_color_class': 'bg-indigo-500',
                        'is_all_day': True,
                        'time_obj': datetime.min.time(),
                    })

        # Add leaves (can span multiple days)
        for leave in leaves:
            curr_day = max(start_date, leave.start_date)
            limit_day = min(end_date, leave.end_date)
            while curr_day <= limit_day:
                title = f"Out: {leave.employee.full_name} ({leave.leave_type.name})"
                events_by_date[curr_day].append({
                    'id': f"leave_{leave.pk}_{curr_day.strftime('%Y%m%d')}",
                    'title': title,
                    'raw_title': f"{leave.employee.full_name} - {leave.leave_type.name}",
                    'source_type': 'leave',
                    'category_label': f"Leave ({leave.status.capitalize()})",
                    'edit_url': '',
                    'time_str': 'All Day',
                    'employee_name': leave.employee.full_name,
                    'leave_type': leave.leave_type.name,
                    'date_range': f"{leave.start_date.strftime('%d/%m/%Y')} to {leave.end_date.strftime('%d/%m/%Y')}",
                    'status': leave.status.capitalize(),
                    'color_classes': 'bg-purple-50 dark:bg-purple-950/30 text-purple-700 dark:text-purple-300 border-purple-200/50 dark:border-purple-800/40 hover:bg-purple-100 dark:hover:bg-purple-900/40',
                    'dot_color_class': 'bg-purple-500',
                    'is_all_day': True,
                    'time_obj': datetime.min.time(),
                })
                curr_day += timedelta(days=1)

        # Add daily progress logs
        for log in logs:
            title = f"Log: {log.project.name} ({log.supervisor_name})"
            events_by_date[log.date].append({
                'id': f"log_{log.pk}",
                'title': title,
                'raw_title': f"Log: {log.project.name}",
                'source_type': 'progress_log',
                'category_label': "Progress Log",
                'edit_url': '',
                'time_str': 'All Day',
                'project_name': log.project.name,
                'supervisor_name': log.supervisor_name,
                'planned_work': log.planned_work,
                'completed_work': log.completed_work,
                'manpower_count': log.manpower_count or 0,
                'color_classes': 'bg-teal-50 dark:bg-teal-950/30 text-teal-700 dark:text-teal-300 border-teal-200/50 dark:border-teal-800/40 hover:bg-teal-100 dark:hover:bg-teal-900/40',
                'dot_color_class': 'bg-teal-500',
                'is_all_day': True,
                'time_obj': datetime.min.time(),
            })

        # Sort each day's events: all-day / holidays first, then by time, then by title
        for day in events_by_date:
            events_by_date[day] = sorted(
                events_by_date[day],
                key=lambda x: (
                    0 if x['source_type'] in ('gov_holiday', 'office_holiday') else (1 if x['is_all_day'] else 2),
                    x['time_obj'],
                    x['title']
                )
            )

        # Build day-by-day weeks structures
        weeks_data = []
        for week in weeks:
            week_data = []
            for day in week:
                day_events = events_by_date[day]
                day_holidays = [e for e in day_events if e['source_type'] in ('gov_holiday', 'office_holiday')]
                has_holiday = len(day_holidays) > 0
                has_gov_holiday = any(e['source_type'] == 'gov_holiday' for e in day_holidays)
                has_office_holiday = any(e['source_type'] == 'office_holiday' for e in day_holidays)

                # Day cell styling for Google Calendar-style full day prominence:
                day_tint_class = ""
                day_badge_class = ""
                badge_label = ""
                if has_gov_holiday:
                    day_tint_class = "bg-rose-50/70 dark:bg-rose-950/25 border-rose-200/60 dark:border-rose-900/40"
                    day_badge_class = "bg-rose-600 text-white font-bold"
                    badge_label = "Govt Holiday"
                elif has_office_holiday:
                    day_tint_class = "bg-amber-50/70 dark:bg-amber-950/25 border-amber-200/60 dark:border-amber-900/40"
                    day_badge_class = "bg-amber-600 text-white font-bold"
                    badge_label = "Office Holiday"

                week_data.append({
                    'date': day,
                    'date_str': day.strftime('%Y-%m-%d'),
                    'date_formatted': day.strftime('%B %d, %Y'),
                    'day_num': day.day,
                    'is_current_month': day.month == month,
                    'is_today': day == today,
                    'all_events': day_events,
                    'holidays': day_holidays,
                    'has_holiday': has_holiday,
                    'has_gov_holiday': has_gov_holiday,
                    'has_office_holiday': has_office_holiday,
                    'day_tint_class': day_tint_class,
                    'day_badge_class': day_badge_class,
                    'badge_label': badge_label,
                    'events_count': len(day_events),
                })
            weeks_data.append(week_data)

        # Compute next/prev months
        if month == 1:
            prev_month = 12
            prev_year = year - 1
        else:
            prev_month = month - 1
            prev_year = year

        if month == 12:
            next_month = 1
            next_year = year + 1
        else:
            next_month = month + 1
            next_year = year

        month_name = pycal.month_name[month]

        is_admin = request.user.is_superuser or getattr(request.user, 'role', '') in ('admin', 'system_owner')

        context = {
            'weeks_data': weeks_data,
            'current_year': year,
            'current_month': month,
            'month_name': month_name,
            'prev_year': prev_year,
            'prev_month': prev_month,
            'next_year': next_year,
            'next_month': next_month,
            'today': today,
            'is_admin': is_admin,
            'is_admin_or_manager': is_admin_or_manager,
            'user_branch': user_branch,
        }

        if request.headers.get('HX-Request') and (
            request.GET.get('partial') == 'true' or
            request.headers.get('HX-Target') == 'calendar-view-container' or
            not request.headers.get('HX-Boosted')
        ):
            return render(request, 'schedule/partials/calendar_content.html', context)

        return render(request, 'schedule/calendar_month.html', context)


class ShiftScheduleView(RoleRequiredMixin, View):
    allowed_roles = ['admin', 'system_owner', 'manager', 'staff', 'employee']

    def get(self, request, *args, **kwargs):
        from apps.branches.models import OfficeSchedule, Branch
        from apps.attendance.models import AttendancePolicy

        is_admin_or_manager = (
            request.user.is_superuser or
            getattr(request.user, 'role', '') in ('admin', 'system_owner', 'manager')
        )
        is_admin = request.user.is_superuser or getattr(request.user, 'role', '') in ('admin', 'system_owner')

        user_branch = None
        if request.user.is_authenticated:
            master_emp = getattr(request.user, 'employee_master', None)
            if master_emp:
                user_branch = getattr(master_emp, 'branch', None)
            if not user_branch:
                profile = getattr(request.user, 'employee_profile', None)
                if profile:
                    user_branch = getattr(profile, 'branch', None)

        branches_qs = Branch.objects.filter(is_active=True).order_by('name')

        # Branch resolution:
        selected_branch = None
        selected_branch_id = request.GET.get('branch_id')

        if is_admin:
            if selected_branch_id:
                try:
                    selected_branch = branches_qs.filter(id=int(selected_branch_id)).first()
                except (ValueError, TypeError):
                    selected_branch = None
            if not selected_branch:
                selected_branch = user_branch or branches_qs.first()
            selectable_branches = branches_qs
        else:
            # Manager, Staff & Employee: strict isolation to their own branch
            selected_branch = user_branch
            selectable_branches = [user_branch] if user_branch else []

        schedules_list = []
        if selected_branch:
            schedule = OfficeSchedule.objects.filter(branch=selected_branch).first()
            if not schedule:
                # Ensure default schedule exists
                schedule, _ = OfficeSchedule.objects.get_or_create(
                    branch=selected_branch,
                    defaults={
                        'working_days': ['saturday', 'sunday', 'monday', 'tuesday', 'wednesday', 'thursday']
                    }
                )
            policy = AttendancePolicy.objects.filter(branch=selected_branch).first()
            manage_url = reverse('admin_panel:schedule_settings') if is_admin else ""
            schedules_list.append({
                'branch': selected_branch,
                'schedule': schedule,
                'policy': policy,
                'manage_url': manage_url
            })

        manage_schedule_url = reverse('admin_panel:schedule_settings') if is_admin else ""

        context = {
            'branches': selectable_branches,
            'selected_branch': selected_branch,
            'schedules_list': schedules_list,
            'is_admin': is_admin,
            'is_admin_or_manager': is_admin_or_manager,
            'manage_schedule_url': manage_schedule_url,
            'user_branch': user_branch,
        }

        if request.headers.get('HX-Request') and (
            request.GET.get('partial') == 'true' or
            request.headers.get('HX-Target') == 'shift-schedule-view-container' or
            not request.headers.get('HX-Boosted')
        ):
            return render(request, 'schedule/partials/shift_schedule_content.html', context)

        return render(request, 'schedule/shift_schedule.html', context)


class ScheduleEventCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = ['admin', 'manager']
    model = ScheduleEvent
    form_class = ScheduleEventForm
    template_name = 'schedule/event_form.html'

    def get_initial(self):
        initial = super().get_initial()
        date_str = self.request.GET.get('date')
        if date_str:
            try:
                initial['date'] = datetime.strptime(date_str, '%Y-%m-%d').date()
            except ValueError:
                pass
        return initial

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        
        # Notify assigned employees
        event = self.object
        for employee in event.assigned_to.all():
            if employee.user:
                # DB Notification
                Notification.objects.create(
                    recipient=employee.user,
                    employee=employee,
                    title=f"New Event: {event.title}",
                    message=f"You have been assigned to event '{event.title}' scheduled on {event.date.strftime('%d/%m/%Y')}.",
                    notif_type='field_visit'
                )
                # Email Notification
                subject = f"Assigned to Event: {event.title}"
                message = (
                    f"Hello {employee.full_name},\n\n"
                    f"You have been assigned to the following event:\n"
                    f"Title: {event.title}\n"
                    f"Date: {event.date.strftime('%d/%m/%Y')}\n"
                    f"Description: {event.description or 'No description'}\n\n"
                    f"Regards,\nFieldTrack System"
                )
                send_email_notification(employee.user, subject, message)
                
        return response

    def get_success_url(self):
        return f"{reverse('schedule:month_view')}?year={self.object.date.year}&month={self.object.date.month}"


class ScheduleEventUpdateView(RoleRequiredMixin, UpdateView):
    allowed_roles = ['admin', 'manager']
    model = ScheduleEvent
    form_class = ScheduleEventForm
    template_name = 'schedule/event_form.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        return kwargs

    def form_valid(self, form):
        from django.db import transaction
        from django.core.exceptions import ValidationError
        
        # Concurrency/retry implementation S6:
        original_version = self.get_object().version
        form_version = self.request.POST.get('version')
        
        if form_version and int(form_version) != original_version:
            form.add_error(None, "The event was modified by another user concurrently. Please reload and try again.")
            return self.form_invalid(form)

        try:
            with transaction.atomic():
                # Perform version verification and update
                response = super().form_valid(form)
                return response
        except ValidationError as e:
            form.add_error(None, e)
            return self.form_invalid(form)

    def get_success_url(self):
        return f"{reverse('schedule:month_view')}?year={self.object.date.year}&month={self.object.date.month}"


class ScheduleEventDeleteView(RoleRequiredMixin, DeleteView):
    allowed_roles = ['admin', 'manager']
    model = ScheduleEvent

    def delete(self, request, *args, **kwargs):
        # Optimistic concurrency check for delete
        self.object = self.get_object()
        form_version = request.POST.get('version')
        if form_version and int(form_version) != self.object.version:
            from django.contrib import messages
            messages.error(request, "The event was modified by another user concurrently. Delete cancelled.")
            return redirect(self.get_success_url())
        return super().delete(request, *args, **kwargs)

    def get_success_url(self):
        return f"{reverse('schedule:month_view')}?year={self.object.date.year}&month={self.object.date.month}"

