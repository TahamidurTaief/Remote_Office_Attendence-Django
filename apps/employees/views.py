from datetime import datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, DetailView, UpdateView, View
from django.contrib import messages
from apps.accounts.mixins import AdminRequiredMixin
from .models import EmployeeProfile, EmployeeLocationSync
from .forms import EmployeeCreateForm, EmployeeEditForm
from apps.branches.models import Branch
from apps.attendance.models import Attendance
from django.db.models import Q
from django.utils import timezone
import calendar

class EmployeeListView(AdminRequiredMixin, ListView):
    model = EmployeeProfile
    template_name = 'employees/employee_list.html'
    context_object_name = 'employees'
    paginate_by = 10
    
    def get_queryset(self):
        queryset = super().get_queryset().select_related('branch', 'user')
        search_query = self.request.GET.get('search', '')
        department_filter = self.request.GET.get('department', '')
        branch_filter = self.request.GET.get('branch', '')
        status_filter = self.request.GET.get('status', '')

        if search_query:
            queryset = queryset.filter(
                Q(full_name__icontains=search_query) | 
                Q(employee_id__icontains=search_query)
            )
            
        if department_filter:
            queryset = queryset.filter(department=department_filter)
            
        if branch_filter:
            queryset = queryset.filter(branch_id=branch_filter)
            
        if status_filter == 'active':
            queryset = queryset.filter(is_active=True)
        elif status_filter == 'inactive':
            queryset = queryset.filter(is_active=False)
            
        return queryset.order_by('full_name', 'employee_id')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search'] = self.request.GET.get('search', '')
        context['department'] = self.request.GET.get('department', '')
        context['branch_id'] = self.request.GET.get('branch', '')
        context['status'] = self.request.GET.get('status', '')
        
        context['departments'] = EmployeeProfile.objects.values_list('department', flat=True).distinct().exclude(department='')
        from apps.branches.utils import get_cached_branches
        context['branches'] = get_cached_branches()
        return context

class EmployeeCreateView(AdminRequiredMixin, CreateView):
    model = EmployeeProfile
    form_class = EmployeeCreateForm
    template_name = 'employees/employee_form.html'
    success_url = reverse_lazy('employees:employee_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from apps.leave.models import LeaveType
        leave_types = LeaveType.objects.all().order_by('name')
        context['leave_types_with_overrides'] = [
            {
                'id': lt.id,
                'name': lt.name,
                'category': lt.category,
                'default_days': lt.default_days_per_year,
                'override_days': self.request.POST.get(f'leave_override_{lt.id}', '') if self.request.method == 'POST' else ''
            }
            for lt in leave_types
        ]
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        from apps.leave.models import LeaveType, LeaveBalance
        from .models import EmployeeLeaveRule
        
        leave_types = LeaveType.objects.all()
        for lt in leave_types:
            post_key = f'leave_override_{lt.id}'
            override_val = self.request.POST.get(post_key)
            if override_val is not None and override_val.strip() != '':
                try:
                    days = int(override_val)
                    EmployeeLeaveRule.objects.update_or_create(
                        employee=self.object,
                        leave_type=lt,
                        defaults={'days_per_year': days}
                    )
                    year = timezone.now().year
                    balance, bal_created = LeaveBalance.objects.get_or_create(
                        employee=self.object,
                        leave_type=lt,
                        year=year,
                        defaults={'total_days': days, 'used_days': 0}
                    )
                    if not bal_created:
                        balance.total_days = days
                        balance.save()
                except ValueError:
                    pass
            else:
                EmployeeLeaveRule.objects.filter(employee=self.object, leave_type=lt).delete()
                year = timezone.now().year
                LeaveBalance.objects.filter(
                    employee=self.object,
                    leave_type=lt,
                    year=year
                ).update(total_days=lt.default_days_per_year)

        messages.success(self.request, 'Employee profile and user account created successfully.')
        return response

class EmployeeEditView(AdminRequiredMixin, UpdateView):
    model = EmployeeProfile
    form_class = EmployeeEditForm
    template_name = 'employees/employee_form.html'
    success_url = reverse_lazy('employees:employee_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from apps.leave.models import LeaveType
        from .models import EmployeeLeaveRule
        leave_types = LeaveType.objects.all().order_by('name')
        
        if self.request.method == 'POST':
            context['leave_types_with_overrides'] = [
                {
                    'id': lt.id,
                    'name': lt.name,
                    'category': lt.category,
                    'default_days': lt.default_days_per_year,
                    'override_days': self.request.POST.get(f'leave_override_{lt.id}', '')
                }
                for lt in leave_types
            ]
        else:
            overrides = {
                rule.leave_type_id: rule.days_per_year 
                for rule in EmployeeLeaveRule.objects.filter(employee=self.object)
            }
            context['leave_types_with_overrides'] = [
                {
                    'id': lt.id,
                    'name': lt.name,
                    'category': lt.category,
                    'default_days': lt.default_days_per_year,
                    'override_days': overrides.get(lt.id, '')
                }
                for lt in leave_types
            ]
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        from apps.leave.models import LeaveType, LeaveBalance
        from .models import EmployeeLeaveRule
        
        leave_types = LeaveType.objects.all()
        for lt in leave_types:
            post_key = f'leave_override_{lt.id}'
            override_val = self.request.POST.get(post_key)
            if override_val is not None and override_val.strip() != '':
                try:
                    days = int(override_val)
                    EmployeeLeaveRule.objects.update_or_create(
                        employee=self.object,
                        leave_type=lt,
                        defaults={'days_per_year': days}
                    )
                    year = timezone.now().year
                    balance, bal_created = LeaveBalance.objects.get_or_create(
                        employee=self.object,
                        leave_type=lt,
                        year=year,
                        defaults={'total_days': days, 'used_days': 0}
                    )
                    if not bal_created:
                        balance.total_days = days
                        balance.save()
                except ValueError:
                    pass
            else:
                EmployeeLeaveRule.objects.filter(employee=self.object, leave_type=lt).delete()
                year = timezone.now().year
                LeaveBalance.objects.filter(
                    employee=self.object,
                    leave_type=lt,
                    year=year
                ).update(total_days=lt.default_days_per_year)

        messages.success(self.request, 'Employee profile updated successfully.')
        return response

class EmployeeDetailView(AdminRequiredMixin, DetailView):
    model = EmployeeProfile
    template_name = 'employees/employee_detail.html'
    context_object_name = 'employee'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        employee = self.object
        
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
            'present': attendances_this_month.filter(status__in=['on_time', 'late']).count(),
            'absent': attendances_this_month.filter(status='absent').count(),
            'late': attendances_this_month.filter(status='late').count(),
            'field_visits': attendances_this_month.filter(type='field').count()
        }
        context['stats'] = stats
        
        # Previous history filtering
        history_date_from = self.request.GET.get('history_date_from', '')
        history_date_to = self.request.GET.get('history_date_to', '')
        history_status = self.request.GET.get('history_status', '')
        history_type = self.request.GET.get('history_type', '')
        
        history_qs = Attendance.objects.filter(employee=employee, is_expired=False)
        if history_date_from:
            history_qs = history_qs.filter(date__gte=history_date_from)
        if history_date_to:
            history_qs = history_qs.filter(date__lte=history_date_to)
        if history_status:
            if history_status == 'present':
                history_qs = history_qs.filter(status__in=['on_time', 'late'])
            else:
                history_qs = history_qs.filter(status=history_status)
        if history_type:
            history_qs = history_qs.filter(type=history_type)
            
        context['recent_attendance'] = history_qs.order_by('-date', '-check_in_time')
        context['history_date_from'] = history_date_from
        context['history_date_to'] = history_date_to
        context['history_status'] = history_status
        context['history_type'] = history_type
        
        context['todays_field_visits'] = Attendance.objects.filter(
            employee=employee, 
            date=timezone.localdate(), 
            attendance_type='field_visit',
            is_expired=False
        ).order_by('-check_in_time')
        
        # Periodic background location syncs (Auto Sync Track)
        sync_date_str = self.request.GET.get('sync_date')
        if sync_date_str:
            try:
                sync_date = datetime.strptime(sync_date_str, '%Y-%m-%d').date()
            except ValueError:
                sync_date = timezone.localdate()
        else:
            sync_date = timezone.localdate()
            
        tz = timezone.get_current_timezone()
        sync_start_dt = timezone.make_aware(datetime.combine(sync_date, datetime.min.time()), tz)
        sync_end_dt = timezone.make_aware(datetime.combine(sync_date, datetime.max.time()), tz)
        
        sync_time_from = self.request.GET.get('sync_time_from')
        sync_time_to = self.request.GET.get('sync_time_to')
        
        if sync_time_from:
            try:
                stf = datetime.strptime(sync_time_from, '%H:%M').time()
                sync_start_dt = timezone.make_aware(datetime.combine(sync_date, stf), tz)
            except ValueError:
                pass
        if sync_time_to:
            try:
                stt = datetime.strptime(sync_time_to, '%H:%M').time()
                sync_end_dt = timezone.make_aware(datetime.combine(sync_date, stt), tz)
            except ValueError:
                pass
                
        sync_qs = EmployeeLocationSync.objects.filter(
            employee=employee,
            timestamp__range=(sync_start_dt, sync_end_dt)
        ).order_by('timestamp')
        
        context['sync_date'] = sync_date.strftime('%Y-%m-%d')
        context['sync_time_from'] = sync_time_from or ''
        context['sync_time_to'] = sync_time_to or ''
        context['location_syncs'] = sync_qs
        
        return context

class ToggleStatusView(AdminRequiredMixin, View):
    def post(self, request, pk):
        employee = get_object_or_404(EmployeeProfile, pk=pk)
        employee.is_active = not employee.is_active
        employee.save()
        return render(request, 'employees/partials/status_badge.html', {'employee': employee})
