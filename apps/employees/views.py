from datetime import datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, DetailView, UpdateView, View
from django.http import HttpResponse, HttpResponseForbidden
from django.contrib import messages
from apps.accounts.mixins import AdminRequiredMixin, RoleRequiredMixin
from apps.notifications.models import log_audit
from .models import EmployeeProfile, EmployeeLocationSync, EmployeeDocument, Employee, EmployeeAuditLog, EmployeeActivityLog, AssetAssignment
from .forms import EmployeeCreateForm, EmployeeEditForm, EmployeeDocumentForm, AssetAssignmentForm, AssetReturnForm, AssetReassignForm
from apps.branches.models import Branch
from apps.attendance.models import Attendance
from django.db.models import Q
from django.utils import timezone
import calendar

def get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')

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


class EmployeeDocumentVerifyView(RoleRequiredMixin, View):
    allowed_roles = ['admin', 'manager']

    def post(self, request, pk):
        doc = get_object_or_404(EmployeeDocument, pk=pk)
        employee_pk = doc.employee_master_id or doc.employee_id
        
        if doc.is_verified:
            doc.is_verified = False
            doc.verified_by = None
            doc.verified_at = None
            action_str = "unverified"
        else:
            doc.is_verified = True
            doc.verified_by = request.user
            doc.verified_at = timezone.now()
            action_str = "verified"
            
        doc.save()
        log_audit(
            actor=request.user,
            action='document_verified' if doc.is_verified else 'document_unverified',
            target=doc,
            summary=f"Marked document {doc.title} ({doc.get_document_type_display()}) as {action_str}"
        )
        messages.success(request, f"Document has been successfully {action_str}.")
        
        if request.headers.get('HX-Request'):
            res = render(request, 'employees/partials/form_success_htmx.html', {'redirect_url': reverse('employees:master_detail', kwargs={'pk': employee_pk})})
            res['HX-Redirect'] = reverse('employees:master_detail', kwargs={'pk': employee_pk})
            return res
        return redirect('employees:master_detail', pk=employee_pk)


class EmployeeDocumentArchiveView(RoleRequiredMixin, View):
    allowed_roles = ['admin', 'manager']

    def post(self, request, pk):
        doc = get_object_or_404(EmployeeDocument, pk=pk)
        employee_pk = doc.employee_master_id or doc.employee_id
        
        doc.is_archived = not doc.is_archived
        doc.save()
        
        action_str = "archived" if doc.is_archived else "restored"
        log_audit(
            actor=request.user,
            action='document_archived' if doc.is_archived else 'document_restored',
            target=doc,
            summary=f"Marked document {doc.title} ({doc.get_document_type_display()}) as {action_str}"
        )
        messages.success(request, f"Document has been successfully {action_str}.")
        
        if request.headers.get('HX-Request'):
            res = render(request, 'employees/partials/form_success_htmx.html', {'redirect_url': reverse('employees:master_detail', kwargs={'pk': employee_pk})})
            res['HX-Redirect'] = reverse('employees:master_detail', kwargs={'pk': employee_pk})
            return res
        return redirect('employees:master_detail', pk=employee_pk)


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
        ).prefetch_related(
            'direct_reports',
            'employment_history__approved_by',
            'documents__uploaded_by',
            'asset_assignments__asset',
            'asset_assignments__reassigned_to__employee'
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['active_tab'] = self.request.GET.get('tab', 'identity')
        context['documents'] = self.object.documents.all()
        context['active_documents'] = self.object.documents.filter(is_active=True, is_archived=False)
        context['archived_documents'] = self.object.documents.filter(is_active=True, is_archived=True)
        context['asset_assignments'] = self.object.asset_assignments.select_related('asset').all()
        context['active_asset_assignments'] = self.object.asset_assignments.filter(returned_date__isnull=True).select_related('asset')
        context['historical_asset_assignments'] = self.object.asset_assignments.filter(returned_date__isnull=False).select_related('asset', 'reassigned_to__employee')
        
        # Payroll gating check
        from apps.accounts.engine import PermissionEngine
        user = self.request.user
        can_view_payroll = user.is_superuser or PermissionEngine.evaluate(user, 'employees.view_payroll').allowed or getattr(user, 'role', '') in ('admin', 'hr', 'hr_manager', 'hr_admin')
        context['can_view_payroll'] = can_view_payroll
        
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

        response = super().form_valid(form)
        employee = self.object
        reason_text = (self.request.POST.get('change_reason') or 'Admin update').strip()

        old_values = {}
        new_values = {}

        # Track status change history
        if old_status != employee.status:
            old_values['status'] = old_status
            new_values['status'] = employee.status
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
            from apps.employees.models import EmployeeActivityLog
            EmployeeActivityLog.objects.create(
                employee=employee,
                actor=self.request.user,
                action_description=f"Changed Employment Status from '{old_status}' to '{employee.status}'",
                field_changed='status'
            )

        # Track other field changes
        fields_to_track = [
            'first_name', 'last_name', 'dob', 'gender', 'national_id',
            'phone', 'personal_email', 'address',
            'emergency_contact_name', 'emergency_contact_phone', 'emergency_contact_relation', 'emergency_contact_address',
            'branch', 'department', 'designation', 'reporting_manager', 'joined_date',
            'employment_type', 'shift', 'weekly_holiday_policy',
            'basic_salary', 'salary_structure', 'bank_name', 'bank_account', 'payment_method',
            'tax_profile', 'pf_enabled', 'overtime_policy', 'user', 'data_scope', 'mfa_required'
        ]

        from apps.employees.models import EmployeeActivityLog

        for field in fields_to_track:
            old_val = getattr(old_instance, field)
            new_val = getattr(employee, field)
            if old_val != new_val:
                # Format FKs or objects nicely
                old_str = str(old_val) if old_val is not None else ""
                new_str = str(new_val) if new_val is not None else ""
                
                if field in ('reporting_manager', 'branch', 'department', 'designation', 'user'):
                    old_str = old_val.get_full_name() if (field == 'reporting_manager' and old_val) else (old_val.name if (field in ('branch', 'department', 'designation') and old_val) else (old_val.email or old_val.phone if (field == 'user' and old_val) else str(old_val or "")))
                    new_str = new_val.get_full_name() if (field == 'reporting_manager' and new_val) else (new_val.name if (field in ('branch', 'department', 'designation') and new_val) else (new_val.email or new_val.phone if (field == 'user' and new_val) else str(new_val or "")))

                old_values[field] = old_str
                new_values[field] = new_str

                EmploymentHistory.objects.create(
                    employee=employee,
                    field_changed=field,
                    old_value=old_str,
                    new_value=new_str,
                    reason=reason_text,
                    approved_by=self.request.user,
                    effective_date=timezone.now().date()
                )
                log_audit(
                    actor=self.request.user,
                    action=f'employee_{field}_changed',
                    target=employee,
                    summary=f"Changed {field} from '{old_str}' to '{new_str}'"
                )
                field_label = field.replace('_', ' ').capitalize()
                EmployeeActivityLog.objects.create(
                    employee=employee,
                    actor=self.request.user,
                    action_description=f"Updated {field_label} from '{old_str}' to '{new_str}'",
                    field_changed=field
                )

        if old_values:
            from apps.employees.models import EmployeeAuditLog
            EmployeeAuditLog.objects.create(
                employee=employee,
                old_value=old_values,
                new_value=new_values,
                changed_by=self.request.user,
                ip_address=get_client_ip(self.request),
                user_agent=self.request.META.get('HTTP_USER_AGENT', '')
            )

        if self.request.headers.get('HX-Request'):
            messages.success(self.request, f"Employee {employee.get_full_name()} updated.")
            response = render(self.request, 'employees/partials/form_success_htmx.html', {
                'message': f"Employee {employee.get_full_name()} updated.",
                'redirect_url': reverse_lazy('employees:employee_detail', kwargs={'pk': employee.pk})
            })
            response['HX-Redirect'] = reverse_lazy('employees:employee_detail', kwargs={'pk': employee.pk})
            return response

        messages.success(self.request, f"Employee {employee.get_full_name()} updated.")
        return response

    def get_success_url(self):
        return reverse_lazy('employees:employee_detail', kwargs={'pk': self.object.pk})


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
        from apps.employees.models import EmployeeActivityLog
        EmployeeActivityLog.objects.create(
            employee=employee,
            actor=request.user,
            action_description=f"Archived employee (soft deleted from status '{old_status}')",
            field_changed='status'
        )

        messages.success(request, f"Employee {employee.get_full_name()} has been archived.")
        if request.headers.get('HX-Request'):
            response = render(request, 'employees/partials/form_success_htmx.html', {
                'redirect_url': reverse_lazy('employees:master_list')
            })
            response['HX-Redirect'] = reverse_lazy('employees:master_list')
            return response

        return redirect('employees:master_list')


# Document Management & Asset Assignment Views (Phase 2 Step 3)
from apps.employees.models import Asset, AssetAssignment, DocumentDownloadLog, DocumentType, SENSITIVE_DOCUMENT_TYPES
from apps.employees.forms import EmployeeDocumentForm, AssetForm, AssetAssignmentForm, AssetReturnForm

class EmployeeDocumentUploadView(RoleRequiredMixin, View):
    allowed_roles = ['admin', 'manager']

    def get(self, request, pk):
        employee = get_object_or_404(Employee, pk=pk)
        form = EmployeeDocumentForm()
        return render(request, 'employees/partials/document_upload_modal.html', {
            'employee': employee,
            'form': form
        })

    def post(self, request, pk):
        employee = get_object_or_404(Employee, pk=pk)
        form = EmployeeDocumentForm(request.POST, request.FILES)
        if form.is_valid():
            doc = form.save(commit=False)
            doc.employee_master = employee
            doc.uploaded_by = request.user
            doc.save()

            log_audit(actor=request.user, action='document_uploaded', target=doc, summary=f"Uploaded {doc.get_document_type_display()} v{doc.version} for {employee.get_full_name()}")
            messages.success(request, f"Document {doc.get_document_type_display()} v{doc.version} uploaded successfully.")

            if request.headers.get('HX-Request'):
                response = render(request, 'employees/partials/form_success_htmx.html', {
                    'redirect_url': reverse('employees:master_detail', kwargs={'pk': employee.pk})
                })
                response['HX-Redirect'] = reverse('employees:master_detail', kwargs={'pk': employee.pk})
                return response
            return redirect('employees:master_detail', pk=employee.pk)

        return render(request, 'employees/partials/document_upload_modal.html', {
            'employee': employee,
            'form': form
        })


# Legacy EmployeeDocumentDownloadView removed — see unified PermissionEngine version below (line ~1307).


class AssetListView(RoleRequiredMixin, ListView):
    allowed_roles = ['admin', 'manager']
    model = Asset
    template_name = 'employees/asset_list.html'
    context_object_name = 'assets'

    def get_queryset(self):
        qs = Asset.objects.prefetch_related('assignments__employee')
        type_filter = self.request.GET.get('type')
        q = self.request.GET.get('q')
        if type_filter:
            qs = qs.filter(asset_type=type_filter)
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(asset_tag__icontains=q) | Q(serial_number__icontains=q))
        return qs


class AssetCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = ['admin', 'manager']
    model = Asset
    form_class = AssetForm
    template_name = 'employees/partials/asset_form_modal.html'

    def form_valid(self, form):
        response = super().form_valid(form)
        log_audit(actor=self.request.user, action='asset_created', target=self.object, summary=f"Created asset {self.object.asset_tag}")
        messages.success(self.request, f"Asset {self.object.asset_tag} created.")
        if self.request.headers.get('HX-Request'):
            res = render(self.request, 'employees/partials/form_success_htmx.html', {'redirect_url': reverse('employees:asset_list')})
            res['HX-Redirect'] = reverse('employees:asset_list')
            return res
        return response

    def get_success_url(self):
        return reverse('employees:asset_list')


class AssetAssignView(RoleRequiredMixin, View):
    allowed_roles = ['admin', 'manager']

    def get(self, request, pk):
        employee = get_object_or_404(Employee, pk=pk)
        form = AssetAssignmentForm()
        return render(request, 'employees/partials/asset_assign_modal.html', {'employee': employee, 'form': form})

    def post(self, request, pk):
        employee = get_object_or_404(Employee, pk=pk)
        form = AssetAssignmentForm(request.POST)
        if form.is_valid():
            assignment = form.save(commit=False)
            assignment.employee = employee
            assignment.assigned_by = request.user
            assignment.save()

            log_audit(actor=request.user, action='asset_assigned', target=assignment, summary=f"Assigned asset {assignment.asset.asset_tag} to {employee.get_full_name()}")
            messages.success(request, f"Asset {assignment.asset.asset_tag} assigned to {employee.get_full_name()}.")

            if request.headers.get('HX-Request'):
                res = render(request, 'employees/partials/form_success_htmx.html', {'redirect_url': reverse('employees:master_detail', kwargs={'pk': employee.pk})})
                res['HX-Redirect'] = reverse('employees:master_detail', kwargs={'pk': employee.pk})
                return res
            return redirect('employees:master_detail', pk=employee.pk)

        return render(request, 'employees/partials/asset_assign_modal.html', {'employee': employee, 'form': form})


class AssetReturnView(RoleRequiredMixin, View):
    allowed_roles = ['admin', 'manager']

    def get(self, request, pk):
        assignment = get_object_or_404(AssetAssignment, pk=pk)
        form = AssetReturnForm(instance=assignment, initial={'returned_date': timezone.localdate()})
        return render(request, 'employees/partials/asset_return_modal.html', {'assignment': assignment, 'form': form})

    def post(self, request, pk):
        assignment = get_object_or_404(AssetAssignment, pk=pk)
        form = AssetReturnForm(request.POST, instance=assignment)
        if form.is_valid():
            updated = form.save()
            log_audit(actor=request.user, action='asset_returned', target=updated, summary=f"Returned asset {updated.asset.asset_tag} from {updated.employee.get_full_name()}")
            messages.success(request, f"Asset {updated.asset.asset_tag} returned.")

            if request.headers.get('HX-Request'):
                res = render(request, 'employees/partials/form_success_htmx.html', {'redirect_url': reverse('employees:master_detail', kwargs={'pk': updated.employee_id})})
                res['HX-Redirect'] = reverse('employees:master_detail', kwargs={'pk': updated.employee_id})
                return res
            return redirect('employees:master_detail', pk=updated.employee_id)

        return render(request, 'employees/partials/asset_return_modal.html', {'assignment': assignment, 'form': form})


class AssetReassignView(RoleRequiredMixin, View):
    allowed_roles = ['admin', 'manager']

    def get(self, request, pk):
        assignment = get_object_or_404(AssetAssignment, pk=pk)
        form = AssetReassignForm(current_assignment=assignment)
        return render(request, 'employees/partials/asset_reassign_modal.html', {'assignment': assignment, 'form': form})

    def post(self, request, pk):
        assignment = get_object_or_404(AssetAssignment, pk=pk)
        form = AssetReassignForm(request.POST, current_assignment=assignment)
        if form.is_valid():
            from django.db import transaction
            
            with transaction.atomic():
                assignment.returned_date = form.cleaned_data['returned_date']
                assignment.condition_at_return = form.cleaned_data['condition_at_return']
                assignment.notes = form.cleaned_data['return_notes']
                assignment.save()
                
                new_assignment = AssetAssignment.objects.create(
                    asset=assignment.asset,
                    employee=form.cleaned_data['new_employee'],
                    assigned_date=form.cleaned_data['assigned_date'],
                    condition_at_assignment=form.cleaned_data['condition_at_assignment'],
                    notes=form.cleaned_data['new_notes'],
                    assigned_by=request.user
                )
                
                assignment.reassigned_to = new_assignment
                assignment.save()

            log_audit(
                actor=request.user,
                action='asset_reassigned',
                target=assignment,
                summary=f"Reassigned asset {assignment.asset.asset_tag} from {assignment.employee.get_full_name()} to {new_assignment.employee.get_full_name()}"
            )
            messages.success(request, f"Asset {assignment.asset.asset_tag} reassigned to {new_assignment.employee.get_full_name()}.")

            if request.headers.get('HX-Request'):
                res = render(request, 'employees/partials/form_success_htmx.html', {'redirect_url': reverse('employees:master_detail', kwargs={'pk': assignment.employee_id})})
                res['HX-Redirect'] = reverse('employees:master_detail', kwargs={'pk': assignment.employee_id})
                return res
            return redirect('employees:master_detail', pk=assignment.employee_id)

        return render(request, 'employees/partials/asset_reassign_modal.html', {'assignment': assignment, 'form': form})


# ==========================================
# LIFECYCLE STATE MACHINE VIEWS
# ==========================================

from apps.employees.models import LifecycleTransitionRequest
from apps.employees.forms import LifecycleActionForm, ReviewTransitionForm
from apps.employees.lifecycle import is_low_risk, is_valid_transition, describe_allowed, TRANSITION_MAP
from django.contrib.auth import get_user_model
from django.utils import timezone as tz


def _notify_admins(request_obj):
    """Send in-app notification to all admin users about a pending lifecycle request."""
    User = get_user_model()
    admins = User.objects.filter(role='admin', is_active=True)
    from apps.notifications.models import Notification
    emp = request_obj.employee
    for admin in admins:
        Notification.objects.create(
            recipient=admin,
            title=f"Lifecycle request: {emp.get_full_name()}",
            message=(
                f"{request_obj.requested_by} requests to move {emp.get_full_name()} "
                f"from '{request_obj.from_status}' to '{request_obj.to_status}'. "
                f"Reason: {request_obj.reason[:200]}"
            ),
            notif_type='lifecycle_request',
        )


def _notify_requester(request_obj):
    """Notify the original requester when their request is reviewed."""
    if not request_obj.requested_by:
        return
    from apps.notifications.models import Notification
    emp = request_obj.employee
    verdict = request_obj.review_status  # 'approved' or 'rejected'
    note = f" Note: {request_obj.review_note}" if request_obj.review_note else ''
    Notification.objects.create(
        recipient=request_obj.requested_by,
        title=f"Lifecycle request {verdict}: {emp.get_full_name()}",
        message=(
            f"Your request to move {emp.get_full_name()} "
            f"from '{request_obj.from_status}' to '{request_obj.to_status}' "
            f"was {verdict} by {request_obj.reviewed_by}.{note}"
        ),
        notif_type='lifecycle_reviewed',
    )


def _apply_transition(employee, req_obj, actor):
    """
    Apply an approved / low-risk transition:
    - Updates employee.status (bypassing clean() state-machine guard by writing directly)
    - Optionally applies new_department / new_designation
    - Creates EmploymentHistory entry
    - Logs audit
    """
    old_status = employee.status
    new_status = req_obj.to_status if req_obj else employee.status

    # Apply org changes if bundled
    changes_desc = []
    if req_obj and req_obj.new_department:
        old_dept = employee.department
        employee.department = req_obj.new_department
        changes_desc.append(f"Dept: {old_dept} → {req_obj.new_department}")
    if req_obj and req_obj.new_designation:
        old_desig = employee.designation
        employee.designation = req_obj.new_designation
        changes_desc.append(f"Designation: {old_desig} → {req_obj.new_designation}")

    # Bypass clean() status-machine check by using update() — we've already
    # validated the transition before calling _apply_transition.
    effective = req_obj.effective_date if req_obj else tz.now().date()
    reason = req_obj.reason if req_obj else 'Direct lifecycle action'

    Employee.objects.filter(pk=employee.pk).update(
        status=new_status,
        department=employee.department,
        designation=employee.designation,
        updated_at=tz.now(),
    )
    employee.refresh_from_db()

    EmploymentHistory.objects.create(
        employee=employee,
        field_changed='status',
        old_value=dict(EmployeeStatus.choices).get(old_status, old_status),
        new_value=dict(EmployeeStatus.choices).get(new_status, new_status),
        reason=reason,
        approved_by=actor,
        effective_date=effective,
    )
    if changes_desc:
        EmploymentHistory.objects.create(
            employee=employee,
            field_changed='organization',
            old_value='',
            new_value='; '.join(changes_desc),
            reason=reason,
            approved_by=actor,
            effective_date=effective,
        )
    log_audit(
        actor=actor,
        action='lifecycle_transition_applied',
        target=employee,
        summary=f"Status: {old_status} → {new_status}"
    )


class LifecycleActionView(AdminRequiredMixin, View):
    """
    POST: Initiate a lifecycle transition from master_detail page.
    LOW_RISK  → apply immediately.
    HIGH_RISK → create LifecycleTransitionRequest (pending), notify admins.
    """
    def get(self, request, pk):
        employee = get_object_or_404(Employee, pk=pk)
        to_status = request.GET.get('to_status', '')
        if not to_status or not is_valid_transition(employee.status, to_status):
            return HttpResponse("Invalid transition.", status=400)
        form = LifecycleActionForm(to_status=to_status, initial={
            'to_status': to_status,
            'effective_date': tz.now().date(),
        })
        return render(request, 'employees/partials/lifecycle_action_modal.html', {
            'employee': employee,
            'to_status': to_status,
            'to_status_display': dict(EmployeeStatus.choices).get(to_status, to_status),
            'form': form,
            'is_high_risk': not is_low_risk(employee.status, to_status),
        })

    def post(self, request, pk):
        employee = get_object_or_404(Employee, pk=pk)
        to_status = request.POST.get('to_status', '')

        if not to_status or not is_valid_transition(employee.status, to_status):
            allowed = describe_allowed(employee.status)
            if request.headers.get('HX-Request'):
                return HttpResponse(
                    f"<p class='text-rose-500 text-sm font-semibold'>Invalid transition from '{employee.status}' to '{to_status}'. Allowed: {allowed}</p>",
                    status=422
                )
            messages.error(request, f"Invalid transition: {allowed}")
            return redirect('employees:master_detail', pk=pk)

        form = LifecycleActionForm(request.POST, to_status=to_status)
        if not form.is_valid():
            return render(request, 'employees/partials/lifecycle_action_modal.html', {
                'employee': employee,
                'to_status': to_status,
                'to_status_display': dict(EmployeeStatus.choices).get(to_status, to_status),
                'form': form,
                'is_high_risk': not is_low_risk(employee.status, to_status),
            })

        cd = form.cleaned_data
        from_status = employee.status

        if is_low_risk(from_status, to_status):
            # Build a fake req_obj-like namespace for _apply_transition
            class _FakeReq:
                pass
            fake = _FakeReq()
            fake.to_status = to_status
            fake.reason = cd['reason']
            fake.effective_date = cd['effective_date']
            fake.new_department = cd.get('new_department')
            fake.new_designation = cd.get('new_designation')
            _apply_transition(employee, fake, request.user)
            log_audit(
                actor=request.user,
                action='lifecycle_transition_applied',
                target=employee,
                summary=f"LOW_RISK: {from_status} → {to_status}"
            )
            messages.success(request, f"Status changed: {from_status} → {to_status}")
        else:
            # HIGH_RISK: queue for admin approval
            req = LifecycleTransitionRequest.objects.create(
                employee=employee,
                from_status=from_status,
                to_status=to_status,
                reason=cd['reason'],
                new_department=cd.get('new_department'),
                new_designation=cd.get('new_designation'),
                requested_by=request.user,
                effective_date=cd['effective_date'],
                review_status=LifecycleTransitionRequest.ReviewStatus.PENDING,
            )
            _notify_admins(req)
            log_audit(
                actor=request.user,
                action='lifecycle_transition_requested',
                target=employee,
                summary=f"HIGH_RISK pending: {from_status} → {to_status}"
            )
            messages.success(request, f"Transition request submitted for admin approval: {from_status} → {to_status}")

        if request.headers.get('HX-Request'):
            res = render(request, 'employees/partials/form_success_htmx.html', {
                'redirect_url': reverse('employees:master_detail', kwargs={'pk': pk})
            })
            res['HX-Redirect'] = reverse('employees:master_detail', kwargs={'pk': pk})
            return res
        return redirect('employees:master_detail', pk=pk)


class LifecyclePendingListView(AdminRequiredMixin, ListView):
    """Admin queue: all lifecycle transition requests (default: pending)."""
    model = LifecycleTransitionRequest
    template_name = 'employees/lifecycle_requests.html'
    context_object_name = 'requests'
    paginate_by = 30

    def get_queryset(self):
        qs = LifecycleTransitionRequest.objects.select_related(
            'employee', 'requested_by', 'reviewed_by', 'new_department', 'new_designation'
        )
        status_filter = self.request.GET.get('status', 'pending')
        if status_filter in ('pending', 'approved', 'rejected'):
            qs = qs.filter(review_status=status_filter)
        return qs.order_by('-requested_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['status_filter'] = self.request.GET.get('status', 'pending')
        context['pending_count'] = LifecycleTransitionRequest.objects.filter(
            review_status=LifecycleTransitionRequest.ReviewStatus.PENDING
        ).count()
        context['review_form'] = ReviewTransitionForm()
        return context

    def render_to_response(self, context, **response_kwargs):
        if self.request.headers.get('HX-Request'):
            return render(self.request, 'employees/partials/lifecycle_requests_table.html', context)
        return super().render_to_response(context, **response_kwargs)


class LifecycleReviewView(AdminRequiredMixin, View):
    """
    POST: Admin approves or rejects a LifecycleTransitionRequest.
    'action' POST param must be 'approve' or 'reject'.
    Returns updated row partial (htmx swap outerHTML).
    """
    def post(self, request, req_pk):
        ltr = get_object_or_404(LifecycleTransitionRequest, pk=req_pk)
        if not ltr.is_pending():
            if request.headers.get('HX-Request'):
                return render(request, 'employees/partials/lifecycle_request_row.html', {'req': ltr})
            return redirect('employees:lifecycle_requests')

        action = request.POST.get('action', '')
        form = ReviewTransitionForm(request.POST)
        # review_note is optional, form is always valid
        review_note = request.POST.get('review_note', '').strip()

        if action == 'approve':
            _apply_transition(ltr.employee, ltr, request.user)
            ltr.review_status = LifecycleTransitionRequest.ReviewStatus.APPROVED
            ltr.reviewed_by = request.user
            ltr.reviewed_at = tz.now()
            ltr.review_note = review_note
            ltr.save(update_fields=['review_status', 'reviewed_by', 'reviewed_at', 'review_note'])
            log_audit(
                actor=request.user,
                action='lifecycle_transition_approved',
                target=ltr.employee,
                summary=f"Approved: {ltr.from_status} → {ltr.to_status}"
            )
            _notify_requester(ltr)
            messages.success(request, f"Approved: {ltr.employee.get_full_name()} {ltr.from_status} → {ltr.to_status}")

        elif action == 'reject':
            ltr.review_status = LifecycleTransitionRequest.ReviewStatus.REJECTED
            ltr.reviewed_by = request.user
            ltr.reviewed_at = tz.now()
            ltr.review_note = review_note
            ltr.save(update_fields=['review_status', 'reviewed_by', 'reviewed_at', 'review_note'])
            log_audit(
                actor=request.user,
                action='lifecycle_transition_rejected',
                target=ltr.employee,
                summary=f"Rejected: {ltr.from_status} → {ltr.to_status}"
            )
            _notify_requester(ltr)
            messages.success(request, f"Rejected: {ltr.employee.get_full_name()} transition to {ltr.to_status}")

        if request.headers.get('HX-Request'):
            return render(request, 'employees/partials/lifecycle_request_row.html', {'req': ltr})
        return redirect('employees:lifecycle_requests')


# ── Employee Multi-Step Wizard View ─────────────────────────────────────────
from django.http import FileResponse, JsonResponse
from apps.accounts.engine import PermissionEngine
from apps.employees.models import Employee, EmployeeDocument, DocumentDownloadLog, Asset, AssetAssignment, EmployeeStatus, EmploymentHistory
from apps.employees.forms import (
    WizardStep1Form, WizardStep2Form, WizardStep3Form, WizardStep4Form,
    WizardStep6Form, EmployeeDocumentForm, AssetAssignmentForm
)

class EmployeeWizardView(AdminRequiredMixin, View):
    """
    Multi-Step Wizard for Employee Creation and Edition (Steps 1 to 8).
    Supports progressive saving, HTMX step navigation, and full lifecycle activation.
    """
    def get_form_class(self, step):
        mapping = {
            1: WizardStep1Form,
            2: WizardStep2Form,
            3: WizardStep3Form,
            4: WizardStep4Form,
            6: WizardStep6Form,
        }
        return mapping.get(step)

    def get_context_data(self, request, pk=None, step=1, form=None):
        step = int(step)
        employee = get_object_or_404(Employee, pk=pk) if pk else None

        ctx = {
            'step': step,
            'step_count': 8,
            'employee': employee,
            'completion_pct': employee.get_completion_percentage() if employee else 0,
            'step_template': f'employees/wizard/step_{step}.html',
        }

        form_cls = self.get_form_class(step)
        if form is None and form_cls:
            if step == 4:
                ctx['form'] = form_cls(employee=employee)
            elif employee:
                ctx['form'] = form_cls(instance=employee)
            else:
                ctx['form'] = form_cls()
        elif form:
            ctx['form'] = form

        if step == 5 and employee:
            ctx['doc_form'] = EmployeeDocumentForm()
            ctx['documents'] = employee.documents.filter(is_active=True).select_related('uploaded_by')
        elif step == 7 and employee:
            ctx['asset_form'] = AssetAssignmentForm()
            ctx['assigned_assets'] = employee.asset_assignments.select_related('asset', 'assigned_by').order_by('-assigned_date')
        elif step == 8 and employee:
            ctx['user_account'] = employee.user
            if employee.user:
                ctx['user_roles'] = UserRoleAssignment.objects.filter(user=employee.user).select_related('role')
            else:
                ctx['user_roles'] = []
            ctx['documents'] = employee.documents.filter(is_active=True)
            ctx['assets'] = employee.asset_assignments.filter(returned_date__isnull=True).select_related('asset')

        return ctx

    def get(self, request, pk=None, step=1):
        step = int(step)
        ctx = self.get_context_data(request, pk, step)
        if request.headers.get('HX-Request'):
            return render(request, f'employees/wizard/step_{step}.html', ctx)
        return render(request, 'employees/employee_wizard.html', ctx)

    def post(self, request, pk=None, step=1):
        step = int(step)
        employee = get_object_or_404(Employee, pk=pk) if pk else None

        if step == 1:
            form = WizardStep1Form(request.POST, request.FILES, instance=employee)
            if form.is_valid():
                emp = form.save(commit=False)
                if not emp.pk:
                    emp.status = EmployeeStatus.DRAFT
                emp.save()
                messages.success(request, f"Step 1 saved. Employee ID {emp.employee_number} created in Draft status.")
                next_step = int(request.POST.get('next_step', 2))
                if request.headers.get('HX-Request'):
                    ctx = self.get_context_data(request, pk=emp.pk, step=next_step)
                    response = render(request, f'employees/wizard/step_{next_step}.html', ctx)
                    response['HX-Push-Url'] = f"/employees/wizard/{emp.pk}/step/{next_step}/"
                    return response
                return redirect('employees:employee_wizard_step', pk=emp.pk, step=next_step)
            else:
                ctx = self.get_context_data(request, pk=pk, step=1, form=form)
                return render(request, f'employees/wizard/step_1.html' if request.headers.get('HX-Request') else 'employees/employee_wizard.html', ctx)

        elif step == 2:
            form = WizardStep2Form(request.POST, instance=employee)
            if form.is_valid():
                form.save()
                messages.success(request, "Step 2 (Organization Info) saved successfully.")
                next_step = int(request.POST.get('next_step', 3))
                if request.headers.get('HX-Request'):
                    ctx = self.get_context_data(request, pk=employee.pk, step=next_step)
                    response = render(request, f'employees/wizard/step_{next_step}.html', ctx)
                    response['HX-Push-Url'] = f"/employees/wizard/{employee.pk}/step/{next_step}/"
                    return response
                return redirect('employees:employee_wizard_step', pk=employee.pk, step=next_step)
            else:
                ctx = self.get_context_data(request, pk=employee.pk, step=2, form=form)
                return render(request, f'employees/wizard/step_2.html' if request.headers.get('HX-Request') else 'employees/employee_wizard.html', ctx)

        elif step == 3:
            form = WizardStep3Form(request.POST, instance=employee)
            if form.is_valid():
                form.save()
                messages.success(request, "Step 3 (Payroll Info) saved successfully.")
                next_step = int(request.POST.get('next_step', 4))
                if request.headers.get('HX-Request'):
                    ctx = self.get_context_data(request, pk=employee.pk, step=next_step)
                    response = render(request, f'employees/wizard/step_{next_step}.html', ctx)
                    response['HX-Push-Url'] = f"/employees/wizard/{employee.pk}/step/{next_step}/"
                    return response
                return redirect('employees:employee_wizard_step', pk=employee.pk, step=next_step)
            else:
                ctx = self.get_context_data(request, pk=employee.pk, step=3, form=form)
                return render(request, f'employees/wizard/step_3.html' if request.headers.get('HX-Request') else 'employees/employee_wizard.html', ctx)

        elif step == 4:
            form = WizardStep4Form(request.POST, employee=employee)
            if form.is_valid():
                form.save()
                messages.success(request, "Step 4 (Security Account & Role Assignment) saved successfully.")
                next_step = int(request.POST.get('next_step', 5))
                if request.headers.get('HX-Request'):
                    ctx = self.get_context_data(request, pk=employee.pk, step=next_step)
                    response = render(request, f'employees/wizard/step_{next_step}.html', ctx)
                    response['HX-Push-Url'] = f"/employees/wizard/{employee.pk}/step/{next_step}/"
                    return response
                return redirect('employees:employee_wizard_step', pk=employee.pk, step=next_step)
            else:
                ctx = self.get_context_data(request, pk=employee.pk, step=4, form=form)
                return render(request, f'employees/wizard/step_4.html' if request.headers.get('HX-Request') else 'employees/employee_wizard.html', ctx)

        elif step == 5:
            # Document Upload
            doc_form = EmployeeDocumentForm(request.POST, request.FILES)
            if doc_form.is_valid():
                doc = doc_form.save(commit=False)
                doc.employee_master = employee
                doc.uploaded_by = request.user
                doc.save()
                messages.success(request, f"Document '{doc.get_document_type_display()}' uploaded successfully.")
            else:
                messages.error(request, "Failed to upload document. Please check the form fields.")
            
            action_type = request.POST.get('action_type', 'upload')
            if action_type == 'next':
                next_step = 6
            else:
                next_step = 5

            ctx = self.get_context_data(request, pk=employee.pk, step=next_step)
            if request.headers.get('HX-Request'):
                response = render(request, f'employees/wizard/step_{next_step}.html', ctx)
                response['HX-Push-Url'] = f"/employees/wizard/{employee.pk}/step/{next_step}/"
                return response
            return redirect('employees:employee_wizard_step', pk=employee.pk, step=next_step)

        elif step == 6:
            form = WizardStep6Form(request.POST, instance=employee)
            if form.is_valid():
                form.save()
                messages.success(request, "Step 6 (Emergency Contact) saved successfully.")
                next_step = int(request.POST.get('next_step', 7))
                if request.headers.get('HX-Request'):
                    ctx = self.get_context_data(request, pk=employee.pk, step=next_step)
                    response = render(request, f'employees/wizard/step_{next_step}.html', ctx)
                    response['HX-Push-Url'] = f"/employees/wizard/{employee.pk}/step/{next_step}/"
                    return response
                return redirect('employees:employee_wizard_step', pk=employee.pk, step=next_step)
            else:
                ctx = self.get_context_data(request, pk=employee.pk, step=6, form=form)
                return render(request, f'employees/wizard/step_6.html' if request.headers.get('HX-Request') else 'employees/employee_wizard.html', ctx)

        elif step == 7:
            # Asset Assignment
            asset_form = AssetAssignmentForm(request.POST)
            if asset_form.is_valid():
                assign = asset_form.save(commit=False)
                assign.employee = employee
                assign.assigned_by = request.user
                assign.save()
                messages.success(request, f"Asset '{assign.asset.name}' assigned successfully.")
            else:
                messages.error(request, "Could not assign asset. Check if asset is already assigned.")

            action_type = request.POST.get('action_type', 'assign')
            next_step = 8 if action_type == 'next' else 7

            ctx = self.get_context_data(request, pk=employee.pk, step=next_step)
            if request.headers.get('HX-Request'):
                response = render(request, f'employees/wizard/step_{next_step}.html', ctx)
                response['HX-Push-Url'] = f"/employees/wizard/{employee.pk}/step/{next_step}/"
                return response
            return redirect('employees:employee_wizard_step', pk=employee.pk, step=next_step)

        elif step == 8:
            # Final Approval step
            action = request.POST.get('action', 'approve')
            if action == 'approve':
                old_status = employee.status
                employee.status = EmployeeStatus.ACTIVE
                employee.save()
                log_audit(
                    actor=request.user,
                    action='employee_wizard_approval',
                    target=employee,
                    summary=f"Approved employee wizard: {employee.get_full_name()} status changed from {old_status} to Active."
                )
                messages.success(request, f"Employee {employee.get_full_name()} successfully activated!")
                return redirect('employees:employee_detail', pk=employee.pk if hasattr(employee, 'legacy_profile') else employee.pk)
            else:
                messages.info(request, f"Employee wizard saved in {employee.status} state.")
                return redirect('employees:employee_list')

        return redirect('employees:employee_list')


from django.contrib.auth.mixins import LoginRequiredMixin

class EmployeeDocumentDownloadView(LoginRequiredMixin, View):
    """
    Unified, PermissionEngine-gated document download view.
    - Sensitive documents: checks ownership first, then falls through PermissionEngine.evaluate().
    - Logs every download via DocumentDownloadLog + audit trail.
    - Returns 404 with message if file is physically missing.
    """
    def get(self, request, pk):
        doc = get_object_or_404(
            EmployeeDocument.objects.select_related('employee_master', 'employee'),
            pk=pk
        )

        if doc.is_sensitive():
            is_owner = (
                (doc.employee_master and doc.employee_master.user == request.user) or
                (doc.employee and doc.employee.user == request.user)
            )
            if not is_owner and not request.user.is_superuser:
                res = PermissionEngine.evaluate(request.user, 'employees.download_sensitive_document')
                if not res.allowed:
                    log_audit(
                        actor=request.user,
                        action='document_access_denied',
                        target=doc,
                        summary=f"Unauthorized download attempt for sensitive document pk={doc.pk}"
                    )
                    return HttpResponseForbidden(
                        "Permission Denied: You do not have permission to download sensitive documents."
                    )

        # Verify file physically exists before streaming
        if not doc.file or not doc.file.storage.exists(doc.file.name):
            messages.error(request, "Document file not found on storage.")
            fallback_pk = doc.employee_master_id or (doc.employee_id if hasattr(doc, 'employee_id') else 1)
            return redirect('employees:master_detail', pk=fallback_pk)

        DocumentDownloadLog.objects.create(
            document=doc,
            downloaded_by=request.user,
            ip_address=get_client_ip(request)
        )
        log_audit(
            actor=request.user,
            action='document_downloaded',
            target=doc,
            summary=f"Downloaded document '{doc.title or doc.get_document_type_display()}' (pk={doc.pk})"
        )
        return FileResponse(
            doc.file.open('rb'),
            as_attachment=True,
            filename=f"{doc.title or doc.get_document_type_display()}_{doc.pk}"
        )


class EmployeeTimelineView(AdminRequiredMixin, DetailView):
    """
    Read-only timeline view combining status transitions and EmploymentHistory logs.
    """
    model = Employee
    template_name = 'employees/employee_timeline.html'
    context_object_name = 'employee'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        employee = self.object
        history_events = list(employee.employment_history.select_related('approved_by').all())
        lifecycle_events = list(employee.lifecycle_requests.select_related('requested_by', 'reviewed_by').all())
        
        combined = []
        for h in history_events:
            combined.append({
                'timestamp': h.created_at,
                'event_type': 'Field Change',
                'title': f"Field '{h.field_changed}' updated",
                'description': f"Old: {h.old_value} → New: {h.new_value} (Reason: {h.reason})",
                'actor': h.approved_by,
            })
        for l in lifecycle_events:
            combined.append({
                'timestamp': l.requested_at,
                'event_type': 'Lifecycle Transition',
                'title': f"Status Request: {l.from_status} → {l.to_status} [{l.review_status}]",
                'description': f"Reason: {l.reason} | Note: {l.review_note}",
                'actor': l.reviewed_by or l.requested_by,
            })

        combined.sort(key=lambda x: x['timestamp'], reverse=True)
        context['timeline_events'] = combined
        return context


class EmployeeSuspendToggleView(AdminRequiredMixin, View):
    def post(self, request, pk):
        employee = get_object_or_404(Employee, pk=pk)
        reason = request.POST.get('reason', '').strip()
        if not reason:
            messages.error(request, "Reason is required to suspend/unsuspend an employee.")
            return redirect('employees:employee_detail', pk=employee.pk)

        is_suspended = not employee.is_suspended
        employee.is_suspended = is_suspended
        employee.save()

        # Log change to EmploymentHistory
        action_str = "Suspended" if is_suspended else "Unsuspended"
        EmploymentHistory.objects.create(
            employee=employee,
            field_changed='is_suspended',
            old_value=str(not is_suspended),
            new_value=str(is_suspended),
            reason=reason,
            approved_by=request.user,
            effective_date=timezone.now().date()
        )
        
        # Log to Activity Log (if model exists)
        try:
            from apps.employees.models import EmployeeActivityLog
            EmployeeActivityLog.objects.create(
                employee=employee,
                actor=request.user,
                action_description=f"{action_str} employee. Reason: {reason}",
                field_changed='is_suspended'
            )
        except ImportError:
            pass

        log_audit(
            actor=request.user,
            action=f"employee_{action_str.lower()}",
            target=employee,
            summary=f"{action_str} employee: {reason}"
        )

        # Audit log (Scope 3)
        try:
            from apps.employees.models import EmployeeAuditLog
            EmployeeAuditLog.objects.create(
                employee=employee,
                old_value={"is_suspended": not is_suspended},
                new_value={"is_suspended": is_suspended},
                changed_by=request.user,
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT', '')
            )
        except ImportError:
            pass

        messages.success(request, f"Employee has been successfully {action_str.lower()}ed.")
        
        if request.headers.get('HX-Request'):
            from django.urls import reverse
            response = render(request, 'employees/partials/form_success_htmx.html', {
                'redirect_url': reverse('employees:employee_detail', kwargs={'pk': employee.pk})
            })
            response['HX-Redirect'] = reverse('employees:employee_detail', kwargs={'pk': employee.pk})
            return response
            
        return redirect('employees:employee_detail', pk=employee.pk)


class EmployeeSuspendModalView(AdminRequiredMixin, View):
    def get(self, request, pk):
        employee = get_object_or_404(Employee, pk=pk)
        return render(request, 'employees/partials/suspend_modal.html', {'employee': employee})


class EmployeeAuditLogView(AdminRequiredMixin, ListView):
    model = EmployeeAuditLog
    template_name = 'employees/partials/audit_log_table.html'
    context_object_name = 'audit_logs'
    paginate_by = 10

    def get_queryset(self):
        self.employee = get_object_or_404(Employee, pk=self.kwargs['pk'])
        from apps.employees.models import EmployeeAuditLog
        return EmployeeAuditLog.objects.filter(employee=self.employee).select_related('changed_by')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['employee'] = self.employee
        return context

