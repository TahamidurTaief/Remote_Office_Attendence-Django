from django.shortcuts import render, redirect, get_object_or_404
from django.views.generic import ListView, DetailView, CreateView, UpdateView, View, TemplateView
from django.urls import reverse_lazy
from django.utils import timezone
from django.contrib import messages
from apps.accounts.mixins import AdminRequiredMixin, StaffRequiredMixin, RoleRequiredMixin
from apps.employees.models import EmployeeProfile
from .models import LeaveType, LeaveBalance, LeaveRequest
from .forms import LeaveRequestForm, LeaveTypeForm


class StaffOrManagerMixin(RoleRequiredMixin):
    """Allows both staff and manager roles to access employee-facing views."""
    allowed_roles = ['staff', 'manager']

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
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['pending_count'] = LeaveRequest.objects.filter(status='pending').count()
        context['approved_count'] = LeaveRequest.objects.filter(status='approved').count()
        context['rejected_count'] = LeaveRequest.objects.filter(status='rejected').count()
        context['current_status'] = self.request.GET.get('status', 'all')
        return context

class ApproveLeaveRequestView(AdminRequiredMixin, View):
    def post(self, request, pk):
        return self._process_approval(request, pk)

    def get(self, request, pk):
        return self._process_approval(request, pk)

    def _process_approval(self, request, pk):
        leave_request = get_object_or_404(LeaveRequest, pk=pk)
        if leave_request.status == 'pending':
            leave_request.status = 'approved'
            leave_request.reviewed_by = request.user
            leave_request.reviewed_at = timezone.now()
            leave_request.save()
            messages.success(request, f"Leave request for {leave_request.employee.full_name} has been approved.")
        else:
            messages.error(request, "This request has already been processed.")
        return redirect('leave:admin_dashboard')

class RejectLeaveRequestView(AdminRequiredMixin, View):
    def post(self, request, pk):
        return self._process_approval(request, pk)

    def get(self, request, pk):
        return self._process_approval(request, pk)

    def _process_approval(self, request, pk):
        leave_request = get_object_or_404(LeaveRequest, pk=pk)
        if leave_request.status == 'pending':
            leave_request.status = 'rejected'
            leave_request.reviewed_by = request.user
            leave_request.reviewed_at = timezone.now()
            leave_request.save()
            messages.success(request, f"Leave request for {leave_request.employee.full_name} has been rejected.")
        else:
            messages.error(request, "This request has already been processed.")
        return redirect('leave:admin_dashboard')

class AdminEmployeeBalancesView(AdminRequiredMixin, ListView):
    model = EmployeeProfile
    template_name = 'admin_panel/leave/employee_balances.html'
    context_object_name = 'employees'

    def get_queryset(self):
        return EmployeeProfile.objects.filter(is_active=True).order_by('full_name')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        year = self.request.GET.get('year', timezone.localdate().year)
        try:
            year = int(year)
        except ValueError:
            year = timezone.localdate().year

        leave_types = LeaveType.objects.all()
        employee_balances = {}

        for emp in context['employees']:
            balances = []
            for lt in leave_types:
                balance = LeaveBalance.objects.filter(employee=emp, leave_type=lt, year=year).first()
                if balance:
                    balances.append({
                        'type': lt,
                        'total': balance.total_days,
                        'used': balance.used_days,
                        'remaining': balance.remaining_days
                    })
                else:
                    balances.append({
                        'type': lt,
                        'total': lt.default_days_per_year,
                        'used': 0,
                        'remaining': lt.default_days_per_year
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
                balances.append({
                    'leave_type': lt,
                    'total_days': lt.default_days_per_year,
                    'used_days': 0,
                    'remaining_days': lt.default_days_per_year
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

class AdminLeaveTypeCreateView(AdminRequiredMixin, CreateView):
    model = LeaveType
    form_class = LeaveTypeForm
    template_name = 'admin_panel/leave/leave_type_form.html'
    success_url = reverse_lazy('leave:admin_leave_types')

    def form_valid(self, form):
        messages.success(self.request, f"Leave type '{form.cleaned_data['name']}' created successfully.")
        return super().form_valid(form)

class AdminLeaveTypeUpdateView(AdminRequiredMixin, UpdateView):
    model = LeaveType
    form_class = LeaveTypeForm
    template_name = 'admin_panel/leave/leave_type_form.html'
    success_url = reverse_lazy('leave:admin_leave_types')

    def form_valid(self, form):
        messages.success(self.request, f"Leave type '{form.cleaned_data['name']}' updated successfully.")
        return super().form_valid(form)

# ==============================================================================
# Staff (Employee) Views
# ==============================================================================

class StaffLeaveDashboardView(StaffOrManagerMixin, TemplateView):
    template_name = 'staff/leave/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        employee = getattr(self.request.user, 'employee_profile', None)
        year = timezone.localdate().year

        if employee:
            leave_types = LeaveType.objects.all()
            balances = []
            for lt in leave_types:
                balance = LeaveBalance.objects.filter(employee=employee, leave_type=lt, year=year).first()
                if balance:
                    balances.append(balance)
                else:
                    # Provide an object-like wrapper matching LeaveBalance interface
                    class MockBalance:
                        def __init__(self, lt, total):
                            self.leave_type = lt
                            self.total_days = total
                            self.used_days = 0
                            self.remaining_days = total
                    balances.append(MockBalance(lt, lt.default_days_per_year))

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

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['employee'] = getattr(self.request.user, 'employee_profile', None)
        return kwargs

    def form_valid(self, form):
        employee = getattr(self.request.user, 'employee_profile', None)
        if not employee:
            messages.error(self.request, "You do not have an active Employee Profile.")
            return redirect('leave:staff_dashboard')
            
        form.instance.employee = employee
        form.instance.status = 'pending'
        messages.success(self.request, "Your leave request has been submitted successfully.")
        return super().form_valid(form)
