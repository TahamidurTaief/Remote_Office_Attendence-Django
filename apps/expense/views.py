import json
import uuid
from django.shortcuts import render, redirect, get_object_or_404
from django.views.generic import ListView, DetailView, CreateView, UpdateView, View
from django.urls import reverse_lazy
from django.utils import timezone
from django.http import JsonResponse
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator

from apps.accounts.mixins import AdminRequiredMixin, StaffRequiredMixin, RoleRequiredMixin
from apps.employees.models import EmployeeProfile
from apps.attendance.sync_utils import parse_and_validate_client_time
from .models import Expense
from .forms import ExpenseForm

class StaffOrManagerMixin(RoleRequiredMixin):
    allowed_roles = ['staff', 'manager', 'admin']

class StaffExpenseListView(StaffOrManagerMixin, ListView):
    model = Expense
    template_name = 'staff/expense/list.html'
    context_object_name = 'expenses'

    def get_queryset(self):
        employee = getattr(self.request.user, 'employee_profile', None)
        if not employee:
            return Expense.objects.none()
        return Expense.objects.filter(employee=employee)

class ExpenseDetailView(StaffOrManagerMixin, DetailView):
    model = Expense
    template_name = 'staff/expense/detail.html'
    context_object_name = 'expense'

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        employee = getattr(self.request.user, 'employee_profile', None)
        if self.request.user.role != 'admin' and obj.employee != employee:
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied("You do not have permission to view this expense.")
        return obj

class StaffExpenseCreateView(StaffOrManagerMixin, CreateView):
    model = Expense
    form_class = ExpenseForm
    template_name = 'staff/expense/request_form.html'
    success_url = reverse_lazy('expense:staff_expense_list')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
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
            existing = Expense.objects.filter(sync_uuid=sync_uuid).first()
            if existing:
                if 'application/json' in content_type or request.headers.get('x-requested-with') == 'XMLHttpRequest':
                    return JsonResponse({'success': True, 'id': existing.id})
                messages.success(request, "Your expense request has been submitted successfully.")
                return redirect(self.success_url)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        employee = getattr(self.request.user, 'employee_profile', None)
        if not employee:
            messages.error(self.request, "You do not have an active Employee Profile.")
            return redirect('expense:staff_expense_list')
            
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
            Expense.objects.filter(pk=form.instance.pk).update(requested_at=client_time)

        if 'application/json' in content_type or self.request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': True, 'id': form.instance.id})

        messages.success(self.request, "Your expense request has been submitted successfully.")
        return response

class AdminExpenseListView(AdminRequiredMixin, ListView):
    model = Expense
    template_name = 'admin_panel/expense/list.html'
    context_object_name = 'expenses'

    def get_queryset(self):
        qs = Expense.objects.all().select_related('employee', 'project', 'reviewed_by')
        status = self.request.GET.get('status')
        if status in ['pending', 'approved', 'rejected']:
            qs = qs.filter(status=status)
        return qs

class BaseProcessExpenseView(RoleRequiredMixin):
    allowed_roles = ['admin', 'manager']

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if request.user.role not in self.allowed_roles:
            return redirect('/')
        return super().dispatch(request, *args, **kwargs)

class ApproveExpenseView(BaseProcessExpenseView, View):
    def post(self, request, pk):
        return self._process_approval(request, pk)

    def get(self, request, pk):
        return self._process_approval(request, pk)

    def _process_approval(self, request, pk):
        expense = get_object_or_404(Expense, pk=pk)
        if expense.status == 'pending':
            expense.status = 'approved'
            expense.reviewed_by = request.user
            expense.reviewed_at = timezone.now()
            expense.save()
            messages.success(request, f"Expense request for {expense.employee.full_name} has been approved.")
        else:
            messages.error(request, "This request has already been processed.")
        referer = request.META.get('HTTP_REFERER')
        if referer:
            return redirect(referer)
        return redirect('expense:admin_expense_list')

class RejectExpenseView(BaseProcessExpenseView, View):
    def post(self, request, pk):
        return self._process_approval(request, pk)

    def get(self, request, pk):
        return self._process_approval(request, pk)

    def _process_approval(self, request, pk):
        expense = get_object_or_404(Expense, pk=pk)
        if expense.status == 'pending':
            expense.status = 'rejected'
            expense.reviewed_by = request.user
            expense.reviewed_at = timezone.now()
            expense.rejection_reason = request.POST.get('rejection_reason', '') or request.GET.get('rejection_reason', '')
            expense.save()
            messages.success(request, f"Expense request for {expense.employee.full_name} has been rejected.")
        else:
            messages.error(request, "This request has already been processed.")
        referer = request.META.get('HTTP_REFERER')
        if referer:
            return redirect(referer)
        return redirect('expense:admin_expense_list')
