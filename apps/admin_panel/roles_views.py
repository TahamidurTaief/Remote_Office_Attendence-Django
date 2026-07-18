from django.contrib.auth.models import Group, Permission
from django.contrib.auth import get_user_model
from django.views.generic import ListView, CreateView, UpdateView, DeleteView, DetailView, View
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.urls import reverse_lazy
from apps.accounts.mixins import AdminRequiredMixin
from apps.accounts.permissions import get_effective_permissions

User = get_user_model()

# Group permissions by app label
APP_LABEL_MAPPING = {
    'projects': 'Projects',
    'attendance': 'Attendance',
    'leave': 'Leave',
    'employees': 'Employees',
    'branches': 'Branches',
    'backups': 'Backups',
    'accounts': 'Users & Roles',
}

def get_grouped_permissions():
    """
    Fetch and group permissions relevant to the application's core apps.
    """
    # Only fetch permissions belonging to our active apps
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
        
        # List employees NOT currently in this group
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
                from apps.notifications.dispatch import log_activity
                added_users = User.objects.filter(pk__in=user_ids)
                for member_user in added_users:
                    log_activity(
                        actor=request.user,
                        verb='group_membership_added',
                        target=member_user,
                        metadata={'role': group.name},
                        notify_users=[member_user]
                    )
            else:
                messages.warning(request, "No members selected to add.")
        elif action == 'remove':
            user_id = request.POST.get('user_id')
            if user_id:
                user = get_object_or_404(User, pk=user_id)
                user.groups.remove(group)
                messages.success(request, f"Removed member from role '{group.name}'.")
                from apps.notifications.dispatch import log_activity
                log_activity(
                    actor=request.user,
                    verb='group_membership_removed',
                    target=user,
                    metadata={'role': group.name},
                    notify_users=[user]
                )
        
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
        
        # Resolve effective permissions and their sources
        effective_perms = get_effective_permissions(user)
        direct_perms = set(user.user_permissions.values_list('codename', flat=True))
        
        # Group permissions by role
        role_perms_map = {}
        for group in user.groups.all().prefetch_related('permissions'):
            for perm in group.permissions.all():
                role_perms_map.setdefault(perm.codename, []).append(group.name)
                
        # Build effective permissions list with sources
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

        old_perm_ids = set(user.user_permissions.values_list('id', flat=True))
        new_perm_ids = set(int(pid) for pid in direct_perm_ids if str(pid).isdigit())
        
        # Toggle is_active
        is_active = request.POST.get('is_active') == 'on'
        user.is_active = is_active
        user.save()
        
        # Set groups and direct permissions
        user.groups.set(role_ids)
        user.user_permissions.set(direct_perm_ids)

        granted = new_perm_ids - old_perm_ids
        revoked = old_perm_ids - new_perm_ids

        from apps.notifications.dispatch import log_activity
        if granted:
            log_activity(
                actor=request.user,
                verb='permission_granted',
                target=user,
                metadata={'permission_ids': list(granted)},
                notify_users=[user]
            )
        if revoked:
            log_activity(
                actor=request.user,
                verb='permission_revoked',
                target=user,
                metadata={'permission_ids': list(revoked)},
                notify_users=[user]
            )
        
        messages.success(request, f"Roles and permissions for user '{user.email or user.phone}' updated successfully.")
        
        # If employee profile exists, redirect back to employee detail
        if hasattr(user, 'employee_profile'):
            return redirect('employees:employee_detail', pk=user.employee_profile.pk)
        return redirect('admin_panel:role_list')
