from datetime import datetime, time
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, DetailView, UpdateView, View
from django.http import HttpResponse, HttpResponseForbidden
from django.contrib import messages
from django.core.exceptions import ValidationError
from apps.accounts.mixins import AdminRequiredMixin, RoleRequiredMixin
from apps.notifications.models import log_audit
from django.utils.decorators import method_decorator
from apps.accounts.decorators import require_reauth
from apps.audit.services import TrashService
from .models import EmployeeProfile, EmployeeLocationSync, EmployeeDocument, Employee, EmployeeAuditLog, EmployeeActivityLog, AssetAssignment
from .forms import EmployeeCreateForm, EmployeeEditForm, EmployeeDocumentForm, AssetAssignmentForm, AssetReturnForm, AssetReassignForm
from apps.branches.models import Branch
from apps.attendance.models import Attendance
from django.db.models import Q
from django.utils import timezone
import calendar

from apps.accounts.rbac_models import UserRoleAssignment, Role

def get_client_ip(request):
    if not request or not hasattr(request, 'META'):
        return ''
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        parts = [ip.strip() for ip in x_forwarded_for.split(',') if ip.strip()]
        if parts:
            return parts[0]
    x_real_ip = request.META.get('HTTP_X_REAL_IP')
    if x_real_ip and x_real_ip.strip():
        return x_real_ip.strip()
    return request.META.get('REMOTE_ADDR') or ''

class EmployeeListView(AdminRequiredMixin, ListView):
    model = EmployeeProfile
    template_name = 'employees/employee_list.html'
    context_object_name = 'employees'
    paginate_by = 20
    
    def get_queryset(self):
        queryset = super().get_queryset().exclude(master_employee__is_trashed=True).select_related(
            'branch', 'user', 'master_employee', 'master_employee__department', 'master_employee__designation'
        )
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

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['actor'] = self.request.user
        return kwargs

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

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['actor'] = self.request.user
        return kwargs

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


@method_decorator(require_reauth, name='dispatch')
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
        queryset = Employee.objects.filter(is_trashed=False).select_related(
            'branch', 'department', 'designation', 'reporting_manager', 'user', 'legacy_profile'
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
        else:
            queryset = queryset.exclude(status=EmployeeStatus.ARCHIVED)
            
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
        context['active_documents'] = self.object.documents.filter(
            is_active=True, is_archived=False
        ).filter(
            Q(expiry_date__isnull=True) | Q(expiry_date__gte=timezone.localdate())
        )
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
    template_name = 'employees/master_edit_page.html'

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
        TrashService.soft_delete(employee, actor=request.user, reason='Archived via Admin Action', request=request)
        
        EmploymentHistory.objects.create(
            employee=employee,
            field_changed='status',
            old_value=old_status,
            new_value=EmployeeStatus.ARCHIVED,
            reason='Archived via Admin Action',
            approved_by=request.user,
            effective_date=timezone.now().date()
        )

        messages.success(request, f"Employee {employee.get_full_name()} has been archived.")
        if request.headers.get('HX-Request'):
            response = render(request, 'employees/partials/form_success_htmx.html', {
                'redirect_url': reverse_lazy('employees:master_list')
            })
            response['HX-Redirect'] = reverse_lazy('employees:master_list')
            return response

        return redirect('employees:master_list')


class EmployeeMasterDeleteView(View):
    """
    Delete an Employee record. Soft-deletes to Trash by default.
    Super-admin can permanently delete if already trashed.
    Protected by strict admin/RBAC delete permission.
    """

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if not self._has_delete_permission(request.user):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied("You do not have permission to delete or trash employees.")
        return super().dispatch(request, *args, **kwargs)

    @staticmethod
    def _has_delete_permission(user):
        if user.is_superuser:
            return True
        user_role_codes = [assignment.role.code for assignment in user.role_assignments.select_related('role').filter(role__is_active=True)]
        if not user_role_codes and hasattr(user, 'role'):
            user_role_codes = [user.role]
        if any(r in ['admin', 'system_owner'] for r in user_role_codes):
            return True
        if user.has_perm('employees.delete_employee'):
            return True
        return False

    def get(self, request, pk):
        """Return the confirmation modal (HTMX partial). Never mutate state."""
        employee = get_object_or_404(Employee, pk=pk)
        if employee.is_trashed:
            title = "Permanently Delete Employee?"
            message = f"Are you sure you want to permanently delete {employee.get_full_name()} ({employee.employee_number})? This action is irreversible."
            action_label = "Delete Permanently"
            reason_required = False
        else:
            title = "Move employee to Trash?"
            message = "This employee will be removed from active operations but historical records will be preserved."
            action_label = "Move to Trash"
            reason_required = True

        return render(request, 'employees/partials/confirmation_modal.html', {
            'title': title,
            'endpoint': reverse_lazy('employees:master_delete', kwargs={'pk': pk}),
            'message': message,
            'action': 'delete',
            'action_label': action_label,
            'reason_required': reason_required,
        })

    def post(self, request, pk):
        employee = get_object_or_404(Employee, pk=pk)
        name = employee.get_full_name()
        emp_number = employee.employee_number

        reason = request.POST.get('reason', '').strip()
        if not employee.is_trashed and not reason:
            if request.headers.get('HX-Request'):
                return render(request, 'employees/partials/confirmation_modal.html', {
                    'title': "Move employee to Trash?",
                    'endpoint': reverse_lazy('employees:master_delete', kwargs={'pk': pk}),
                    'message': "This employee will be removed from active operations but historical records will be preserved.",
                    'action': 'delete',
                    'action_label': "Move to Trash",
                    'reason_required': True,
                    'reason_error': "Reason is required to move employee to trash.",
                })
            messages.error(request, "Reason is required to move employee to trash.")
            return redirect('employees:master_list')

        try:
            if employee.is_trashed:
                if reason:
                    # Idempotent duplicate soft-delete request
                    success_msg = f"Employee {name} ({emp_number}) is already in Trash."
                    if request.headers.get('HX-Request'):
                        import json
                        response = HttpResponse(
                            f'<div id="modal-container"></div><tr id="employee-row-{employee.pk}" hx-swap-oob="delete"></tr>'
                        )
                        response['HX-Trigger'] = json.dumps({
                            'close-modal': {'id': 'employee-confirm-modal'},
                            'show-toast': {'message': success_msg, 'variant': 'info'},
                        })
                        return response
                    messages.info(request, success_msg)
                    return redirect('employees:master_list')

                if not request.user.is_superuser:
                    from django.core.exceptions import PermissionDenied
                    raise PermissionDenied("Only super-admin can perform permanent delete.")
                entry = TrashService.get_active_entry(employee)
                if not entry:
                    messages.error(request, "No active trash entry exists for this employee.")
                else:
                    try:
                        TrashService.permanent_delete(entry, actor=request.user, request=request)
                        success_msg = f"Employee {name} ({emp_number}) has been permanently deleted."
                        if request.headers.get('HX-Request'):
                            import json
                            response = HttpResponse(
                                f'<div id="modal-container"></div><tr id="employee-row-{employee.pk}" hx-swap-oob="delete"></tr>'
                            )
                            response['HX-Trigger'] = json.dumps({
                                'close-modal': {'id': 'employee-confirm-modal'},
                                'show-toast': {'message': success_msg, 'variant': 'success'},
                            })
                            return response
                        messages.success(request, success_msg)
                        return redirect('employees:master_list')
                    except Exception as exc:
                        import logging
                        logger = logging.getLogger(__name__)
                        logger.error(f"Permanent delete failed for employee {pk}: {str(exc)}", exc_info=True)
                        err_msg = "Unable to permanently delete this employee due to active historical or business dependencies."
                        if request.headers.get('HX-Request'):
                            import json
                            response = HttpResponse(
                                f'<div id="modal-container"></div>'
                            )
                            response['HX-Trigger'] = json.dumps({
                                'close-modal': {'id': 'employee-confirm-modal'},
                                'show-toast': {'message': err_msg, 'variant': 'danger'},
                            })
                            return response
                        messages.error(request, err_msg)
                        return redirect('employees:master_list')
            else:
                entry, created = TrashService.soft_delete(
                    employee,
                    actor=request.user,
                    reason=reason,
                    request=request
                )
                success_msg = f"Employee {name} ({emp_number}) has been moved to Trash."
                if request.headers.get('HX-Request'):
                    import json
                    response = HttpResponse(
                        f'<div id="modal-container"></div><tr id="employee-row-{employee.pk}" hx-swap-oob="delete"></tr>'
                    )
                    response['HX-Trigger'] = json.dumps({
                        'close-modal': {'id': 'employee-confirm-modal'},
                        'show-toast': {'message': success_msg, 'variant': 'success'},
                    })
                    return response
                messages.success(request, success_msg)
                return redirect('employees:master_list')

        except ValidationError as e:
            err = str(e.messages[0]) if hasattr(e, 'messages') else str(e)
            if request.headers.get('HX-Request'):
                return render(request, 'employees/partials/confirmation_modal.html', {
                    'title': "Move employee to Trash?",
                    'endpoint': reverse_lazy('employees:master_delete', kwargs={'pk': pk}),
                    'message': "This employee will be removed from active operations but historical records will be preserved.",
                    'action': 'delete',
                    'action_label': "Move to Trash",
                    'reason_required': True,
                    'reason_error': err,
                })
            messages.error(request, err)
            return redirect('employees:master_list')

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
            from django.core.exceptions import ValidationError
            try:
                assignment.save()
            except ValidationError as e:
                form.add_error(None, e)
                return render(request, 'employees/partials/asset_assign_modal.html', {'employee': employee, 'form': form})

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
    from apps.employees.models import EmployeeSuspension, EmployeeStatus
    old_status = employee.status
    new_status = req_obj.to_status if req_obj else employee.status

    # Warning / block rule on archiving
    if new_status == EmployeeStatus.ARCHIVED:
        profile = getattr(employee, 'legacy_profile', None)
        if profile:
            if profile.managed_projects.exclude(status='Completed').exists() or \
               profile.site_engineer_projects.exclude(status='Completed').exists() or \
               profile.member_projects.exclude(status='Completed').exists() or \
               profile.assigned_tasks.exclude(status='Completed').exists():
                raise ValidationError("Cannot archive employee assigned to active projects or tasks.")

    # Apply org changes if bundled
    changes_desc = []
    if req_obj and getattr(req_obj, 'new_department', None):
        old_dept = employee.department
        employee.department = req_obj.new_department
        changes_desc.append(f"Dept: {old_dept} → {req_obj.new_department}")
    if req_obj and getattr(req_obj, 'new_designation', None):
        old_desig = employee.designation
        employee.designation = req_obj.new_designation
        changes_desc.append(f"Designation: {old_desig} → {req_obj.new_designation}")

    # Bypass clean() status-machine check by using update() — we've already
    # validated the transition before calling _apply_transition.
    effective = req_obj.effective_date if req_obj else tz.now().date()
    reason = req_obj.reason if req_obj else ''
    if not reason and req_obj:
        reason = 'Direct lifecycle action'

    # Enforce mandatory reason for: inactive, suspended, resigned, terminated, archived
    if new_status in ('inactive', 'suspended', 'resigned', 'terminated', 'archived') and (not reason or reason == 'Direct lifecycle action'):
        raise ValidationError(f"A transition reason is mandatory for '{new_status}' status.")

    # Handle suspension records
    if new_status == EmployeeStatus.SUSPENDED:
        # Create EmployeeSuspension record
        start_date = getattr(req_obj, 'suspension_start_date', None) or tz.now().date()
        end_date = getattr(req_obj, 'suspension_end_date', None)
        auto_react = getattr(req_obj, 'auto_reactivate', False)
        
        # Deactivate existing active suspensions just in case
        EmployeeSuspension.objects.filter(employee=employee, is_active=True).update(is_active=False)
        
        EmployeeSuspension.objects.create(
            employee=employee,
            suspension_start_date=start_date,
            suspension_end_date=end_date,
            suspension_reason=reason,
            auto_reactivate=auto_react,
            is_active=True,
            changed_by=actor,
            previous_status=old_status,
        )
        employee.is_suspended = True
    elif old_status == EmployeeStatus.SUSPENDED:
        # Reactivating: clear active suspension state but preserve history
        EmployeeSuspension.objects.filter(employee=employee, is_active=True).update(is_active=False)
        employee.is_suspended = False

    employee.status = new_status
    employee._bypass_lifecycle_validation = True
    employee.save()


    EmploymentHistory.objects.create(
        employee=employee,
        field_changed='status',
        old_value=dict(EmployeeStatus.choices).get(old_status, old_status),
        new_value=dict(EmployeeStatus.choices).get(new_status, new_status),
        reason=reason or 'Direct lifecycle action',
        approved_by=actor,
        effective_date=effective,
    )
    if changes_desc:
        EmploymentHistory.objects.create(
            employee=employee,
            field_changed='organization',
            old_value='',
            new_value='; '.join(changes_desc),
            reason=reason or 'Direct lifecycle action',
            approved_by=actor,
            effective_date=effective,
        )

    from apps.audit.services import AuditService
    AuditService.log_event(
        actor=actor,
        action=new_status.lower(),
        instance=employee,
        before={'status': old_status, 'is_suspended': getattr(employee, '_old_is_suspended', employee.is_suspended)},
        after={'status': new_status, 'is_suspended': employee.is_suspended},
        changed_fields={'status': {'before': old_status, 'after': new_status}},
        reason=reason,
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
            fake.suspension_start_date = cd.get('suspension_start_date')
            fake.suspension_end_date = cd.get('suspension_end_date')
            fake.auto_reactivate = cd.get('auto_reactivate', False)
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


@method_decorator(require_reauth, name='dispatch')
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

    def get_context_data(self, request, uuid=None, step=1, form=None):
        step = int(step)
        employee = get_object_or_404(Employee, uuid=uuid) if uuid else None

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
                ctx['form'] = form_cls(employee=employee, actor=request.user)
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

    def get(self, request, uuid=None, step=1):
        step = int(step)
        ctx = self.get_context_data(request, uuid, step)
        if request.headers.get('HX-Request'):
            return render(request, 'employees/wizard/wizard_content.html', ctx)
        return render(request, 'employees/employee_wizard.html', ctx)

    def post(self, request, uuid=None, step=1):
        step = int(step)
        employee = get_object_or_404(Employee, uuid=uuid) if uuid else None

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
                    ctx = self.get_context_data(request, uuid=emp.uuid, step=next_step)
                    response = render(request, 'employees/wizard/wizard_content.html', ctx)
                    response['HX-Push-Url'] = f"/employees/wizard/{emp.uuid}/step/{next_step}/"
                    return response
                return redirect('employees:employee_wizard_step', uuid=emp.uuid, step=next_step)
            else:
                ctx = self.get_context_data(request, uuid=uuid, step=1, form=form)
                return render(request, 'employees/wizard/wizard_content.html' if request.headers.get('HX-Request') else 'employees/employee_wizard.html', ctx)

        elif step == 2:
            form = WizardStep2Form(request.POST, instance=employee)
            if form.is_valid():
                form.save()
                messages.success(request, "Step 2 (Organization Info) saved successfully.")
                next_step = int(request.POST.get('next_step', 3))
                if request.headers.get('HX-Request'):
                    ctx = self.get_context_data(request, uuid=employee.uuid, step=next_step)
                    response = render(request, 'employees/wizard/wizard_content.html', ctx)
                    response['HX-Push-Url'] = f"/employees/wizard/{employee.uuid}/step/{next_step}/"
                    return response
                return redirect('employees:employee_wizard_step', uuid=employee.uuid, step=next_step)
            else:
                ctx = self.get_context_data(request, uuid=employee.uuid, step=2, form=form)
                return render(request, 'employees/wizard/wizard_content.html' if request.headers.get('HX-Request') else 'employees/employee_wizard.html', ctx)

        elif step == 3:
            form = WizardStep3Form(request.POST, instance=employee)
            if form.is_valid():
                form.save()
                messages.success(request, "Step 3 (Payroll Info) saved successfully.")
                next_step = int(request.POST.get('next_step', 4))
                if request.headers.get('HX-Request'):
                    ctx = self.get_context_data(request, uuid=employee.uuid, step=next_step)
                    response = render(request, 'employees/wizard/wizard_content.html', ctx)
                    response['HX-Push-Url'] = f"/employees/wizard/{employee.uuid}/step/{next_step}/"
                    return response
                return redirect('employees:employee_wizard_step', uuid=employee.uuid, step=next_step)
            else:
                ctx = self.get_context_data(request, uuid=employee.uuid, step=3, form=form)
                return render(request, 'employees/wizard/wizard_content.html' if request.headers.get('HX-Request') else 'employees/employee_wizard.html', ctx)

        elif step == 4:
            form = WizardStep4Form(request.POST, employee=employee, actor=request.user)
            if form.is_valid():
                form.save()
                messages.success(request, "Step 4 (Security Account & Role Assignment) saved successfully.")
                next_step = int(request.POST.get('next_step', 5))
                if request.headers.get('HX-Request'):
                    ctx = self.get_context_data(request, uuid=employee.uuid, step=next_step)
                    response = render(request, 'employees/wizard/wizard_content.html', ctx)
                    response['HX-Push-Url'] = f"/employees/wizard/{employee.uuid}/step/{next_step}/"
                    return response
                return redirect('employees:employee_wizard_step', uuid=employee.uuid, step=next_step)
            else:
                ctx = self.get_context_data(request, uuid=employee.uuid, step=4, form=form)
                return render(request, 'employees/wizard/wizard_content.html' if request.headers.get('HX-Request') else 'employees/employee_wizard.html', ctx)

        elif step == 5:
            # Document Upload
            action_type = request.POST.get('action_type', 'upload')
            if action_type == 'upload':
                doc_form = EmployeeDocumentForm(request.POST, request.FILES)
                if doc_form.is_valid():
                    doc = doc_form.save(commit=False)
                    doc.employee_master = employee
                    doc.uploaded_by = request.user
                    doc.save()
                    messages.success(request, f"Document '{doc.get_document_type_display()}' uploaded successfully.")
                else:
                    messages.error(request, "Failed to upload document. Please select a valid file.")
                next_step = 5
            else:
                next_step = 6

            ctx = self.get_context_data(request, uuid=employee.uuid, step=next_step)
            if request.headers.get('HX-Request'):
                response = render(request, 'employees/wizard/wizard_content.html', ctx)
                response['HX-Push-Url'] = f"/employees/wizard/{employee.uuid}/step/{next_step}/"
                return response
            return redirect('employees:employee_wizard_step', uuid=employee.uuid, step=next_step)

        elif step == 6:
            form = WizardStep6Form(request.POST, instance=employee)
            if form.is_valid():
                form.save()
                messages.success(request, "Step 6 (Emergency Contact) saved successfully.")
                next_step = int(request.POST.get('next_step', 7))
                if request.headers.get('HX-Request'):
                    ctx = self.get_context_data(request, uuid=employee.uuid, step=next_step)
                    response = render(request, 'employees/wizard/wizard_content.html', ctx)
                    response['HX-Push-Url'] = f"/employees/wizard/{employee.uuid}/step/{next_step}/"
                    return response
                return redirect('employees:employee_wizard_step', uuid=employee.uuid, step=next_step)
            else:
                ctx = self.get_context_data(request, uuid=employee.uuid, step=6, form=form)
                return render(request, 'employees/wizard/wizard_content.html' if request.headers.get('HX-Request') else 'employees/employee_wizard.html', ctx)

        elif step == 7:
            # Asset Assignment
            action_type = request.POST.get('action_type', 'assign')
            if action_type == 'assign':
                asset_form = AssetAssignmentForm(request.POST)
                if asset_form.is_valid():
                    assign = asset_form.save(commit=False)
                    assign.employee = employee
                    assign.assigned_by = request.user
                    assign.save()
                    messages.success(request, f"Asset '{assign.asset.name}' assigned successfully.")
                else:
                    messages.error(request, "Could not assign asset. Check if asset is available.")
                next_step = 7
            else:
                next_step = 8

            ctx = self.get_context_data(request, uuid=employee.uuid, step=next_step)
            if request.headers.get('HX-Request'):
                response = render(request, 'employees/wizard/wizard_content.html', ctx)
                response['HX-Push-Url'] = f"/employees/wizard/{employee.uuid}/step/{next_step}/"
                return response
            return redirect('employees:employee_wizard_step', uuid=employee.uuid, step=next_step)

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

        is_owner = (
            (doc.employee_master and doc.employee_master.user == request.user) or
            (doc.employee and doc.employee.user == request.user)
        )

        if not is_owner and not request.user.is_superuser:
            if not PermissionEngine.evaluate(request.user, 'employees.view').allowed:
                log_audit(
                    actor=request.user,
                    action='document_access_denied',
                    target=doc,
                    summary=f"Unauthorized download attempt for document pk={doc.pk}"
                )
                return HttpResponseForbidden(
                    "Permission Denied: You do not have permission to download this document."
                )

            if doc.is_sensitive():
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
    Read-only timeline view combining HR history, lifecycle requests, leave requests,
    asset assignments, document uploads, and attendance logs.
    """
    model = Employee
    template_name = 'employees/employee_timeline.html'
    context_object_name = 'employee'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        employee = self.object
        profile = getattr(employee, 'legacy_profile', None)

        # Filters
        category = self.request.GET.get('category', 'all')
        start_date_str = self.request.GET.get('start_date')
        end_date_str = self.request.GET.get('end_date')

        # Pagination
        try:
            page = int(self.request.GET.get('page', 1))
        except ValueError:
            page = 1
        page_size = 15
        limit = page * page_size

        events = []

        # Helper to apply date filters
        def filter_dates(qs, field):
            nonlocal start_date_str, end_date_str
            if start_date_str:
                qs = qs.filter(**{f"{field}__gte": start_date_str})
            if end_date_str:
                qs = qs.filter(**{f"{field}__lte": f"{end_date_str} 23:59:59"})
            return qs

        # 1. HR & Lifecycle Transitions
        if category in ('all', 'hr'):
            hist_qs = employee.employment_history.select_related('approved_by')
            hist_qs = filter_dates(hist_qs, 'created_at')
            for h in hist_qs.order_by('-created_at')[:limit]:
                events.append({
                    'timestamp': h.created_at,
                    'category': 'hr',
                    'icon': 'user-cog',
                    'title': f"Field '{h.field_changed.replace('_', ' ').title()}' updated",
                    'description': f"Old: {h.old_value} → New: {h.new_value}",
                    'reason': h.reason,
                    'actor': h.approved_by,
                })

            life_qs = employee.lifecycle_requests.select_related('requested_by', 'reviewed_by')
            life_qs = filter_dates(life_qs, 'requested_at')
            for l in life_qs.order_by('-requested_at')[:limit]:
                events.append({
                    'timestamp': l.requested_at,
                    'category': 'hr',
                    'icon': 'git-pull-request',
                    'title': f"Status transition: {l.from_status.title()} → {l.to_status.title()}",
                    'description': f"Approval status: {l.review_status.upper()}",
                    'reason': l.reason,
                    'actor': l.reviewed_by or l.requested_by,
                })

        # 2. Leave requests
        if category in ('all', 'leave') and profile:
            leave_qs = profile.leave_requests.select_related('leave_type', 'reviewed_by')
            leave_qs = filter_dates(leave_qs, 'requested_at')
            for lv in leave_qs.order_by('-requested_at')[:limit]:
                events.append({
                    'timestamp': lv.requested_at,
                    'category': 'leave',
                    'icon': 'calendar',
                    'title': f"Leave Request: {lv.leave_type.name}",
                    'description': f"Period: {lv.start_date} to {lv.end_date} ({lv.number_of_days} days) - Status: {lv.status.upper()}",
                    'reason': lv.reason,
                    'actor': lv.reviewed_by or profile.user,
                })

        # 3. Asset Assignments
        if category in ('all', 'asset'):
            asset_qs = employee.asset_assignments.select_related('asset', 'assigned_by')
            asset_qs = filter_dates(asset_qs, 'assigned_date')
            for ast in asset_qs.order_by('-assigned_date')[:limit]:
                title_str = f"Asset Assigned: {ast.asset.name}"
                desc_str = f"Tag: {ast.asset.asset_tag} | Condition: {ast.condition_at_assignment.upper()}"
                if ast.returned_date:
                    title_str = f"Asset Returned: {ast.asset.name}"
                    desc_str += f" | Returned condition: {ast.condition_at_return.upper() if ast.condition_at_return else 'Unknown'}"
                events.append({
                    'timestamp': timezone.make_aware(datetime.combine(ast.assigned_date, time.min)),
                    'category': 'asset',
                    'icon': 'laptop',
                    'title': title_str,
                    'description': desc_str,
                    'reason': ast.notes,
                    'actor': ast.assigned_by,
                })

        # 4. Documents
        if category in ('all', 'document'):
            from apps.employees.models import EmployeeDocument
            from django.db.models import Q
            doc_qs = EmployeeDocument.objects.filter(
                Q(employee_master=employee) | Q(employee=profile) if profile else Q(employee_master=employee)
            ).select_related('uploaded_by')
            doc_qs = filter_dates(doc_qs, 'uploaded_at')
            for doc in doc_qs.order_by('-uploaded_at')[:limit]:
                events.append({
                    'timestamp': doc.uploaded_at,
                    'category': 'document',
                    'icon': 'file-text',
                    'title': f"Document Uploaded: {doc.get_document_type_display()}",
                    'description': f"Filename: {doc.file.name if doc.file else 'No file'} | Version: v{doc.version}",
                    'reason': doc.title,
                    'actor': doc.uploaded_by,
                })

        # 5. Attendance
        if category in ('all', 'attendance') and profile:
            from apps.attendance.models import Attendance
            att_qs = Attendance.objects.filter(employee=profile)
            att_qs = filter_dates(att_qs, 'check_in_time')
            for att in att_qs.order_by('-check_in_time')[:limit]:
                events.append({
                    'timestamp': att.check_in_time,
                    'category': 'attendance',
                    'icon': 'clock',
                    'title': f"Attendance Session ({att.status.title()})",
                    'description': f"Checked in at {att.check_in_time.strftime('%H:%M') if att.check_in_time else '—'} | Checked out at {att.check_out_time.strftime('%H:%M') if att.check_out_time else '—'}",
                    'reason': f"Work mode: {att.work_mode.upper()}",
                    'actor': profile.user,
                })

        # 6. Expenses
        if category in ('all', 'expense') and profile:
            from apps.expense.models import Expense
            exp_qs = Expense.objects.filter(employee=profile)
            exp_qs = filter_dates(exp_qs, 'requested_at')
            for exp in exp_qs.order_by('-requested_at')[:limit]:
                events.append({
                    'timestamp': exp.requested_at,
                    'category': 'expense',
                    'icon': 'receipt',
                    'title': f"Expense Claim ({exp.get_status_display()})",
                    'description': f"Category: {exp.get_category_display()} | Amount: {exp.amount}",
                    'reason': exp.description,
                    'actor': exp.reviewed_by or profile.user,
                })

        # Sort and Slice paginated segment
        events.sort(key=lambda x: x['timestamp'], reverse=True)
        start_idx = (page - 1) * page_size
        end_idx = page * page_size
        
        context['timeline_events'] = events[start_idx:end_idx]
        context['has_next'] = len(events) > end_idx
        context['next_page'] = page + 1
        context['category_filter'] = category
        context['start_date'] = start_date_str
        context['end_date'] = end_date_str

        # If it's an HTMX request for infinite scroll/pagination, render only the partial row list
        if self.request.headers.get('HX-Request') and self.request.GET.get('page'):
            self.template_name = 'employees/partials/timeline_events_rows.html'

        return context


class EmployeeSuspendToggleView(AdminRequiredMixin, View):
    def post(self, request, pk):
        employee = get_object_or_404(Employee, pk=pk)
        reason = request.POST.get('reason', '').strip()
        if not reason:
            if request.headers.get('HX-Request'):
                from django.urls import reverse
                return render(request, 'employees/partials/confirmation_modal.html', {
                    'title': "Un-suspend employee?" if employee.is_suspended else "Suspend employee?",
                    'endpoint': reverse('employees:employee_suspend_toggle', kwargs={'pk': employee.pk}),
                    'message': f"Are you sure you want to change the suspension status for {employee.get_full_name()}?",
                    'action': 'suspend',
                    'action_label': "Un-suspend Profile" if employee.is_suspended else "Suspend Profile",
                    'reason_required': True,
                    'reason_error': "Reason is required to suspend/unsuspend an employee.",
                    'suspension_dates': not employee.is_suspended,
                    'today_str': timezone.localdate().isoformat(),
                })
            messages.error(request, "Reason is required to suspend/unsuspend an employee.")
            return redirect('employees:employee_detail', pk=employee.pk)

        is_suspended = not employee.is_suspended
        before_state = {'is_suspended': not is_suspended}
        after_state = {'is_suspended': is_suspended}

        from apps.employees.models import EmployeeSuspension
        if is_suspended:
            start_date = request.POST.get('suspension_start_date') or timezone.localdate().isoformat()
            end_date = request.POST.get('suspension_end_date') or None
            auto_react = request.POST.get('auto_reactivate') == 'on'
            EmployeeSuspension.objects.filter(employee=employee, is_active=True).update(is_active=False)
            EmployeeSuspension.objects.create(
                employee=employee,
                suspension_start_date=start_date,
                suspension_end_date=end_date,
                suspension_reason=reason,
                auto_reactivate=auto_react,
                is_active=True,
                changed_by=request.user,
                previous_status=employee.status,
            )
            employee.status = 'suspended'
        else:
            EmployeeSuspension.objects.filter(employee=employee, is_active=True).update(is_active=False)
            # Restore previous status or fallback to active
            employee.status = 'active'

        employee.is_suspended = is_suspended
        employee._bypass_lifecycle_validation = True
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

        from apps.audit.services import AuditService
        AuditService.log_event(
            actor=request.user,
            action='suspended' if is_suspended else 'reactivated',
            instance=employee,
            before=before_state,
            after=after_state,
            changed_fields={'is_suspended': {'before': not is_suspended, 'after': is_suspended}},
            reason=reason,
            request=request
        )

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
        from django.urls import reverse
        title = "Un-suspend employee?" if employee.is_suspended else "Suspend employee?"
        action_label = "Un-suspend Profile" if employee.is_suspended else "Suspend Profile"
        message = f"Are you sure you want to change the suspension status for {employee.get_full_name()}?"
        
        return render(request, 'employees/partials/confirmation_modal.html', {
            'title': title,
            'endpoint': reverse('employees:employee_suspend_toggle', kwargs={'pk': employee.pk}),
            'message': message,
            'action': 'suspend',
            'action_label': action_label,
            'reason_required': True,
            'suspension_dates': not employee.is_suspended,
            'today_str': timezone.localdate().isoformat(),
        })



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


from django.views.generic import TemplateView
from apps.employees.hierarchy_services import OrgHierarchyService
from apps.employees.models import ManagerDelegation

class OrgChartView(AdminRequiredMixin, TemplateView):
    template_name = 'employees/org_chart.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Fetch root nodes (employees without reporting manager)
        roots = Employee.objects.filter(reporting_manager__isnull=True).exclude(status='archived').select_related(
            'branch', 'department', 'designation', 'user'
        )
        context['roots'] = roots
        return context

class OrgChartNodeView(AdminRequiredMixin, View):
    def get(self, request, pk):
        # HTMX lazy load node direct reports
        employee = get_object_or_404(Employee, pk=pk)
        directs = OrgHierarchyService.get_direct_reports(employee)
        return render(request, 'employees/partials/org_node_children.html', {
            'directs': directs
        })

class ManagerDelegationListView(AdminRequiredMixin, ListView):
    model = ManagerDelegation
    template_name = 'employees/delegation_list.html'
    context_object_name = 'delegations'

    def get_queryset(self):
        return ManagerDelegation.objects.select_related('manager', 'delegate_to', 'created_by').all()

class ManagerDelegationCreateView(AdminRequiredMixin, View):
    def get(self, request):
        active_employees = Employee.objects.exclude(status='archived').order_by('first_name')
        return render(request, 'employees/partials/delegation_create_modal.html', {
            'employees': active_employees
        })

    def post(self, request):
        manager_id = request.POST.get('manager')
        delegate_to_id = request.POST.get('delegate_to')
        start_date = request.POST.get('start_date')
        end_date = request.POST.get('end_date')
        reason = request.POST.get('reason', '')

        try:
            delg = ManagerDelegation.objects.create(
                manager_id=manager_id,
                delegate_to_id=delegate_to_id,
                start_date=start_date,
                end_date=end_date,
                reason=reason,
                created_by=request.user
            )
            log_audit(
                actor=request.user,
                action='delegation_created',
                target=delg,
                summary=f"Created delegation from {delg.manager.get_full_name()} to {delg.delegate_to.get_full_name()}"
            )
            messages.success(request, "Delegation created successfully.")
        except ValidationError as e:
            messages.error(request, f"Error: {e.message_dict if hasattr(e, 'message_dict') else e.message}")
        except Exception as e:
            messages.error(request, f"Unexpected error: {str(e)}")

        return redirect('employees:delegation_list')

class ManagerDelegationEndView(AdminRequiredMixin, View):
    def post(self, request, pk):
        delg = get_object_or_404(ManagerDelegation, pk=pk)
        delg.is_active = False
        delg.end_date = timezone.localdate()
        delg.save(update_fields=['is_active', 'end_date'])
        
        log_audit(
            actor=request.user,
            action='delegation_ended',
            target=delg,
            summary=f"Manually ended delegation from {delg.manager.get_full_name()} to {delg.delegate_to.get_full_name()}"
        )
        messages.success(request, "Delegation ended successfully.")
        return redirect('employees:delegation_list')


class EmployeeReportsView(AdminRequiredMixin, TemplateView):
    template_name = 'employees/reports.html'

    def get_context_data(self, **kwargs):
        from django.db.models import Count
        from datetime import timedelta
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        
        # 1. Lifecycle Status Report
        status_counts = Employee.objects.exclude(status='archived').values('status').annotate(count=Count('id')).order_by('status')
        status_list = []
        for item in status_counts:
            status_list.append({
                'status': item['status'],
                'status_display': dict(EmployeeStatus.choices).get(item['status'], item['status']).capitalize(),
                'count': item['count']
            })
        context['status_reports'] = status_list
        context['employees_by_status'] = Employee.objects.exclude(status='archived').select_related('branch', 'department', 'designation', 'user', 'legacy_profile').all().order_by('status', 'first_name')

        # 2. Document Expiry Report
        context['expiring_documents'] = EmployeeDocument.objects.filter(
            is_active=True,
            is_archived=False,
            expiry_date__isnull=False
        ).select_related('employee_master', 'employee').order_by('expiry_date')
        
        # 3. Asset Allocation Report
        context['asset_assignments'] = AssetAssignment.objects.filter(
            returned_date__isnull=True
        ).select_related('asset', 'employee', 'assigned_by').order_by('assigned_date')
        context['all_assets'] = Asset.objects.all().prefetch_related('assignments__employee')
        
        return context


# ==========================================
# DEPARTMENT MANAGEMENT VIEWS
# ==========================================

class DepartmentListView(AdminRequiredMixin, ListView):
    model = Department
    template_name = 'employees/department_list.html'
    context_object_name = 'departments'
    paginate_by = 20

    def get_queryset(self):
        qs = Department.objects.prefetch_related('branches', 'designations')
        search = self.request.GET.get('q', '').strip()
        branch_filter = self.request.GET.get('branch', '').strip()
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(code__icontains=search))
        if branch_filter:
            qs = qs.filter(Q(is_global=True) | Q(branches__id=branch_filter)).distinct()
        return qs.order_by('name')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from apps.branches.utils import get_cached_branches
        context['branches'] = get_cached_branches()
        context['search'] = self.request.GET.get('q', '')
        context['branch_filter'] = self.request.GET.get('branch', '')
        return context

    def render_to_response(self, context, **response_kwargs):
        if self.request.headers.get('HX-Request') and self.request.headers.get('HX-Target') != 'modal-container':
            return render(self.request, 'employees/partials/department_table.html', context)
        return super().render_to_response(context, **response_kwargs)


class DepartmentCreateView(AdminRequiredMixin, CreateView):
    model = Department
    template_name = 'employees/partials/department_form_modal.html'

    def get_form_class(self):
        from apps.employees.forms import DepartmentForm
        return DepartmentForm

    def form_valid(self, form):
        dept = form.save(commit=False)
        dept.save()
        form.save_m2m()
        log_audit(
            actor=self.request.user,
            action='department_created',
            target=dept,
            summary=f"Created Department {dept.name}"
        )
        if self.request.headers.get('HX-Request'):
            response = HttpResponse(status=204)
            response['HX-Redirect'] = reverse_lazy('employees:department_list')
            return response
        messages.success(self.request, f'Department "{dept.name}" created.')
        return redirect('employees:department_list')

    def form_invalid(self, form):
        if self.request.headers.get('HX-Request'):
            return render(self.request, self.template_name, {'form': form})
        return super().form_invalid(form)


class DepartmentEditView(AdminRequiredMixin, UpdateView):
    model = Department
    template_name = 'employees/partials/department_form_modal.html'

    def get_form_class(self):
        from apps.employees.forms import DepartmentForm
        return DepartmentForm

    def form_valid(self, form):
        dept = form.save(commit=False)
        dept.save()
        form.save_m2m()
        log_audit(
            actor=self.request.user,
            action='department_updated',
            target=dept,
            summary=f"Updated Department {dept.name}"
        )
        if self.request.headers.get('HX-Request'):
            response = HttpResponse(status=204)
            response['HX-Redirect'] = reverse_lazy('employees:department_list')
            return response
        messages.success(self.request, f'Department "{dept.name}" updated.')
        return redirect('employees:department_list')

    def form_invalid(self, form):
        if self.request.headers.get('HX-Request'):
            return render(self.request, self.template_name, {'form': form, 'object': self.object})
        return super().form_invalid(form)


class DepartmentDeleteView(AdminRequiredMixin, View):
    def post(self, request, pk):
        dept = get_object_or_404(Department, pk=pk)
        name = dept.name
        dept.delete()
        log_audit(
            actor=request.user,
            action='department_deleted',
            target=None,
            summary=f"Deleted Department {name}"
        )
        if request.headers.get('HX-Request'):
            response = HttpResponse(status=204)
            response['HX-Redirect'] = reverse_lazy('employees:department_list')
            return response
        messages.success(request, f'Department "{name}" deleted.')
        return redirect('employees:department_list')


class DepartmentExportCSVView(AdminRequiredMixin, View):
    def get(self, request):
        import csv
        departments = Department.objects.prefetch_related('branches').all().order_by('name')
        
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="departments.csv"'
        
        writer = csv.writer(response)
        writer.writerow(['Name', 'Code', 'Description', 'Is Global', 'Branches', 'Is Active'])
        
        for dept in departments:
            branches_str = ",".join([b.name for b in dept.branches.all()]) if not dept.is_global else 'All'
            writer.writerow([
                dept.name,
                dept.code or '',
                dept.description or '',
                'True' if dept.is_global else 'False',
                branches_str,
                'True' if dept.is_active else 'False'
            ])
            
        return response


class DepartmentImportCSVView(AdminRequiredMixin, View):
    def post(self, request):
        import csv
        import io
        from apps.branches.models import Branch
        csv_file = request.FILES.get('file')
        if not csv_file:
            messages.error(request, 'No file uploaded.')
            return redirect('employees:department_list')
        
        if not csv_file.name.endswith('.csv'):
            messages.error(request, 'Please upload a CSV file.')
            return redirect('employees:department_list')
        
        try:
            file_data = csv_file.read().decode('utf-8')
            csv_reader = csv.reader(io.StringIO(file_data))
            header = next(csv_reader) # Skip header
            
            created_count = 0
            updated_count = 0
            
            for row in csv_reader:
                if not row or len(row) < 1:
                    continue
                name = row[0].strip()
                if not name:
                    continue
                
                code = row[1].strip() if len(row) > 1 else ''
                description = row[2].strip() if len(row) > 2 else ''
                is_global_str = row[3].strip().lower() if len(row) > 3 else 'true'
                is_global = is_global_str in ['true', '1', 'yes']
                
                branches_str = row[4].strip() if len(row) > 4 else ''
                is_active_str = row[5].strip().lower() if len(row) > 5 else 'true'
                is_active = is_active_str in ['true', '1', 'yes']
                
                dept, created = Department.objects.get_or_create(
                    name=name,
                    defaults={
                        'code': code,
                        'description': description,
                        'is_global': is_global,
                        'is_active': is_active
                    }
                )
                
                if not created:
                    dept.code = code
                    dept.description = description
                    dept.is_global = is_global
                    dept.is_active = is_active
                    dept.save()
                    updated_count += 1
                else:
                    created_count += 1
                
                if not is_global and branches_str and branches_str.lower() != 'all':
                    dept.branches.clear()
                    branch_names = [b.strip() for b in branches_str.split(',') if b.strip()]
                    for b_name in branch_names:
                        branch = Branch.objects.filter(name__iexact=b_name).first()
                        if branch:
                            dept.branches.add(branch)
                            
            messages.success(request, f'Successfully imported departments. Created: {created_count}, Updated: {updated_count}')
            log_audit(
                actor=request.user,
                action='departments_imported',
                target=None,
                summary=f"Imported departments via CSV. Created: {created_count}, Updated: {updated_count}"
            )
        except Exception as e:
            messages.error(request, f'Failed to parse CSV file: {str(e)}')
            
        return redirect('employees:department_list')


# ==========================================
# DESIGNATION MANAGEMENT VIEWS
# ==========================================

class DesignationListView(AdminRequiredMixin, ListView):
    model = Designation
    template_name = 'employees/designation_list.html'
    context_object_name = 'designations'
    paginate_by = 20

    def get_queryset(self):
        qs = Designation.objects.select_related('department')
        search = self.request.GET.get('q', '').strip()
        dept_filter = self.request.GET.get('department', '').strip()
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(code__icontains=search))
        if dept_filter:
            qs = qs.filter(department_id=dept_filter)
        return qs.order_by('department__name', 'name')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['departments'] = Department.objects.filter(is_active=True).order_by('name')
        context['search'] = self.request.GET.get('q', '')
        context['dept_filter'] = self.request.GET.get('department', '')
        return context

    def render_to_response(self, context, **response_kwargs):
        if self.request.headers.get('HX-Request') and self.request.headers.get('HX-Target') != 'modal-container':
            return render(self.request, 'employees/partials/designation_table.html', context)
        return super().render_to_response(context, **response_kwargs)


class DesignationCreateView(AdminRequiredMixin, CreateView):
    model = Designation
    template_name = 'employees/partials/designation_form_modal.html'

    def get_form_class(self):
        from apps.employees.forms import DesignationForm
        return DesignationForm

    def form_valid(self, form):
        desig = form.save()
        log_audit(
            actor=self.request.user,
            action='designation_created',
            target=desig,
            summary=f"Created Designation {desig.name}"
        )
        if self.request.headers.get('HX-Request'):
            response = HttpResponse(status=204)
            response['HX-Redirect'] = reverse_lazy('employees:designation_list')
            return response
        messages.success(self.request, f'Designation "{desig.name}" created.')
        return redirect('employees:designation_list')

    def form_invalid(self, form):
        if self.request.headers.get('HX-Request'):
            return render(self.request, self.template_name, {'form': form})
        return super().form_invalid(form)


class DesignationEditView(AdminRequiredMixin, UpdateView):
    model = Designation
    template_name = 'employees/partials/designation_form_modal.html'

    def get_form_class(self):
        from apps.employees.forms import DesignationForm
        return DesignationForm

    def form_valid(self, form):
        desig = form.save()
        log_audit(
            actor=self.request.user,
            action='designation_updated',
            target=desig,
            summary=f"Updated Designation {desig.name}"
        )
        if self.request.headers.get('HX-Request'):
            response = HttpResponse(status=204)
            response['HX-Redirect'] = reverse_lazy('employees:designation_list')
            return response
        messages.success(self.request, f'Designation "{desig.name}" updated.')
        return redirect('employees:designation_list')

    def form_invalid(self, form):
        if self.request.headers.get('HX-Request'):
            return render(self.request, self.template_name, {'form': form, 'object': self.object})
        return super().form_invalid(form)


# ==========================================
# HTMX CASCADE API ENDPOINTS
# ==========================================

import json


class DepartmentsForBranchAPIView(AdminRequiredMixin, View):
    """JSON endpoint: departments available for a given branch."""

    def get(self, request):
        branch_id = request.GET.get('branch', '')
        branch = None
        if branch_id:
            try:
                branch = Branch.objects.get(pk=branch_id)
            except Branch.DoesNotExist:
                pass
        departments = Department.available_for_branch(branch)
        data = [{'id': d.pk, 'name': str(d)} for d in departments]
        return HttpResponse(json.dumps(data), content_type='application/json')


class DesignationsForDepartmentAPIView(AdminRequiredMixin, View):
    """JSON endpoint: designations under a given department."""

    def get(self, request):
        dept_id = request.GET.get('department', '')
        if not dept_id:
            return HttpResponse(json.dumps([]), content_type='application/json')
        try:
            dept = Department.objects.get(pk=dept_id)
        except Department.DoesNotExist:
            return HttpResponse(json.dumps([]), content_type='application/json')
        designations = Designation.available_for_department(dept)
        data = [{'id': d.pk, 'name': str(d)} for d in designations]
        return HttpResponse(json.dumps(data), content_type='application/json')


class DesignationExportCSVView(AdminRequiredMixin, View):
    def get(self, request):
        import csv
        designations = Designation.objects.select_related('department').all().order_by('name')
        
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="designations.csv"'
        
        writer = csv.writer(response)
        writer.writerow(['Name', 'Code', 'Department'])
        
        for des in designations:
            writer.writerow([
                des.name,
                des.code or '',
                des.department.name if des.department else ''
            ])
            
        return response


class DesignationImportCSVView(AdminRequiredMixin, View):
    def post(self, request):
        import csv
        import io
        csv_file = request.FILES.get('file')
        if not csv_file:
            messages.error(request, 'No file uploaded.')
            return redirect('employees:designation_list')
        
        if not csv_file.name.endswith('.csv'):
            messages.error(request, 'Please upload a CSV file.')
            return redirect('employees:designation_list')
        
        try:
            file_data = csv_file.read().decode('utf-8')
            csv_reader = csv.reader(io.StringIO(file_data))
            header = next(csv_reader) # Skip header
            
            created_count = 0
            updated_count = 0
            
            for row in csv_reader:
                if not row or len(row) < 1:
                    continue
                name = row[0].strip()
                if not name:
                    continue
                
                code = row[1].strip() if len(row) > 1 else ''
                dept_name = row[2].strip() if len(row) > 2 else ''
                
                dept = None
                if dept_name:
                    dept = Department.objects.filter(name__iexact=dept_name).first()
                
                desig, created = Designation.objects.get_or_create(
                    name=name,
                    defaults={
                        'code': code,
                        'department': dept
                    }
                )
                
                if not created:
                    desig.code = code
                    desig.department = dept
                    desig.save()
                    updated_count += 1
                else:
                    created_count += 1
                            
            messages.success(request, f'Successfully imported designations. Created: {created_count}, Updated: {updated_count}')
            log_audit(
                actor=request.user,
                action='designations_imported',
                target=None,
                summary=f"Imported designations via CSV. Created: {created_count}, Updated: {updated_count}"
            )
        except Exception as e:
            messages.error(request, f'Failed to parse CSV file: {str(e)}')
            
        return redirect('employees:designation_list')


class EmployeeExportCSVView(AdminRequiredMixin, View):
    def get(self, request):
        import csv
        employees = Employee.objects.select_related('branch', 'department', 'designation', 'user').all().order_by('first_name', 'last_name')
        
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="employees.csv"'
        
        writer = csv.writer(response)
        writer.writerow(['First Name', 'Last Name', 'Email', 'Phone', 'Employee Number', 'Branch', 'Department', 'Designation', 'Status', 'Joined Date'])
        
        for emp in employees:
            writer.writerow([
                emp.first_name,
                emp.last_name,
                emp.user.email if emp.user else '',
                emp.phone or '',
                emp.employee_number or '',
                emp.branch.name if emp.branch else '',
                emp.department.name if emp.department else '',
                emp.designation.name if emp.designation else '',
                emp.status,
                emp.joined_date.isoformat() if emp.joined_date else ''
            ])
            
        return response


class EmployeeImportCSVView(AdminRequiredMixin, View):
    def post(self, request):
        import csv
        import io
        from apps.branches.models import Branch
        from django.contrib.auth import get_user_model
        User = get_user_model()
        
        csv_file = request.FILES.get('file')
        if not csv_file:
            messages.error(request, 'No file uploaded.')
            return redirect('employees:master_list')
        
        if not csv_file.name.endswith('.csv'):
            messages.error(request, 'Please upload a CSV file.')
            return redirect('employees:master_list')
        
        try:
            file_data = csv_file.read().decode('utf-8')
            csv_reader = csv.reader(io.StringIO(file_data))
            header = next(csv_reader) # Skip header
            
            created_count = 0
            updated_count = 0
            
            for row in csv_reader:
                if not row or len(row) < 5:
                    continue
                first_name = row[0].strip()
                last_name = row[1].strip()
                email = row[2].strip()
                phone = row[3].strip()
                emp_number = row[4].strip()
                branch_name = row[5].strip() if len(row) > 5 else ''
                dept_name = row[6].strip() if len(row) > 6 else ''
                desig_name = row[7].strip() if len(row) > 7 else ''
                status = row[8].strip().lower() if len(row) > 8 else 'active'
                joined_date_str = row[9].strip() if len(row) > 9 else ''
                
                if not first_name or not last_name or not emp_number:
                    continue
                
                branch = Branch.objects.filter(name__iexact=branch_name).first() if branch_name else None
                dept = Department.objects.filter(name__iexact=dept_name).first() if dept_name else None
                desig = Designation.objects.filter(name__iexact=desig_name).first() if desig_name else None
                
                user = None
                if email:
                    user = User.objects.filter(email__iexact=email).first()
                    if not user:
                        user = User.objects.create_user(
                            email=email,
                            password='TempPassword123!',
                            role='staff',
                            is_active=True
                        )
                
                emp, created = Employee.objects.get_or_create(
                    employee_number=emp_number,
                    defaults={
                        'first_name': first_name,
                        'last_name': last_name,
                        'user': user,
                        'phone': phone,
                        'branch': branch,
                        'department': dept,
                        'designation': desig,
                        'status': status,
                    }
                )
                
                if not created:
                    emp.first_name = first_name
                    emp.last_name = last_name
                    if user:
                        emp.user = user
                    emp.phone = phone
                    emp.branch = branch
                    emp.department = dept
                    emp.designation = desig
                    emp.status = status
                    emp.save()
                    updated_count += 1
                else:
                    created_count += 1
                            
            messages.success(request, f'Successfully imported employees. Created: {created_count}, Updated: {updated_count}')
            log_audit(
                actor=request.user,
                action='employees_imported',
                target=None,
                summary=f"Imported employees via CSV. Created: {created_count}, Updated: {updated_count}"
            )
        except Exception as e:
            messages.error(request, f'Failed to parse CSV file: {str(e)}')
            
        return redirect('employees:master_list')

