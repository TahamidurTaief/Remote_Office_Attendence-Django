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
        from apps.accounts.engine import PermissionEngine
        can_manage = self.request.user.is_superuser or PermissionEngine.evaluate(self.request.user, 'expense.approve').allowed or getattr(self.request.user, 'role', '') == 'admin'
        if not can_manage and obj.employee != employee:
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied("You do not have permission to view this expense.")
        return obj

class StaffExpenseCreateView(StaffOrManagerMixin, CreateView):
    model = Expense
    form_class = ExpenseForm
    template_name = 'staff/expense/request_form.html'
    success_url = reverse_lazy('expense:staff_expense_list')

    def dispatch(self, request, *args, **kwargs):
        employee = getattr(request.user, 'employee_profile', None)
        if employee:
            master = getattr(employee, 'master_employee', None)
            if master and (master.is_suspended or master.business_status == 'suspended'):
                from django.core.exceptions import PermissionDenied
                raise PermissionDenied("Your account is suspended. You cannot submit expense requests.")
        return super().dispatch(request, *args, **kwargs)

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

class BaseProcessExpenseView(View):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('/login/')
        from apps.accounts.engine import PermissionEngine
        res = PermissionEngine.evaluate(request.user, 'expense.approve')
        if not res.allowed and not request.user.is_superuser and getattr(request.user, 'role', '') not in ('admin', 'manager'):
            return redirect('/')

        # Scoping check for manager/team scope
        if res.allowed and res.data_scope != 'global' and not request.user.is_superuser:
            from django.shortcuts import get_object_or_404
            from apps.expense.models import Expense
            from apps.employees.models import EmployeeProfile
            
            expense = get_object_or_404(Expense, pk=kwargs.get('pk'))
            profile = getattr(request.user, 'employee_profile', None)
            
            is_reporting_manager = False
            emp_master = getattr(expense.employee, 'master_employee', None)
            if emp_master and emp_master.reporting_manager:
                if emp_master.reporting_manager.user == request.user:
                    is_reporting_manager = True
            
            scoped = is_reporting_manager
            if not scoped and profile:
                if profile.branch and expense.employee.branch == profile.branch:
                    scoped = True
                elif not profile.branch:
                    from django.db.models import Q
                    from apps.projects.models import Project
                    managed_projects = Project.objects.filter(project_manager=profile)
                    project_employees = EmployeeProfile.objects.filter(
                        Q(site_engineer_projects__in=managed_projects) |
                        Q(assigned_tasks__project__in=managed_projects)
                    ).distinct()
                    if expense.employee in project_employees:
                        scoped = True
            if not scoped:
                from django.http import HttpResponseForbidden
                return HttpResponseForbidden("You do not have permission to process expense requests outside your scope.")

        return super().dispatch(request, *args, **kwargs)

class ApproveExpenseView(BaseProcessExpenseView, View):
    def post(self, request, pk):
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
        expense = get_object_or_404(Expense, pk=pk)
        if expense.status == 'pending':
            expense.status = 'rejected'
            expense.reviewed_by = request.user
            expense.reviewed_at = timezone.now()
            expense.rejection_reason = request.POST.get('rejection_reason', '')
            expense.save()
            messages.success(request, f"Expense request for {expense.employee.full_name} has been rejected.")
        else:
            messages.error(request, "This request has already been processed.")
        referer = request.META.get('HTTP_REFERER')
        if referer:
            return redirect(referer)
        return redirect('expense:admin_expense_list')
