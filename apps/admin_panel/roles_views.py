from django.contrib.auth.models import Group, Permission
from django.contrib.auth import get_user_model
from django.views.generic import ListView, CreateView, UpdateView, DeleteView, DetailView, View
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import JsonResponse
from django.urls import reverse_lazy
from apps.accounts.mixins import AdminRequiredMixin
from apps.accounts.permissions import get_effective_permissions

User = get_user_model()

# Group permissions by app label covering all 10 modules
APP_LABEL_MAPPING = {
    'attendance': 'Attendance',
    'projects': 'Projects',
    'employees': 'Employees',
    'leave': 'Leave',
    'branches': 'Branches',
    'notifications': 'Notifications',
    'expense': 'Expense',
    'backups': 'Backups',
    'schedule': 'Schedule',
    'accounts': 'Accounts & Security',
}


def ensure_custom_permissions():
    """
    Ensures custom export and approval permissions exist for core modules.
    """
    from django.contrib.contenttypes.models import ContentType

    custom_perms = [
        ('attendance', 'attendance', 'export_attendance', 'Can export attendance records'),
        ('attendance', 'attendance', 'approve_attendance', 'Can approve attendance entries'),
        ('projects', 'project', 'export_projects', 'Can export project reports'),
        ('employees', 'employeeprofile', 'export_employees', 'Can export employee records'),
        ('leave', 'leaverequest', 'export_leave', 'Can export leave reports'),
        ('expense', 'expense', 'export_expense', 'Can export expense reports'),
    ]

    for app_label, model, codename, name in custom_perms:
        ct = ContentType.objects.filter(app_label=app_label, model=model).first()
        if ct:
            Permission.objects.get_or_create(
                codename=codename,
                content_type=ct,
                defaults={'name': name}
            )


def get_grouped_permissions():
    """
    Fetch and group permissions relevant to the application's core apps.
    """
    ensure_custom_permissions()
    valid_apps = list(APP_LABEL_MAPPING.keys())
    perms = Permission.objects.filter(content_type__app_label__in=valid_apps).select_related('content_type').order_by('content_type__app_label', 'name')

    grouped = {}
    for app_label, friendly_name in APP_LABEL_MAPPING.items():
        grouped[friendly_name] = [p for p in perms if p.content_type.app_label == app_label]

    return grouped


class RoleListView(AdminRequiredMixin, ListView):
    model = Group
    template_name = 'admin_panel/roles/role_list.html'
    context_object_name = 'roles'

    def get_queryset(self):
        return Group.objects.all().order_by('name')


class RoleCreateView(AdminRequiredMixin, CreateView):
    model = Group
    template_name = 'admin_panel/roles/role_form.html'
    fields = ['name']
    success_url = reverse_lazy('admin_panel:role_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f"Role '{self.object.name}' created successfully.")
        return response


class RoleUpdateView(AdminRequiredMixin, UpdateView):
    model = Group
    template_name = 'admin_panel/roles/role_form.html'
    fields = ['name']
    success_url = reverse_lazy('admin_panel:role_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f"Role renamed to '{self.object.name}' successfully.")
        return response


class RoleDeleteView(AdminRequiredMixin, DeleteView):
    model = Group
    success_url = reverse_lazy('admin_panel:role_list')

    def post(self, request, *args, **kwargs):
        role = self.get_object()
        role_name = role.name
        role.delete()
        messages.success(request, f"Role '{role_name}' deleted successfully.")
        return redirect(self.success_url)


class RoleCloneView(AdminRequiredMixin, View):
    def post(self, request, pk):
        source_role = get_object_or_404(Group, pk=pk)
        new_name = request.POST.get('name', '').strip()
        if not new_name:
            messages.error(request, "Role name cannot be empty.")
            return redirect('admin_panel:role_list')

        if Group.objects.filter(name=new_name).exists():
            messages.error(request, f"Role '{new_name}' already exists.")
            return redirect('admin_panel:role_list')

        new_role = Group.objects.create(name=new_name)
        new_role.permissions.set(source_role.permissions.all())
        messages.success(request, f"Role '{source_role.name}' cloned into '{new_name}' successfully.")
        return redirect('admin_panel:role_list')


class RoleMembersView(AdminRequiredMixin, DetailView):
    model = Group
    template_name = 'admin_panel/roles/role_members.html'
    context_object_name = 'role'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        group = self.object
        context['members'] = User.objects.filter(groups=group).order_by('email', 'phone')

        from apps.employees.models import EmployeeProfile
        context['non_members'] = EmployeeProfile.objects.filter(user__isnull=False).exclude(user__groups=group).order_by('full_name')
        return context

    def post(self, request, *args, **kwargs):
        group = self.get_object()
        action = request.POST.get('action')

        if action == 'add':
            user_ids = request.POST.getlist('user_ids')
            if user_ids:
                group.user_set.add(*user_ids)
                messages.success(request, f"Added {len(user_ids)} members to role '{group.name}'.")
            else:
                messages.warning(request, "No members selected to add.")
        elif action == 'remove':
            user_id = request.POST.get('user_id')
            if user_id:
                user = get_object_or_404(User, pk=user_id)
                user.groups.remove(group)
                messages.success(request, f"Removed member from role '{group.name}'.")

        return redirect('admin_panel:role_members', pk=group.pk)


class RolePermissionsView(AdminRequiredMixin, View):
    def get(self, request, pk):
        role = get_object_or_404(Group, pk=pk)
        context = {
            'role': role,
            'grouped_permissions': get_grouped_permissions(),
            'role_permissions': set(role.permissions.values_list('id', flat=True)),
        }
        return render(request, 'admin_panel/roles/role_permissions.html', context)

    def post(self, request, pk):
        role = get_object_or_404(Group, pk=pk)
        permission_ids = request.POST.getlist('permissions')
        role.permissions.set(permission_ids)
        messages.success(request, f"Permissions for role '{role.name}' updated successfully.")
        return redirect('admin_panel:role_list')


class UserPermissionsView(AdminRequiredMixin, View):
    def get(self, request, pk):
        user = get_object_or_404(User, pk=pk)
        effective_perms = get_effective_permissions(user)
        direct_perms = set(user.user_permissions.values_list('codename', flat=True))

        role_perms_map = {}
        for group in user.groups.all().prefetch_related('permissions'):
            for perm in group.permissions.all():
                role_perms_map.setdefault(perm.codename, []).append(group.name)

        valid_apps = list(APP_LABEL_MAPPING.keys())
        all_perms = Permission.objects.filter(content_type__app_label__in=valid_apps).select_related('content_type')

        effective_list = []
        if user.is_superuser:
            for perm in all_perms:
                effective_list.append({
                    'name': perm.name,
                    'codename': perm.codename,
                    'app': APP_LABEL_MAPPING.get(perm.content_type.app_label, perm.content_type.app_label),
                    'source': 'Superuser Bypass (All permissions)'
                })
        else:
            for perm in all_perms:
                if perm.codename in effective_perms:
                    sources = []
                    if perm.codename in direct_perms:
                        sources.append('Directly Assigned')
                    if perm.codename in role_perms_map:
                        for gname in role_perms_map[perm.codename]:
                            sources.append(f"Role: {gname}")
                    effective_list.append({
                        'name': perm.name,
                        'codename': perm.codename,
                        'app': APP_LABEL_MAPPING.get(perm.content_type.app_label, perm.content_type.app_label),
                        'source': ' & '.join(sources)
                    })

        context = {
            'target_user': user,
            'roles': Group.objects.all().order_by('name'),
            'user_roles': set(user.groups.values_list('id', flat=True)),
            'grouped_permissions': get_grouped_permissions(),
            'user_direct_permissions': set(user.user_permissions.values_list('id', flat=True)),
            'effective_permissions': effective_list,
        }
        return render(request, 'admin_panel/roles/user_permissions.html', context)

    def post(self, request, pk):
        user = get_object_or_404(User, pk=pk)
        role_ids = request.POST.getlist('roles')
        direct_perm_ids = request.POST.getlist('permissions')

        is_active = request.POST.get('is_active') == 'on'
        user.is_active = is_active
        user.save()

        user.groups.set(role_ids)
        user.user_permissions.set(direct_perm_ids)

        messages.success(request, f"Roles and permissions for user '{user.email or user.phone}' updated successfully.")
        return redirect('admin_panel:role_list')


class PermissionMatrixView(AdminRequiredMixin, View):
    """
    GET /admin-panel/permissions/matrix/
    Permission Matrix Editor displaying Module vs Action per role.
    """
    def get(self, request):
        ensure_custom_permissions()
        roles = Group.objects.all().order_by('name')
        selected_role_id = request.GET.get('role_id')
        selected_role = roles.filter(pk=selected_role_id).first() if selected_role_id else roles.first()

        role_perm_ids = set(selected_role.permissions.values_list('id', flat=True)) if selected_role else set()

        valid_apps = list(APP_LABEL_MAPPING.keys())
        all_perms = Permission.objects.filter(content_type__app_label__in=valid_apps).select_related('content_type')

        # Matrix: Module -> Action (view, create, edit, delete, approve, export) -> Permission
        matrix = {}
        for app_label, module_title in APP_LABEL_MAPPING.items():
            matrix[app_label] = {
                'title': module_title,
                'view': None,
                'create': None,
                'edit': None,
                'delete': None,
                'approve': None,
                'export': None,
                'others': []
            }

        for perm in all_perms:
            app_label = perm.content_type.app_label
            if app_label not in matrix:
                continue

            code = perm.codename
            perm_data = {
                'id': perm.id,
                'codename': code,
                'name': perm.name,
                'is_checked': perm.id in role_perm_ids
            }

            if code.startswith('view_'):
                if not matrix[app_label]['view']: matrix[app_label]['view'] = perm_data
            elif code.startswith('add_'):
                if not matrix[app_label]['create']: matrix[app_label]['create'] = perm_data
            elif code.startswith('change_'):
                if not matrix[app_label]['edit']: matrix[app_label]['edit'] = perm_data
            elif code.startswith('delete_'):
                if not matrix[app_label]['delete']: matrix[app_label]['delete'] = perm_data
            elif code.startswith('approve_'):
                if not matrix[app_label]['approve']: matrix[app_label]['approve'] = perm_data
            elif code.startswith('export_'):
                if not matrix[app_label]['export']: matrix[app_label]['export'] = perm_data
            else:
                matrix[app_label]['others'].append(perm_data)

        context = {
            'roles': roles,
            'selected_role': selected_role,
            'matrix': matrix,
        }
        return render(request, 'admin_panel/roles/permission_matrix.html', context)


class PermissionToggleView(AdminRequiredMixin, View):
    """
    POST /admin-panel/roles/<int:group_id>/permission/<int:perm_id>/toggle/
    HTMX live toggle action for role permission matrix.
    """
    def post(self, request, group_id, perm_id):
        role = get_object_or_404(Group, pk=group_id)
        perm = get_object_or_404(Permission, pk=perm_id)

        if role.permissions.filter(id=perm.id).exists():
            role.permissions.remove(perm)
            is_checked = False
        else:
            role.permissions.add(perm)
            is_checked = True

        return render(request, 'cotton/permission-toggle.html', {
            'group_id': role.id,
            'perm_id': perm.id,
            'is_checked': is_checked
        })


from django.db.models import Q
from apps.notifications.models import AuditLog

class AdminAuditLogView(AdminRequiredMixin, View):
    """
    GET /admin-panel/audit-logs/
    Admin Audit Log Viewer displaying sensitive system action logs with HTMX live filtering.
    """
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


from datetime import timedelta
from django.utils import timezone
from apps.accounts.models import UserSession, UserLoginActivity

class AdminSecurityDashboardView(AdminRequiredMixin, View):
    """
    GET /admin-panel/security-dashboard/
    Admin Security Dashboard showing active sessions, locked accounts,
    failed login stats, new device alerts, and live security timeline.
    """
    def get(self, request):
        now = timezone.now()
        since_24h = now - timedelta(hours=24)

        active_sessions_count = UserSession.objects.filter(is_active=True).count()
        locked_accounts_count = User.objects.filter(locked_until__gt=now).count()
        failed_logins_24h = UserLoginActivity.objects.filter(status='failed', timestamp__gte=since_24h).count()
        new_devices_24h = AuditLog.objects.filter(action='new_device_login', timestamp__gte=since_24h).count()

        active_sessions = UserSession.objects.filter(is_active=True).select_related('user').order_by('-login_time')[:15]
        locked_users = User.objects.filter(locked_until__gt=now).order_by('-locked_until')
        recent_security_logs = AuditLog.objects.select_related('actor').order_by('-timestamp')[:25]

        context = {
            'active_sessions_count': active_sessions_count,
            'locked_accounts_count': locked_accounts_count,
            'failed_logins_24h': failed_logins_24h,
            'new_devices_24h': new_devices_24h,
            'active_sessions': active_sessions,
            'locked_users': locked_users,
            'recent_security_logs': recent_security_logs,
        }

        if request.headers.get('HX-Request') == 'true':
            return render(request, 'admin_panel/security_dashboard_partial.html', context)
        return render(request, 'admin_panel/security_dashboard.html', context)


