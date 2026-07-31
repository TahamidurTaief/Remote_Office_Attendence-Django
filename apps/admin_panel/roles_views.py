from django.contrib.auth import get_user_model
from django.views.generic import ListView, CreateView, UpdateView, DeleteView, DetailView, View
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.urls import reverse_lazy
from django.template.defaulttags import register

from apps.accounts.mixins import AdminRequiredMixin, RBACPermissionRequiredMixin
from apps.accounts.models import (
    Role, Module, Action, Permission, RolePermission,
    UserRoleAssignment, UserPermissionOverride, DataScope
)
from django.utils.decorators import method_decorator
from apps.accounts.decorators import require_reauth
from apps.employees.models import EmployeeProfile

User = get_user_model()


@register.filter
def get_item(dictionary, key):
    if isinstance(dictionary, dict):
        return dictionary.get(key)
    return None


class DynamicRoleListView(AdminRequiredMixin, ListView):
    model = Role
    template_name = 'admin_panel/roles/role_list.html'
    context_object_name = 'roles'

    def get_queryset(self):
        return Role.objects.prefetch_related(
            'user_assignments',
            'role_permissions',
            'role_permissions__permission'
        ).order_by('-is_system_protected', 'name')


@method_decorator(require_reauth, name='dispatch')
class DynamicRoleCreateView(AdminRequiredMixin, CreateView):
    model = Role
    template_name = 'admin_panel/roles/role_form.html'
    fields = ['name', 'code', 'description', 'is_active']
    success_url = reverse_lazy('admin_panel:role_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f"Role '{self.object.name}' created successfully.")
        return response


@method_decorator(require_reauth, name='dispatch')
class DynamicRoleUpdateView(AdminRequiredMixin, UpdateView):
    model = Role
    template_name = 'admin_panel/roles/role_form.html'
    fields = ['name', 'code', 'description', 'is_active']
    success_url = reverse_lazy('admin_panel:role_list')

    def form_valid(self, form):
        if self.object.is_system_protected and form.cleaned_data.get('code') != self.object.code:
            messages.error(self.request, "System Owner protected role code cannot be changed.")
            return self.form_invalid(form)

        response = super().form_valid(form)
        messages.success(self.request, f"Role '{self.object.name}' updated successfully.")
        return response


@method_decorator(require_reauth, name='dispatch')
class DynamicRoleDeleteView(AdminRequiredMixin, DeleteView):
    model = Role
    success_url = reverse_lazy('admin_panel:role_list')

    def post(self, request, *args, **kwargs):
        role = self.get_object()
        if role.is_system_protected:
            messages.error(request, "Protected System Owner role cannot be deleted.")
            return redirect(self.success_url)

        role_name = role.name
        role.delete()
        messages.success(request, f"Role '{role_name}' deleted successfully.")
        return redirect(self.success_url)


class DynamicRoleMatrixView(AdminRequiredMixin, DetailView):
    model = Role
    template_name = 'admin_panel/roles/role_matrix.html'
    context_object_name = 'role'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        role = self.object

        modules = Module.objects.filter(is_active=True).prefetch_related(
            'permissions', 'permissions__action'
        ).order_by('sort_order', 'name')

        modules_with_perms = []
        for mod in modules:
            modules_with_perms.append({
                'module': mod,
                'permissions': mod.permissions.select_related('action').order_by('action__name')
            })

        role_perms = RolePermission.objects.filter(role=role).select_related('permission')
        role_perm_ids = set(role_perms.values_list('permission_id', flat=True))
        perm_scope_map = {rp.permission_id: rp.data_scope for rp in role_perms}

        context['modules_with_perms'] = modules_with_perms
        context['role_perm_ids'] = role_perm_ids
        context['perm_scope_map'] = perm_scope_map
        context['total_permissions_count'] = Permission.objects.count()
        context['data_scope_choices'] = DataScope.choices
        return context


@method_decorator(require_reauth, name='post')
class RoleMembersView(AdminRequiredMixin, View):
    def get(self, request, pk):
        role = get_object_or_404(Role, pk=pk)
        members = User.objects.filter(
            role_assignments__role=role
        ).select_related('employee_profile', 'employee_profile__branch').order_by('email')

        non_members = EmployeeProfile.objects.filter(
            is_active=True,
            user__is_active=True
        ).exclude(
            user__role_assignments__role=role
        ).select_related('user').order_by('full_name')

        context = {
            'role': role,
            'members': members,
            'non_members': non_members,
        }
        return render(request, 'admin_panel/roles/role_members.html', context)

    def post(self, request, pk):
        role = get_object_or_404(Role, pk=pk)
        action = request.POST.get('action')

        if action == 'add':
            user_ids = request.POST.getlist('user_ids')
            added_count = 0
            if user_ids:
                users_to_add = User.objects.filter(pk__in=user_ids)
                for user in users_to_add:
                    _, created = UserRoleAssignment.objects.get_or_create(
                        user=user,
                        role=role,
                        defaults={'assigned_by': request.user}
                    )
                    if created:
                        added_count += 1
            messages.success(request, f"Successfully added {added_count} user(s) to role '{role.name}'.")

        elif action == 'remove':
            user_id = request.POST.get('user_id')
            if user_id:
                deleted_count, _ = UserRoleAssignment.objects.filter(
                    role=role,
                    user_id=user_id
                ).delete()
                if deleted_count > 0:
                    messages.info(request, f"Removed user from role '{role.name}'.")

        return redirect('admin_panel:role_members', pk=role.pk)


@method_decorator(require_reauth, name='dispatch')
class RolePermissionToggleView(AdminRequiredMixin, View):
    def post(self, request, role_id, perm_id):
        role = get_object_or_404(Role, pk=role_id)
        perm = get_object_or_404(Permission, pk=perm_id)

        existing = RolePermission.objects.filter(role=role, permission=perm).first()
        if existing:
            existing.delete()
            granted = False
        else:
            RolePermission.objects.create(role=role, permission=perm, data_scope=DataScope.GLOBAL)
            granted = True

        return JsonResponse({'status': 'ok', 'granted': granted, 'role_id': role_id, 'perm_id': perm_id})


@method_decorator(require_reauth, name='dispatch')
class RolePermissionScopeView(AdminRequiredMixin, View):
    def post(self, request, role_id, perm_id):
        role = get_object_or_404(Role, pk=role_id)
        perm = get_object_or_404(Permission, pk=perm_id)
        new_scope = request.POST.get('data_scope', DataScope.GLOBAL)

        rp, created = RolePermission.objects.get_or_create(role=role, permission=perm)
        rp.data_scope = new_scope
        rp.save()

        return JsonResponse({'status': 'ok', 'scope': new_scope})


@method_decorator(require_reauth, name='post')
class UserPermissionsView(AdminRequiredMixin, DetailView):
    model = User
    template_name = 'admin_panel/roles/user_permissions.html'
    context_object_name = 'target_user'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        target = self.object

        context['all_roles'] = Role.objects.filter(is_active=True).order_by('name')
        context['assigned_role_ids'] = set(
            UserRoleAssignment.objects.filter(user=target).values_list('role_id', flat=True)
        )
        context['overrides'] = UserPermissionOverride.objects.filter(user=target).select_related('permission', 'permission__module', 'permission__action')
        context['all_permissions'] = Permission.objects.select_related('module', 'action').order_by('module__name', 'name')
        context['data_scope_choices'] = DataScope.choices
        return context

    def post(self, request, pk):
        target = get_object_or_404(User, pk=pk)
        role_ids = request.POST.getlist('role_ids')

        # Update user roles
        UserRoleAssignment.objects.filter(user=target).delete()
        for r_id in role_ids:
            try:
                r_obj = Role.objects.get(pk=r_id)
                UserRoleAssignment.objects.create(user=target, role=r_obj, assigned_by=request.user)
            except Role.DoesNotExist:
                pass

        messages.success(request, f"Updated role assignments for {target.email}.")
        return redirect('admin_panel:user_permissions', pk=target.pk)


@method_decorator(require_reauth, name='dispatch')
class UserPermissionOverrideSaveView(AdminRequiredMixin, View):
    def post(self, request, pk):
        target = get_object_or_404(User, pk=pk)
        perm_id = request.POST.get('permission_id')
        is_granted = request.POST.get('is_granted') == 'true'
        data_scope = request.POST.get('data_scope') or None

        perm = get_object_or_404(Permission, pk=perm_id)

        override, _ = UserPermissionOverride.objects.get_or_create(user=target, permission=perm)
        override.is_granted = is_granted
        override.data_scope = data_scope
        override.save()

        messages.success(request, f"Permission override for '{perm.codename}' saved.")
        return redirect('admin_panel:user_permissions', pk=target.pk)


from django.db.models import Q
from apps.notifications.models import AuditLog


class AdminAuditLogView(AdminRequiredMixin, View):
    def get(self, request):
        action_filter = request.GET.get('action', '').strip()
        search_query = request.GET.get('q', '').strip()

        logs = AuditLog.objects.select_related('actor').order_by('-timestamp')

        if action_filter:
            logs = logs.filter(action=action_filter)
        if search_query:
            logs = logs.filter(
                Q(actor__email__icontains=search_query) |
                Q(actor__phone__icontains=search_query) |
                Q(summary__icontains=search_query) |
                Q(action__icontains=search_query)
            )

        action_types = AuditLog.objects.values_list('action', flat=True).distinct()

        context = {
            'logs': logs[:150],
            'action_filter': action_filter,
            'search_query': search_query,
            'action_types': action_types
        }

        if request.headers.get('HX-Request') == 'true':
            return render(request, 'admin_panel/audit/audit_log_list_partial.html', context)
        return render(request, 'admin_panel/audit/audit_log_list.html', context)

    def post(self, request):
        ids = request.POST.getlist('ids') or request.POST.get('ids', '').split(',')
        ids = [i for i in ids if str(i).isdigit()]
        if ids:
            from apps.notifications.models import log_audit
            deleted_count, _ = AuditLog.objects.filter(id__in=ids).delete()
            log_audit(request.user, 'bulk_audit_log_delete', summary=f"Bulk deleted {deleted_count} AuditLog entries", ip=request.META.get('REMOTE_ADDR'))
            messages.success(request, f"Successfully deleted {deleted_count} audit log entries.")

        logs = AuditLog.objects.select_related('actor').order_by('-timestamp')[:150]
        action_types = AuditLog.objects.values_list('action', flat=True).distinct()
        context = {'logs': logs, 'action_types': action_types}
        if request.headers.get('HX-Request') == 'true':
            return render(request, 'admin_panel/audit/audit_log_list_partial.html', context)
        return redirect('admin_panel:admin_audit_logs')


from datetime import timedelta
from django.utils import timezone
from apps.accounts.models import UserSession, UserLoginActivity, LoginProtection


class AdminSecurityDashboardView(AdminRequiredMixin, View):
    def get(self, request):
        now = timezone.now()
        since_24h = now - timedelta(hours=24)

        active_sessions_count = UserSession.objects.filter(is_active=True).count()
        locked_accounts_count = LoginProtection.objects.filter(locked_until__gt=now).count()
        failed_logins_24h = UserLoginActivity.objects.filter(status='failed', timestamp__gte=since_24h).count()
        new_devices_24h = AuditLog.objects.filter(action='new_device_login', timestamp__gte=since_24h).count()

        active_sessions = UserSession.objects.filter(is_active=True).select_related('user').order_by('-login_time')[:15]
        locked_entries = LoginProtection.objects.filter(locked_until__gt=now).select_related('user').order_by('-locked_until')
        recent_security_logs = AuditLog.objects.select_related('actor').order_by('-timestamp')[:25]

        context = {
            'active_sessions_count': active_sessions_count,
            'locked_accounts_count': locked_accounts_count,
            'failed_logins_24h': failed_logins_24h,
            'new_devices_24h': new_devices_24h,
            'active_sessions': active_sessions,
            'locked_entries': locked_entries,
            'recent_security_logs': recent_security_logs,
        }

        if request.headers.get('HX-Request') == 'true':
            return render(request, 'admin_panel/security_dashboard_partial.html', context)
        return render(request, 'admin_panel/security_dashboard.html', context)
