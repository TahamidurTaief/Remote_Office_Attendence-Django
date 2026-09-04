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
    allowed_roles = ['staff', 'manager', 'admin']

class StaffExpenseListView(StaffOrManagerMixin, ListView):
    model = Expense
    template_name = 'staff/expense/list.html'
    context_object_name = 'expenses'

    def get_queryset(self):
        employee = _get_profile(self.request.user)
        if not employee:
            return Expense.objects.none()
        return Expense.objects.filter(employee=employee)

class ExpenseDetailView(StaffOrManagerMixin, DetailView):
    model = Expense
    template_name = 'staff/expense/detail.html'
    context_object_name = 'expense'

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        employee = _get_profile(self.request.user)
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
        employee = _get_profile(request.user)
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
        employee = _get_profile(self.request.user)
        if not employee:
            messages.error(self.request, "You do not have an active Employee Profile.")
            return redirect('expense:staff_expense_list')
            
        form.instance.employee = employee

        content_type = self.request.content_type or ''
        if 'application/json' in content_type:
            try:
                data = json.loads(self.request.body)
            except (json.JSONDecodeError, ValueError):
                data = self.request.POST
        else:
            data = self.request.POST

        action = data.get('action', 'submit')
        if action == 'draft':
            form.instance.status = 'draft'
        else:
            form.instance.status = 'pending_manager'

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
        if status in ['pending_manager', 'pending_finance', 'pending_accounts', 'approved', 'rejected']:
            qs = qs.filter(status=status)
        elif status == 'pending':
            qs = qs.filter(status__in=['pending_manager', 'pending_finance', 'pending_accounts'])
        elif not status:
            # By default, show all
            pass
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from .models import ExpenseCategory
        from apps.projects.models import Project
        context['categories'] = ExpenseCategory.objects.filter(is_active=True)
        context['projects'] = Project.objects.all()
        base_qs = Expense.objects.all()
        context['all_count'] = base_qs.count()
        context['pending_count'] = base_qs.filter(status__in=['pending_manager', 'pending_finance', 'pending_accounts']).count()
        context['approved_count'] = base_qs.filter(status='approved').count()
        context['rejected_count'] = base_qs.filter(status='rejected').count()
        return context


@login_required
def expense_detail_api(request, pk):
    import os
    expense = get_object_or_404(
        Expense.objects.select_related('employee', 'project', 'category', 'reviewed_by'),
        pk=pk
    )
    from apps.accounts.engine import PermissionEngine
    can_manage = (
        request.user.is_superuser
        or PermissionEngine.evaluate(request.user, 'expense.approve').allowed
        or getattr(request.user, 'role', '') in ('admin', 'manager', 'finance', 'accounts')
    )
    employee = _get_profile(request.user)
    if not can_manage and expense.employee != employee:
        return JsonResponse({'error': 'Permission denied'}, status=403)

    return_events_data = []
    for ev in expense.return_events.select_related('returned_by').order_by('-created_at'):
        return_events_data.append({
            'id': ev.id,
            'returned_by': ev.returned_by.email if ev.returned_by else 'Reviewer',
            'reason': ev.reason,
            'returned_from_status': ev.returned_from_status,
            'fields_to_correct': ev.fields_to_correct or [],
            'due_date': ev.due_date.strftime('%d %b %Y') if ev.due_date else None,
            'created_at': ev.created_at.strftime('%d %b %Y, %I:%M %p') if ev.created_at else '',
            'attachment_url': ev.attachment.url if ev.attachment else None,
        })

    history_data = []
    for h in expense.history.select_related('updated_by', 'category').order_by('-changed_at')[:10]:
        history_data.append({
            'id': h.id,
            'updated_by': h.updated_by.email if h.updated_by else 'System',
            'amount': f"{h.amount:.2f}",
            'category': h.category.name if h.category else 'Uncategorized',
            'description': h.description,
            'changed_at': h.changed_at.strftime('%d %b %Y, %I:%M %p') if h.changed_at else '',
        })

    emp = expense.employee
    dept = getattr(emp, 'department', None)
    dept_name = dept if isinstance(dept, str) else (str(dept.name) if hasattr(dept, 'name') and not callable(getattr(dept, 'name')) else str(dept or ''))
    desig = getattr(emp, 'designation', None)
    desig_title = desig if isinstance(desig, str) else (str(desig.title) if hasattr(desig, 'title') and not callable(getattr(desig, 'title')) else str(desig or ''))

    attachment_data = None
    if expense.attachment:
        name = os.path.basename(expense.attachment.name)
        ext = os.path.splitext(name)[1].lower()
        attachment_data = {
            'url': expense.attachment.url,
            'name': name,
            'is_image': ext in ['.jpg', '.jpeg', '.png', '.webp'],
            'is_pdf': ext == '.pdf',
        }

    return JsonResponse({
        'id': expense.id,
        'employee': {
            'id': emp.id,
            'name': emp.full_name,
            'employee_id': emp.employee_id,
            'photo': emp.profile_photo.url if emp.profile_photo else None,
            'department': dept_name,
            'designation': desig_title,
        },
        'amount': f"{expense.amount:.2f}",
        'category': {
            'id': expense.category.id if expense.category else None,
            'name': expense.category.name if expense.category else 'Uncategorized',
            'code': expense.category.code if expense.category else '',
        },
        'project': {
            'id': expense.project.id if expense.project else None,
            'name': expense.project.name if expense.project else 'None',
        } if expense.project else None,
        'description': expense.description or '',
        'status': expense.status,
        'status_display': expense.get_status_display(),
        'requested_at': expense.requested_at.strftime('%d %b %Y, %I:%M %p') if expense.requested_at else '',
        'reviewed_by': expense.reviewed_by.email if expense.reviewed_by else None,
        'reviewed_at': expense.reviewed_at.strftime('%d %b %Y, %I:%M %p') if expense.reviewed_at else None,
        'rejection_reason': expense.rejection_reason or '',
        'attachment': attachment_data,
        'can_approve': expense.status in ('pending_manager', 'pending_finance', 'pending_accounts'),
        'can_return': expense.status in ('pending_manager', 'pending_finance'),
        'can_reject': expense.status in ('pending_manager', 'pending_finance', 'pending_accounts'),
        'return_events': return_events_data,
        'history': history_data,
    })


class AdminExpenseUpdateView(AdminRequiredMixin, View):
    def post(self, request, pk):
        from django.db import transaction
        from .models import ExpenseHistory, ExpenseCategory
        from apps.projects.models import Project

        expense = get_object_or_404(Expense, pk=pk)

        content_type = request.content_type or ''
        if 'application/json' in content_type:
            try:
                data = json.loads(request.body)
            except (json.JSONDecodeError, ValueError):
                data = request.POST
        else:
            data = request.POST

        amount = data.get('amount')
        category_id = data.get('category')
        project_id = data.get('project')
        description = data.get('description')
        status = data.get('status')

        with transaction.atomic():
            ExpenseHistory.objects.create(
                expense=expense,
                updated_by=request.user,
                amount=expense.amount,
                category=expense.category,
                description=expense.description,
                attachment=expense.attachment
            )

            if amount:
                try:
                    expense.amount = float(amount)
                except (ValueError, TypeError):
                    pass

            if category_id:
                cat = ExpenseCategory.objects.filter(pk=category_id).first()
                if cat:
                    expense.category = cat
            elif 'category' in data and not category_id:
                expense.category = None

            if project_id:
                proj = Project.objects.filter(pk=project_id).first()
                if proj:
                    expense.project = proj
            elif 'project' in data and not project_id:
                expense.project = None

            if description is not None:
                expense.description = description.strip()

            if status and status in dict(Expense.STATUS_CHOICES):
                expense.status = status

            expense.save()

        if 'application/json' in content_type or request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': True, 'id': expense.id})

        messages.success(request, f"Expense claim #{expense.id} updated successfully.")
        referer = request.META.get('HTTP_REFERER')
        if referer:
            return redirect(referer)
        return redirect('expense:admin_expense_list')


class BaseProcessExpenseView(View):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('/login/')
        
        from django.db import transaction
        from apps.expense.models import Expense
        
        with transaction.atomic():
            expense = get_object_or_404(Expense.objects.select_for_update(), pk=kwargs.get('pk'))
            user = request.user
            
            # Self-approval restriction
            if expense.employee.user == user:
                from django.http import HttpResponseForbidden
                return HttpResponseForbidden("You cannot approve or process your own expense claim.")
            
            # Superuser / Admin bypass
            if user.is_superuser or getattr(user, 'role', '') == 'admin':
                return super().dispatch(request, *args, **kwargs)
                
            if expense.status == 'pending_manager':
                # Needs to be reporting manager of the employee or their delegate
                emp_master = getattr(expense.employee, 'master_employee', None)
                if not emp_master:
                    if getattr(user, 'role', '') == 'manager':
                        return super().dispatch(request, *args, **kwargs)
                    from django.http import HttpResponseForbidden
                    return HttpResponseForbidden("No manager link or profile found to evaluate.")
                
                reporting_manager = emp_master.reporting_manager
                if not reporting_manager:
                    if getattr(user, 'role', '') == 'manager':
                        return super().dispatch(request, *args, **kwargs)
                    from django.http import HttpResponseForbidden
                    return HttpResponseForbidden("This employee has no reporting manager assigned.")
                
                # Check direct manager
                is_allowed = False
                if reporting_manager.user == user:
                    is_allowed = True
                
                # Check hierarchy manager (any level)
                from apps.employees.hierarchy_services import OrgHierarchyService
                reviewer_emp = getattr(user, 'employee_master', None)
                if not is_allowed and reviewer_emp:
                    if OrgHierarchyService.is_manager_of(reviewer_emp, emp_master):
                        is_allowed = True
                
                # Check manager delegation
                if not is_allowed and reviewer_emp:
                    from apps.employees.models import ManagerDelegation
                    from django.utils import timezone
                    today = timezone.localdate()
                    managers = [reporting_manager]
                    managers.extend(OrgHierarchyService.get_management_chain(emp_master))
                    active_delegations = ManagerDelegation.objects.filter(
                        manager__in=managers,
                        delegate_to=reviewer_emp,
                        is_active=True,
                        start_date__lte=today,
                        end_date__gte=today
                    )
                    if active_delegations.exists():
                        is_allowed = True
                
                if not is_allowed:
                    from django.http import HttpResponseForbidden
                    return HttpResponseForbidden("You are not authorized to approve this expense at the Manager stage.")
                    
            elif expense.status == 'pending_finance':
                from apps.accounts.engine import PermissionEngine
                res = PermissionEngine.evaluate(user, 'expense.approve')
                if getattr(user, 'role', '') != 'finance' and not res.allowed:
                    from django.http import HttpResponseForbidden
                    return HttpResponseForbidden("You do not have Finance permissions to process this expense.")
                    
            elif expense.status == 'pending_accounts':
                if getattr(user, 'role', '') != 'accounts':
                    from django.http import HttpResponseForbidden
                    return HttpResponseForbidden("You do not have Accounts permissions to process this expense.")
                    
            else:
                from django.http import HttpResponseForbidden
                return HttpResponseForbidden("This expense is already processed or in an invalid state.")
                
            return super().dispatch(request, *args, **kwargs)

class ApproveExpenseView(BaseProcessExpenseView, View):
    def post(self, request, pk):
        expense = get_object_or_404(Expense, pk=pk)
        from apps.notifications.dispatch import log_activity
        
        wf_instance = expense.workflow_instance
        if wf_instance and not wf_instance.completed_at:
            from apps.workflow.services import record_action
            old_status = expense.status
            record_action(wf_instance, request.user, 'approve', f"Approved via view")
            
            if old_status == 'pending_manager':
                verb = 'expense_approved_manager'
                title = 'Expense Approved by Manager'
                msg = f"Expense request approved by Manager {request.user.email} and forwarded to Finance."
            elif old_status == 'pending_finance':
                verb = 'expense_approved_finance'
                title = 'Expense Approved by Finance'
                msg = f"Expense request approved by Finance {request.user.email} and forwarded to Accounts."
            elif old_status == 'pending_accounts':
                verb = 'expense_fully_approved'
                title = 'Expense Fully Approved'
                msg = f"Expense request has been fully approved/disbursed by Accounts ({request.user.email})."
            else:
                verb = 'expense_approved'
                title = 'Expense Approved'
                msg = f"Expense request approved by {request.user.email}."
                
            log_activity(
                actor=request.user,
                verb=verb,
                target=expense,
                metadata={
                    'title': title,
                    'message': msg,
                    'notif_type': 'expense'
                },
                notify_users=[expense.employee.user]
            )
            messages.success(request, f"Expense request for {expense.employee.full_name} has been processed.")
        else:
            if expense.status == 'pending_manager':
                expense.status = 'pending_finance'
                expense.save()
                messages.success(request, f"Expense request for {expense.employee.full_name} has been approved by Manager and sent to Finance.")
                log_activity(
                    actor=request.user,
                    verb='expense_approved_manager',
                    target=expense,
                    metadata={
                        'title': 'Expense Approved by Manager',
                        'message': f"Expense request approved by Manager {request.user.email} and forwarded to Finance.",
                        'notif_type': 'expense'
                    },
                    notify_users=[expense.employee.user]
                )
            elif expense.status == 'pending_finance':
                expense.status = 'pending_accounts'
                expense.save()
                messages.success(request, f"Expense request for {expense.employee.full_name} has been approved by Finance and sent to Accounts.")
                log_activity(
                    actor=request.user,
                    verb='expense_approved_finance',
                    target=expense,
                    metadata={
                        'title': 'Expense Approved by Finance',
                        'message': f"Expense request approved by Finance {request.user.email} and forwarded to Accounts.",
                        'notif_type': 'expense'
                    },
                    notify_users=[expense.employee.user]
                )
            elif expense.status == 'pending_accounts':
                expense.status = 'approved'
                expense.reviewed_by = request.user
                expense.reviewed_at = timezone.now()
                expense.save()
                messages.success(request, f"Expense request for {expense.employee.full_name} has been fully approved.")
                log_activity(
                    actor=request.user,
                    verb='expense_fully_approved',
                    target=expense,
                    metadata={
                        'title': 'Expense Fully Approved',
                        'message': f"Expense request has been fully approved/disbursed by Accounts ({request.user.email}).",
                        'notif_type': 'expense'
                    },
                    notify_users=[expense.employee.user]
                )
            else:
                messages.error(request, "This request has already been processed.")
            
        referer = request.META.get('HTTP_REFERER')
        if referer:
            return redirect(referer)
        return redirect('expense:admin_expense_list')

class RejectExpenseView(BaseProcessExpenseView, View):
    def post(self, request, pk):
        expense = get_object_or_404(Expense, pk=pk)
        from apps.notifications.dispatch import log_activity
        
        wf_instance = expense.workflow_instance
        if wf_instance and not wf_instance.completed_at:
            from apps.workflow.services import record_action
            old_status = expense.status
            reason = request.POST.get('rejection_reason', '')
            expense.rejection_reason = reason
            expense.save()
            
            record_action(wf_instance, request.user, 'reject', f"Rejected via view: {reason}")
            
            messages.success(request, f"Expense request for {expense.employee.full_name} has been rejected.")
            log_activity(
                actor=request.user,
                verb='expense_rejected',
                target=expense,
                metadata={
                    'title': 'Expense Rejected',
                    'message': f"Expense request rejected at {old_status} stage by {request.user.email}. Reason: {reason}",
                    'notif_type': 'expense'
                },
                notify_users=[expense.employee.user]
            )
        else:
            if expense.status in ('pending_manager', 'pending_finance', 'pending_accounts'):
                old_status = expense.status
                expense.status = 'rejected'
                expense.reviewed_by = request.user
                expense.reviewed_at = timezone.now()
                expense.rejection_reason = request.POST.get('rejection_reason', '')
                expense.save()
                
                messages.success(request, f"Expense request for {expense.employee.full_name} has been rejected.")
                log_activity(
                    actor=request.user,
                    verb='expense_rejected',
                    target=expense,
                    metadata={
                        'title': 'Expense Rejected',
                        'message': f"Expense request rejected at {old_status} stage by {request.user.email}. Reason: {expense.rejection_reason}",
                        'notif_type': 'expense'
                    },
                    notify_users=[expense.employee.user]
                )
            else:
                messages.error(request, "This request has already been processed.")
            
        referer = request.META.get('HTTP_REFERER')
        if referer:
            return redirect(referer)
        return redirect('expense:admin_expense_list')

class ReturnExpenseView(BaseProcessExpenseView, View):
    def post(self, request, pk):
        from django.db import transaction
        expense = get_object_or_404(Expense, pk=pk)
        
        reason = request.POST.get('reason')
        if not reason:
            messages.error(request, "Reason is required to return an expense.")
            referer = request.META.get('HTTP_REFERER')
            if referer:
                return redirect(referer)
            return redirect('expense:admin_expense_list')
            
        fields_to_correct = request.POST.getlist('fields_to_correct')
        due_date_str = request.POST.get('due_date')
        due_date = None
        if due_date_str:
            from datetime import datetime
            try:
                due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()
            except ValueError:
                pass
                
        attachment = request.FILES.get('attachment')
        
        wf_instance = expense.workflow_instance
        if wf_instance and not wf_instance.completed_at:
            from apps.workflow.services import record_action
            from apps.notifications.dispatch import log_activity
            old_status = expense.status
            
            with transaction.atomic():
                record_action(wf_instance, request.user, 'return', f"Returned: {reason}", return_to_initiator=True)
                
                from .models import ExpenseReturnEvent
                ExpenseReturnEvent.objects.create(
                    expense=expense,
                    returned_by=request.user,
                    returned_from_status=old_status,
                    reason=reason,
                    fields_to_correct=fields_to_correct,
                    due_date=due_date,
                    attachment=attachment
                )
                
                messages.success(request, f"Expense request for {expense.employee.full_name} has been returned.")
                log_activity(
                    actor=request.user,
                    verb='expense_returned',
                    target=expense,
                    metadata={
                        'title': 'Expense Returned',
                        'message': f"Expense request returned at {old_status} stage by {request.user.email}. Reason: {reason}",
                        'notif_type': 'expense'
                    },
                    notify_users=[expense.employee.user]
                )
        else:
            with transaction.atomic():
                old_status = expense.status
                if old_status == 'pending_manager':
                    expense.status = 'returned_by_manager'
                elif old_status == 'pending_finance':
                    expense.status = 'returned_by_finance'
                else:
                    messages.error(request, "Only Manager or Finance stages can return expenses.")
                    return redirect('expense:admin_expense_list')
                    
                expense.save()
                
                from .models import ExpenseReturnEvent
                ExpenseReturnEvent.objects.create(
                    expense=expense,
                    returned_by=request.user,
                    returned_from_status=old_status,
                    reason=reason,
                    fields_to_correct=fields_to_correct,
                    due_date=due_date,
                    attachment=attachment
                )
                
                from apps.notifications.dispatch import log_activity
                messages.success(request, f"Expense request for {expense.employee.full_name} has been returned.")
                log_activity(
                    actor=request.user,
                    verb='expense_returned',
                    target=expense,
                    metadata={
                        'title': 'Expense Returned',
                        'message': f"Expense request returned at {old_status} stage by {request.user.email}. Reason: {reason}",
                        'notif_type': 'expense'
                    },
                    notify_users=[expense.employee.user]
                )
            
        referer = request.META.get('HTTP_REFERER')
        if referer:
            return redirect(referer)
        return redirect('expense:admin_expense_list')

class SubmitExpenseDraftView(StaffOrManagerMixin, View):
    def post(self, request, pk):
        expense = get_object_or_404(Expense, pk=pk)
        employee = _get_profile(request.user)
        if expense.employee != employee:
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied("You do not have permission to submit this draft.")
        if expense.status not in ('draft', 'returned', 'returned_by_manager', 'returned_by_finance'):
            messages.error(request, "Only drafts or returned expenses can be submitted.")
            return redirect('expense:staff_expense_list')
        
        if expense.status == 'returned_by_finance':
            expense.status = 'pending_finance'
        else:
            expense.status = 'pending_manager'
            
        expense.save()
        messages.success(request, "Expense submitted successfully.")
        return redirect('expense:staff_expense_list')

class StaffExpenseUpdateView(StaffOrManagerMixin, UpdateView):
    model = Expense
    form_class = ExpenseForm
    template_name = 'staff/expense/request_form.html'
    success_url = reverse_lazy('expense:staff_expense_list')

    def dispatch(self, request, *args, **kwargs):
        expense = self.get_object()
        employee = _get_profile(request.user)
        if expense.employee != employee:
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied("You do not have permission to edit this expense.")
        if expense.status not in ('draft', 'returned', 'returned_by_manager', 'returned_by_finance'):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied("You can only edit draft or returned expenses.")
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

    def form_valid(self, form):
        from django.db import transaction
        from .models import ExpenseHistory
        expense = self.get_object()
        
        with transaction.atomic():
            ExpenseHistory.objects.create(
                expense=expense,
                updated_by=self.request.user,
                amount=expense.amount,
                category=expense.category,
                description=expense.description,
                attachment=expense.attachment
            )
            
            content_type = self.request.content_type or ''
            if 'application/json' in content_type:
                try:
                    data = json.loads(self.request.body)
                except (json.JSONDecodeError, ValueError):
                    data = self.request.POST
            else:
                data = self.request.POST

            action = data.get('action', 'submit')
            if action == 'draft':
                # keep status unchanged (or draft if it was draft)
                pass
            else:
                if expense.status == 'returned_by_finance':
                    form.instance.status = 'pending_finance'
                else:
                    form.instance.status = 'pending_manager'
            
            response = super().form_valid(form)
            
        messages.success(self.request, "Expense updated and resubmitted successfully.")
        return response

