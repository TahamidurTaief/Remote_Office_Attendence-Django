from datetime import datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, DetailView, UpdateView, View
from django.contrib import messages
from apps.accounts.mixins import AdminRequiredMixin
from .models import EmployeeProfile, EmployeeLocationSync, EmployeeDocument
from .forms import EmployeeCreateForm, EmployeeEditForm, EmployeeDocumentForm
from apps.branches.models import Branch
from apps.attendance.models import Attendance
from django.db.models import Q
from django.utils import timezone
import calendar

class EmployeeListView(AdminRequiredMixin, ListView):
    model = EmployeeProfile
    template_name = 'employees/employee_list.html'
    context_object_name = 'employees'
    paginate_by = 20
    
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
        context['today'] = timezone.localdate()
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

class EmployeeDocumentCreateView(AdminRequiredMixin, CreateView):
    model = EmployeeDocument
    form_class = EmployeeDocumentForm
    template_name = 'employees/document_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.employee = get_object_or_404(EmployeeProfile, pk=kwargs['employee_pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['employee'] = self.employee
        return context

    def form_valid(self, form):
        form.instance.employee = self.employee
        messages.success(self.request, 'Document uploaded successfully.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('employees:employee_detail', kwargs={'pk': self.employee.pk})

class EmployeeDocumentEditView(AdminRequiredMixin, UpdateView):
    model = EmployeeDocument
    form_class = EmployeeDocumentForm
    template_name = 'employees/document_form.html'

    def form_valid(self, form):
        messages.success(self.request, 'Document updated successfully.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('employees:employee_detail', kwargs={'pk': self.object.employee.pk})

class EmployeeDocumentDeleteView(AdminRequiredMixin, View):
    def post(self, request, pk):
        doc = get_object_or_404(EmployeeDocument, pk=pk)
        employee_pk = doc.employee.pk
        doc.delete()
        messages.success(request, 'Document deleted successfully.')
        return redirect('employees:employee_detail', pk=employee_pk)


# ==========================================
# PHASE 2: EMPLOYEE MASTER (SSOT) VIEWS
# ==========================================

from apps.employees.models import Employee, Department, Designation, EmployeeStatus, EmploymentHistory
from apps.employees.forms import EmployeeMasterForm, DepartmentForm, DesignationForm
from apps.notifications.models import log_audit


class EmployeeMasterListView(AdminRequiredMixin, ListView):
    model = Employee
    template_name = 'employees/master_list.html'
    context_object_name = 'employees'
    paginate_by = 20

    def get_queryset(self):
        queryset = Employee.objects.select_related(
            'branch', 'department', 'designation', 'reporting_manager', 'user'
        ).prefetch_related('direct_reports', 'employment_history')

        search = self.request.GET.get('search', '').strip()
        status_filter = self.request.GET.get('status', '').strip()
        dept_filter = self.request.GET.get('department', '').strip()
        branch_filter = self.request.GET.get('branch', '').strip()
        desig_filter = self.request.GET.get('designation', '').strip()

        if search:
            queryset = queryset.filter(
                Q(first_name__icontains=search) |
                Q(last_name__icontains=search) |
                Q(employee_number__icontains=search) |
                Q(personal_email__icontains=search) |
                Q(phone__icontains=search)
            )

        if status_filter:
            queryset = queryset.filter(status=status_filter)
        if dept_filter:
            queryset = queryset.filter(department_id=dept_filter)
        if branch_filter:
            queryset = queryset.filter(branch_id=branch_filter)
        if desig_filter:
            queryset = queryset.filter(designation_id=desig_filter)

        return queryset.order_by('-created_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search'] = self.request.GET.get('search', '')
        context['status_filter'] = self.request.GET.get('status', '')
        context['dept_filter'] = self.request.GET.get('department', '')
        context['branch_filter'] = self.request.GET.get('branch', '')
        context['desig_filter'] = self.request.GET.get('designation', '')

        context['departments'] = Department.objects.filter(is_active=True)
        context['designations'] = Designation.objects.filter(is_active=True)
        from apps.branches.utils import get_cached_branches
        context['branches'] = get_cached_branches()
        context['statuses'] = EmployeeStatus.choices
        return context

    def render_to_response(self, context, **response_kwargs):
        if self.request.headers.get('HX-Request') and not self.request.headers.get('HX-Target') == 'modal-container':
            return render(self.request, 'employees/partials/master_table.html', context)
        return super().render_to_response(context, **response_kwargs)


class EmployeeMasterDetailView(AdminRequiredMixin, DetailView):
    model = Employee
    template_name = 'employees/master_detail.html'
    context_object_name = 'employee'

    def get_queryset(self):
        return Employee.objects.select_related(
            'branch', 'department', 'designation', 'reporting_manager', 'user'
        ).prefetch_related('direct_reports', 'employment_history__approved_by')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['active_tab'] = self.request.GET.get('tab', 'identity')
        return context


class EmployeeMasterCreateView(AdminRequiredMixin, CreateView):
    model = Employee
    form_class = EmployeeMasterForm
    template_name = 'employees/master_form_modal.html'
    success_url = reverse_lazy('employees:master_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        employee = self.object

        # Audit log creation
        log_audit(
            actor=self.request.user,
            action='employee_created',
            target=employee,
            summary=f"Created Employee Master {employee.employee_number} ({employee.get_full_name()})"
        )

        # Initial EmploymentHistory entry
        EmploymentHistory.objects.create(
            employee=employee,
            field_changed='status',
            old_value='',
            new_value=employee.get_status_display(),
            reason='Initial Master record creation',
            approved_by=self.request.user,
            effective_date=employee.joined_date or timezone.now().date()
        )

        if self.request.headers.get('HX-Request'):
            messages.success(self.request, f"Employee {employee.get_full_name()} created successfully.")
            response = render(self.request, 'employees/partials/form_success_htmx.html', {
                'message': f"Employee {employee.get_full_name()} created.",
                'redirect_url': reverse_lazy('employees:master_list')
            })
            response['HX-Redirect'] = reverse_lazy('employees:master_list')
            return response

        messages.success(self.request, f"Employee {employee.get_full_name()} created successfully.")
        return response


class EmployeeMasterEditView(AdminRequiredMixin, UpdateView):
    model = Employee
    form_class = EmployeeMasterForm
    template_name = 'employees/master_form_modal.html'

    def get_queryset(self):
        return Employee.objects.select_related('branch', 'department', 'designation', 'reporting_manager', 'user')

    def form_valid(self, form):
        old_instance = Employee.objects.get(pk=self.object.pk)
        old_status = old_instance.status
        old_dept = old_instance.department
        old_desig = old_instance.designation
        old_branch = old_instance.branch
        old_mgr = old_instance.reporting_manager

        response = super().form_valid(form)
        employee = self.object
        reason_text = self.request.POST.get('change_reason', 'Admin update')

        # Track status change history
        if old_status != employee.status:
            EmploymentHistory.objects.create(
                employee=employee,
                field_changed='status',
                old_value=dict(EmployeeStatus.choices).get(old_status, old_status),
                new_value=employee.get_status_display(),
                reason=reason_text,
                approved_by=self.request.user,
                effective_date=timezone.now().date()
            )
            log_audit(
                actor=self.request.user,
                action='employee_status_changed',
                target=employee,
                summary=f"Changed status from {old_status} to {employee.status}"
            )

        # Track org change history
        org_changed = False
        if old_dept != employee.department or old_desig != employee.designation or old_branch != employee.branch or old_mgr != employee.reporting_manager:
            org_changed = True
            changes_desc = []
            if old_dept != employee.department:
                changes_desc.append(f"Dept: {old_dept} -> {employee.department}")
            if old_desig != employee.designation:
                changes_desc.append(f"Designation: {old_desig} -> {employee.designation}")
            if old_branch != employee.branch:
                changes_desc.append(f"Branch: {old_branch} -> {employee.branch}")
            if old_mgr != employee.reporting_manager:
                changes_desc.append(f"Manager: {old_mgr} -> {employee.reporting_manager}")

            EmploymentHistory.objects.create(
                employee=employee,
                field_changed='organization',
                old_value=f"Dept: {old_dept}, Desig: {old_desig}, Branch: {old_branch}, Mgr: {old_mgr}",
                new_value=f"Dept: {employee.department}, Desig: {employee.designation}, Branch: {employee.branch}, Mgr: {employee.reporting_manager}",
                reason=reason_text,
                approved_by=self.request.user,
                effective_date=timezone.now().date()
            )
            log_audit(
                actor=self.request.user,
                action='employee_org_changed',
                target=employee,
                summary=f"Updated org details: {'; '.join(changes_desc)}"
            )

        if self.request.headers.get('HX-Request'):
            messages.success(self.request, f"Employee {employee.get_full_name()} updated.")
            response = render(self.request, 'employees/partials/form_success_htmx.html', {
                'message': f"Employee {employee.get_full_name()} updated.",
                'redirect_url': reverse_lazy('employees:master_detail', kwargs={'pk': employee.pk})
            })
            response['HX-Redirect'] = reverse_lazy('employees:master_detail', kwargs={'pk': employee.pk})
            return response

        messages.success(self.request, f"Employee {employee.get_full_name()} updated.")
        return response

    def get_success_url(self):
        return reverse_lazy('employees:master_detail', kwargs={'pk': self.object.pk})


class EmployeeMasterArchiveView(AdminRequiredMixin, View):
    def post(self, request, pk):
        employee = get_object_or_404(Employee, pk=pk)
        old_status = employee.status
        employee.delete()  # soft delete -> sets status to archived
        
        EmploymentHistory.objects.create(
            employee=employee,
            field_changed='status',
            old_value=old_status,
            new_value=EmployeeStatus.ARCHIVED,
            reason='Archived via Admin Action',
            approved_by=request.user,
            effective_date=timezone.now().date()
        )
        log_audit(
            actor=request.user,
            action='employee_status_changed',
            target=employee,
            summary=f"Archived employee {employee.employee_number}"
        )

        messages.success(request, f"Employee {employee.get_full_name()} has been archived.")
        if request.headers.get('HX-Request'):
            response = render(request, 'employees/partials/form_success_htmx.html', {
                'redirect_url': reverse_lazy('employees:master_list')
            })
            response['HX-Redirect'] = reverse_lazy('employees:master_list')
            return response

        return redirect('employees:master_list')

