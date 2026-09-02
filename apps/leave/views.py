import json
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.views.generic import ListView, DetailView, CreateView, UpdateView, View, TemplateView
from django.urls import reverse_lazy
from django.utils import timezone
from django.contrib import messages
from apps.attendance.sync_utils import parse_and_validate_client_time
from apps.accounts.mixins import AdminRequiredMixin, StaffRequiredMixin, RoleRequiredMixin
from apps.employees.models import EmployeeProfile
from .models import LeaveType, LeaveBalance, LeaveRequest
from .forms import LeaveRequestForm, LeaveTypeForm

def _get_profile(user):
    if not user or not user.is_authenticated:
        return None
    if hasattr(user, 'employee_profile') and user.employee_profile:
        return user.employee_profile
    from apps.employees.hr_resolver import get_canonical_employee
    canonical_emp = get_canonical_employee(user)
    if canonical_emp:
        if hasattr(canonical_emp, 'employee_profile'):
            return canonical_emp.employee_profile
        from apps.employees.models import EmployeeProfile
        if isinstance(canonical_emp, EmployeeProfile):
            return canonical_emp
    return None


class StaffOrManagerMixin(RoleRequiredMixin):
    """Allows staff role to access employee-facing views."""
    allowed_roles = ['staff']

# ==============================================================================
# Admin Views
# ==============================================================================

class AdminLeaveDashboardView(AdminRequiredMixin, ListView):
    model = LeaveRequest
    template_name = 'admin_panel/leave/request_list.html'
    context_object_name = 'requests'
    paginate_by = 15

    def get_queryset(self):
        status = self.request.GET.get('status')
        queryset = LeaveRequest.objects.all().select_related('employee', 'leave_type')
        if status in ['pending', 'approved', 'rejected']:
            queryset = queryset.filter(status=status)
        from django.db.models import Case, When
        return queryset.order_by(Case(When(status='pending', then=0), default=1), '-requested_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['pending_count'] = LeaveRequest.objects.filter(status='pending').count()
        context['approved_count'] = LeaveRequest.objects.filter(status='approved').count()
        context['rejected_count'] = LeaveRequest.objects.filter(status='rejected').count()
        context['current_status'] = self.request.GET.get('status', 'all')
        return context

    def get(self, request, *args, **kwargs):
        self.object_list = self.get_queryset()
        context = self.get_context_data()
        if request.headers.get('HX-Request') == 'true':
            return render(request, 'admin_panel/leave/partials/request_list_partial.html', context)
        return self.render_to_response(context)

class BaseProcessLeaveRequestView(View):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('/login/')

        from apps.accounts.engine import PermissionEngine
        res = PermissionEngine.evaluate(request.user, 'leave.approve')
        if not res.allowed and not request.user.is_superuser and getattr(request.user, 'role', '') not in ('admin', 'manager'):
            if PermissionEngine.evaluate(request.user, 'accounts.view').allowed or getattr(request.user, 'role', '') == 'admin':
                return redirect('/admin-panel/dashboard/')
            return redirect('/staff/home/')

        # Scoping check for manager/team scope
        if res.allowed and res.data_scope != 'global' and not request.user.is_superuser:
            leave_request = get_object_or_404(LeaveRequest, pk=kwargs.get('pk'))
            profile = _get_profile(request.user)
            
            is_reporting_manager = False
            emp_master = getattr(leave_request.employee, 'master_employee', None)
            if emp_master and emp_master.reporting_manager:
                if emp_master.reporting_manager.user == request.user:
                    is_reporting_manager = True
            
            scoped = is_reporting_manager
            if not scoped and profile:
                if profile.branch and leave_request.employee.branch == profile.branch:
                    scoped = True
                elif not profile.branch:
                    from django.db.models import Q
                    from apps.projects.models import Project
                    managed_projects = Project.objects.filter(project_managers=profile)
                    project_employees = EmployeeProfile.objects.filter(
                        Q(site_engineer_projects__in=managed_projects) |
                        Q(assigned_tasks__project__in=managed_projects)
                    ).distinct()
                    if leave_request.employee in project_employees:
                        scoped = True
            if not scoped:
                from django.http import HttpResponseForbidden
                return HttpResponseForbidden("You do not have permission to process leave requests outside your scope.")

        return super().dispatch(request, *args, **kwargs)

class ApproveLeaveRequestView(BaseProcessLeaveRequestView):
    def post(self, request, pk):
        leave_request = get_object_or_404(LeaveRequest, pk=pk)
        wf_instance = leave_request.workflow_instance
        if wf_instance and not wf_instance.completed_at:
            from apps.workflow.services import record_action
            record_action(wf_instance, request.user, 'approve', 'Approved via view')
            messages.success(request, f"Leave request for {leave_request.employee.full_name} has been processed.")
        else:
            if leave_request.status in ('pending', 'manager_approved', 'returned'):
                leave_request.status = 'approved'
                leave_request.reviewed_by = request.user
                leave_request.reviewed_at = timezone.now()
                leave_request.save()
                messages.success(request, f"Leave request for {leave_request.employee.full_name} has been approved.")
            else:
                messages.error(request, "This request has already been processed.")
        referer = request.META.get('HTTP_REFERER')
        if referer:
            return redirect(referer)
        return redirect('leave:admin_dashboard')

class RejectLeaveRequestView(BaseProcessLeaveRequestView):
    def post(self, request, pk):
        leave_request = get_object_or_404(LeaveRequest, pk=pk)
        wf_instance = leave_request.workflow_instance
        if wf_instance and not wf_instance.completed_at:
            from apps.workflow.services import record_action
            record_action(wf_instance, request.user, 'reject', 'Rejected via view')
            messages.success(request, f"Leave request for {leave_request.employee.full_name} has been processed.")
        else:
            if leave_request.status in ('pending', 'manager_approved', 'returned'):
                leave_request.status = 'rejected'
                leave_request.reviewed_by = request.user
                leave_request.reviewed_at = timezone.now()
                leave_request.save()
                messages.success(request, f"Leave request for {leave_request.employee.full_name} has been rejected.")
            else:
                messages.error(request, "This request has already been processed.")
        referer = request.META.get('HTTP_REFERER')
        if referer:
            return redirect(referer)
        return redirect('leave:admin_dashboard')

class ReturnLeaveRequestView(BaseProcessLeaveRequestView):
    def post(self, request, pk):
        leave_request = get_object_or_404(LeaveRequest, pk=pk)
        wf_instance = leave_request.workflow_instance
        if wf_instance and not wf_instance.completed_at:
            from apps.workflow.services import record_action
            record_action(wf_instance, request.user, 'return', 'Returned via view')
            messages.success(request, f"Leave request for {leave_request.employee.full_name} has been returned.")
        else:
            messages.error(request, "This request cannot be returned.")
        referer = request.META.get('HTTP_REFERER')
        if referer:
            return redirect(referer)
        return redirect('leave:admin_dashboard')

class AdminEmployeeBalancesView(AdminRequiredMixin, ListView):
    model = EmployeeProfile
    template_name = 'admin_panel/leave/employee_balances.html'
    context_object_name = 'employees'

    def get_queryset(self):
        return EmployeeProfile.objects.filter(is_active=True).prefetch_related('leave_balances', 'leave_rules').order_by('full_name')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        year = self.request.GET.get('year', timezone.localdate().year)
        try:
            year = int(year)
        except ValueError:
            year = timezone.localdate().year

        from .models import get_cached_leave_types
        leave_types = get_cached_leave_types()
        employee_balances = {}

        for emp in context['employees']:
            balances = []
            for lt in leave_types:
                balance = next((b for b in emp.leave_balances.all() if b.leave_type_id == lt.id and b.year == year), None)
                if balance:
                    balances.append({
                        'type': lt,
                        'total': balance.total_days,
                        'used': balance.used_days,
                        'remaining': balance.remaining_days
                    })
                else:
                    rule = next((r for r in emp.leave_rules.all() if r.leave_type_id == lt.id), None)
                    limit = rule.days_per_year if rule else lt.default_days_per_year
                    balances.append({
                        'type': lt,
                        'total': limit,
                        'used': 0,
                        'remaining': limit
                    })
            employee_balances[emp.id] = balances

        context.update({
            'employee_balances': employee_balances,
            'leave_types': leave_types,
            'year': year,
            'available_years': range(timezone.localdate().year - 2, timezone.localdate().year + 3)
        })
        return context

class AdminEmployeeBalanceDetailView(AdminRequiredMixin, DetailView):
    model = EmployeeProfile
    template_name = 'admin_panel/leave/employee_balance_detail.html'
    context_object_name = 'employee'
    pk_url_kwarg = 'employee_id'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        year = self.request.GET.get('year', timezone.localdate().year)
        try:
            year = int(year)
        except ValueError:
            year = timezone.localdate().year

        leave_types = LeaveType.objects.all()
        balances = []
        for lt in leave_types:
            balance = LeaveBalance.objects.filter(employee=self.object, leave_type=lt, year=year).first()
            if balance:
                balances.append(balance)
            else:
                from apps.employees.models import EmployeeLeaveRule
                rule = EmployeeLeaveRule.objects.filter(employee=self.object, leave_type=lt).first()
                limit = rule.days_per_year if rule else lt.default_days_per_year
                balances.append({
                    'leave_type': lt,
                    'total_days': limit,
                    'used_days': 0,
                    'remaining_days': limit
                })

        history = LeaveRequest.objects.filter(employee=self.object).select_related('leave_type').order_by('-requested_at')

        context.update({
            'balances': balances,
            'history': history,
            'year': year,
            'available_years': range(timezone.localdate().year - 2, timezone.localdate().year + 3)
        })
        return context

class AdminLeaveTypesView(AdminRequiredMixin, ListView):
    model = LeaveType
    template_name = 'admin_panel/leave/leave_types.html'
    context_object_name = 'leave_types'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form'] = LeaveTypeForm()
        return context

class AdminLeaveTypeCreateView(AdminRequiredMixin, CreateView):
    model = LeaveType
    form_class = LeaveTypeForm
    template_name = 'admin_panel/leave/leave_type_form.html'
    success_url = reverse_lazy('leave:admin_leave_types')

    def form_valid(self, form):
        messages.success(self.request, f"Leave type '{form.cleaned_data['name']}' created successfully.")
        response = super().form_valid(form)
        next_url = self.request.GET.get('next') or self.request.POST.get('next')
        from django.utils.http import url_has_allowed_host_and_scheme
        if next_url and url_has_allowed_host_and_scheme(url=next_url, allowed_hosts={self.request.get_host()}):
            from django.shortcuts import redirect
            return redirect(next_url)
        return response


class AdminLeaveTypeUpdateView(AdminRequiredMixin, UpdateView):
    model = LeaveType
    form_class = LeaveTypeForm
    template_name = 'admin_panel/leave/leave_type_form.html'
    success_url = reverse_lazy('leave:admin_leave_types')

    def get_template_names(self):
        if self.request.headers.get('HX-Request') == 'true':
            return ['admin_panel/leave/partials/edit_drawer.html']
        return [self.template_name]

    def form_valid(self, form):
        messages.success(self.request, f"Leave type '{form.cleaned_data['name']}' updated successfully.")
        response = super().form_valid(form)
        if self.request.headers.get('HX-Request') == 'true':
            from django.http import HttpResponse
            return HttpResponse('<script>window.location.reload();</script>')
        next_url = self.request.GET.get('next') or self.request.POST.get('next')
        from django.utils.http import url_has_allowed_host_and_scheme
        if next_url and url_has_allowed_host_and_scheme(url=next_url, allowed_hosts={self.request.get_host()}):
            from django.shortcuts import redirect
            return redirect(next_url)
        return response


class AdminLeaveTypeDeleteView(AdminRequiredMixin, View):
    def post(self, request, pk):
        leave_type = get_object_or_404(LeaveType, pk=pk)
        name = leave_type.name
        leave_type.is_active = False
        leave_type.save()
        messages.success(request, f"Leave type '{name}' deactivated successfully.")
        return redirect('leave:admin_leave_types')

# ==============================================================================
# Staff (Employee) Views
# ==============================================================================

class StaffLeaveDashboardView(StaffOrManagerMixin, TemplateView):
    template_name = 'staff/leave/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        employee = _get_profile(self.request.user)
        year = timezone.localdate().year

        if employee:
            leave_types = LeaveType.objects.all()
            balances = []
            for lt in leave_types:
                balance = LeaveBalance.objects.filter(employee=employee, leave_type=lt, year=year).first()
                if balance:
                    balances.append(balance)
                else:
                    from apps.employees.models import EmployeeLeaveRule
                    rule = EmployeeLeaveRule.objects.filter(employee=employee, leave_type=lt).first()
                    limit = rule.days_per_year if rule else lt.default_days_per_year
                    # Provide an object-like wrapper matching LeaveBalance interface
                    class MockBalance:
                        def __init__(self, lt, total):
                            self.leave_type = lt
                            self.total_days = total
                            self.used_days = 0
                            self.remaining_days = total
                    balances.append(MockBalance(lt, limit))

            history = LeaveRequest.objects.filter(employee=employee).select_related('leave_type').order_by('-requested_at')
        else:
            balances = []
            history = []

        context.update({
            'employee': employee,
            'balances': balances,
            'history': history,
            'year': year
        })
        return context

class StaffLeaveRequestCreateView(StaffOrManagerMixin, CreateView):
    model = LeaveRequest
    form_class = LeaveRequestForm
    template_name = 'staff/leave/request_form.html'
    success_url = reverse_lazy('leave:staff_dashboard')

    def dispatch(self, request, *args, **kwargs):
        master = getattr(request.user, 'employee_master', None)
        if not master and hasattr(request.user, 'employee_profile') and request.user.employee_profile.master_employee:
            master = request.user.employee_profile.master_employee
        if master and (master.is_suspended or master.business_status == 'suspended'):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied("Your account is suspended. You cannot submit leave requests.")
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        initial = super().get_initial()
        date_str = self.request.GET.get('date')
        if date_str:
            initial['start_date'] = date_str
            initial['end_date'] = date_str
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['employee'] = _get_profile(self.request.user)
        
        content_type = self.request.content_type or ''
        if 'application/json' in content_type and self.request.method in ('POST', 'PUT'):
            try:
                import json
                kwargs['data'] = json.loads(self.request.body)
            except (json.JSONDecodeError, ValueError):
                pass
        return kwargs

    def post(self, request, *args, **kwargs):
        content_type = request.content_type or ''
        if 'application/json' in content_type:
            try:
                data = json.loads(request.body)
            except (json.JSONDecodeError, ValueError):
                data = request.POST
        else:
            data = request.POST

        sync_uuid = data.get('sync_uuid')
        if sync_uuid:
            existing = LeaveRequest.objects.filter(sync_uuid=sync_uuid).first()
            if existing:
                if 'application/json' in content_type or request.headers.get('x-requested-with') == 'XMLHttpRequest':
                    return JsonResponse({'success': True, 'id': existing.id})
                messages.success(request, "Your leave request has been submitted successfully.")
                return redirect(self.success_url)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        employee = _get_profile(self.request.user)
        if not employee:
            messages.error(self.request, "You do not have an active Employee Profile.")
            return redirect('leave:staff_dashboard')
            
        form.instance.employee = employee
        form.instance.status = 'pending'

        content_type = self.request.content_type or ''
        if 'application/json' in content_type:
            try:
                data = json.loads(self.request.body)
            except (json.JSONDecodeError, ValueError):
                data = self.request.POST
        else:
            data = self.request.POST

        sync_uuid = data.get('sync_uuid')
        if sync_uuid:
            form.instance.sync_uuid = sync_uuid

        client_event_time_str = data.get('client_event_time')
        client_time = parse_and_validate_client_time(client_event_time_str)

        if client_time:
            form.instance.client_event_time = client_time
            form.instance.synced_at = timezone.now()

        response = super().form_valid(form)

        if client_time:
            LeaveRequest.objects.filter(pk=form.instance.pk).update(requested_at=client_time)

        if 'application/json' in content_type or self.request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': True, 'id': form.instance.id})

        messages.success(self.request, "Your leave request has been submitted successfully.")
        return response


from django import forms
from apps.leave.forms import SELECT_INPUT, TEXT_INPUT, TEXTAREA_INPUT

class AdminLeaveRequestRescheduleForm(forms.ModelForm):
    class Meta:
        model = LeaveRequest
        fields = ['leave_type', 'start_date', 'end_date', 'reason']
        widgets = {
            'leave_type': forms.Select(attrs={'class': SELECT_INPUT}),
            'start_date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': TEXT_INPUT}),
            'end_date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': TEXT_INPUT}),
            'reason': forms.Textarea(attrs={'rows': 3, 'class': TEXTAREA_INPUT, 'placeholder': 'Reason for reschedule...'}),
        }

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get('start_date')
        end_date = cleaned_data.get('end_date')

        if start_date and end_date:
            if end_date < start_date:
                raise forms.ValidationError("End date cannot be before start date.")
        return cleaned_data


class RescheduleLeaveRequestView(AdminRequiredMixin, UpdateView):
    model = LeaveRequest
    form_class = AdminLeaveRequestRescheduleForm
    template_name = 'admin_panel/leave/reschedule_form.html'

    def form_valid(self, form):
        form.instance.reviewed_by = self.request.user
        form.instance.reviewed_at = timezone.now()
        
        response = super().form_valid(form)
        
        messages.success(
            self.request,
            f"Leave request for {self.object.employee.full_name} rescheduled to "
            f"{self.object.start_date}–{self.object.end_date}."
        )
        return response

    def get_success_url(self):
        return reverse_lazy('leave:admin_dashboard')


class CancelLeaveRequestView(StaffOrManagerMixin, View):
    def post(self, request, pk):
        leave_request = get_object_or_404(LeaveRequest, pk=pk)
        
        # Verify ownership
        if leave_request.employee.user != request.user:
            messages.error(request, "You do not have permission to cancel this request.")
            return redirect('leave:staff_dashboard')
            
        # Verify status eligibility
        if leave_request.status not in ('pending', 'manager_approved', 'returned'):
            messages.error(request, f"This request cannot be cancelled because it is already {leave_request.status}.")
            return redirect('leave:staff_dashboard')
            
        wf_instance = leave_request.workflow_instance
        if wf_instance:
            if not wf_instance.completed_at:
                from apps.workflow.services import cancel_workflow
                try:
                    cancel_workflow(wf_instance, request.user, 'Cancelled by requester')
                except Exception as e:
                    messages.error(request, f"Failed to cancel workflow: {str(e)}")
                    return redirect('leave:staff_dashboard')
            else:
                messages.error(request, "The workflow has already completed.")
                return redirect('leave:staff_dashboard')
        
        # Transition leave request status to cancelled
        leave_request.status = 'cancelled'
        leave_request.save()
        messages.success(request, "Your leave request has been cancelled.")
        
        return redirect('leave:staff_dashboard')
