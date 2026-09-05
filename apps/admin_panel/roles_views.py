from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import AccessMixin
from apps.accounts.mixins import AdminRequiredMixin
from django.views.generic import ListView, CreateView, UpdateView, DetailView, View
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.urls import reverse_lazy
from django.template.defaulttags import register
from django.db.models import Q
from django.utils.decorators import method_decorator

from apps.accounts.engine import PermissionEngine, SCOPE_HIERARCHY
from apps.accounts.models import (
    Role, Module, Action, Permission, RolePermission,
    UserRoleAssignment, UserPermissionOverride, DataScope
)
from apps.accounts.services import RoleAssignmentService, RolePermissionAssignmentService
from apps.accounts.rbac_registry import RBACRegistryService
import json
from django.core.exceptions import PermissionDenied, ValidationError
from apps.accounts.decorators import require_reauth
from apps.admin_panel.forms import DynamicRoleForm
from apps.audit.services import AuditService
from apps.notifications.models import log_audit
from apps.employees.models import EmployeeProfile

User = get_user_model()


@register.filter
def get_item(dictionary, key):
    if isinstance(dictionary, dict):
        return dictionary.get(key)
    return None


def log_rbac_event(actor, action, target=None, summary="", request=None, before=None, after=None):
    ip = request.META.get('REMOTE_ADDR') if request else None
    AuditService.log_event(
        actor=actor,
        action=action,
        instance=target,
        before=before or {},
        after=after or {},
        request=request
    )
    log_audit(
        actor=actor,
        action=action,
        target=target,
        summary=summary,
        ip=ip
    )


from django.http import HttpResponse, JsonResponse, HttpResponseForbidden


class RBACRoleAdminMixin(AccessMixin):
    """
    Enforces dynamic PermissionEngine decisions for RBAC role administration.
    - Superusers allowed full access.
    - Users with accounts.view (for read) or accounts.edit (for write) allowed if granted by PermissionEngine.
    """
    required_action = 'view'

    def handle_no_permission(self):
        if not self.request.user.is_authenticated:
            return super().handle_no_permission()
        if self.request.content_type == 'application/json' or self.request.headers.get('Accept') == 'application/json':
            return JsonResponse({'status': 'error', 'message': 'Access denied: insufficient RBAC permissions to administer system roles.'}, status=403)
        if self.request.headers.get('HX-Request'):
            from django.template.loader import render_to_string
            content = render_to_string(
                'cotton/permission_denied_hx.html',
                {'message': 'You do not have permission to administer roles.'},
                request=self.request
            )
            response = HttpResponseForbidden(content)
            response['HX-Reswap'] = 'none'
            return response
        raise PermissionDenied("Access denied: insufficient RBAC permissions to administer system roles.")

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        if request.user.is_superuser:
            return super().dispatch(request, *args, **kwargs)

        perm_codename = 'accounts.edit' if self.required_action == 'edit' else 'accounts.view'
        eval_res = PermissionEngine.evaluate(
            user=request.user,
            codename=perm_codename,
            action_type=self.required_action
        )

        if not eval_res.allowed:
            alt_codename = 'roles.edit' if self.required_action == 'edit' else 'roles.view'
            eval_alt = PermissionEngine.evaluate(
                user=request.user,
                codename=alt_codename,
                action_type=self.required_action
            )
            if not eval_alt.allowed:
                log_rbac_event(
                    request.user,
                    'unauthorized_rbac_admin_access_attempt',
                    summary=f"Unauthorized access attempt to {request.path}",
                    request=request
                )
                return self.handle_no_permission()
            eval_res = eval_alt

        request.rbac_eval = eval_res
        return super().dispatch(request, *args, **kwargs)


class DynamicRoleListView(RBACRoleAdminMixin, ListView):
    model = Role
    template_name = 'admin_panel/roles/role_list.html'
    context_object_name = 'roles'
    required_action = 'view'

    def get_queryset(self):
        return Role.objects.prefetch_related(
            'user_assignments',
            'role_permissions',
            'role_permissions__permission'
        ).order_by('-is_system_protected', 'name')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        can_edit = user.is_superuser or PermissionEngine.evaluate(user, 'accounts.edit').allowed or getattr(user, 'role', '') in ('admin', 'system_owner')
        context['can_manage_roles'] = can_edit
        context['is_superuser'] = user.is_superuser
        return context


@method_decorator(require_reauth, name='dispatch')
class DynamicRoleCreateView(RBACRoleAdminMixin, CreateView):
    model = Role
    form_class = DynamicRoleForm
    template_name = 'admin_panel/roles/role_form.html'
    success_url = reverse_lazy('admin_panel:role_list')
    required_action = 'edit'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        log_rbac_event(
            actor=self.request.user,
            action='role_created',
            target=self.object,
            summary=f"Created dynamic role '{self.object.name}' with code '{self.object.code}'",
            request=self.request,
            after={'name': self.object.name, 'code': self.object.code, 'is_active': self.object.is_active}
        )
        messages.success(self.request, f"Role '{self.object.name}' created successfully.")
        return response


@method_decorator(require_reauth, name='dispatch')
class DynamicRoleUpdateView(RBACRoleAdminMixin, UpdateView):
    model = Role
    form_class = DynamicRoleForm
    template_name = 'admin_panel/roles/role_form.html'
    success_url = reverse_lazy('admin_panel:role_list')
    required_action = 'edit'

    def dispatch(self, request, *args, **kwargs):
        role = self.get_object()
        if role.is_system_protected or role.code == 'system_owner':
            messages.error(request, "Protected System Owner role cannot be modified via the UI.")
            log_rbac_event(request.user, 'unauthorized_system_owner_edit_attempt', target=role, summary="Attempted UI edit on protected system_owner", request=request)
            return redirect('admin_panel:role_list')

        if role.code == 'super_admin' and not request.user.is_superuser:
            messages.error(request, "Only a System Owner (superuser) can configure the Super Admin role.")
            log_rbac_event(request.user, 'unauthorized_super_admin_edit_attempt', target=role, summary="Non-superuser attempted edit on super_admin role", request=request)
            return redirect('admin_panel:role_list')

        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        old_role = Role.objects.get(pk=self.object.pk)
        before = {'name': old_role.name, 'code': old_role.code, 'is_active': old_role.is_active, 'description': old_role.description}
        response = super().form_valid(form)
        after = {'name': self.object.name, 'code': self.object.code, 'is_active': self.object.is_active, 'description': self.object.description}
        log_rbac_event(
            actor=self.request.user,
            action='role_updated',
            target=self.object,
            summary=f"Updated dynamic role '{self.object.name}' ({self.object.code})",
            request=self.request,
            before=before,
            after=after
        )
        messages.success(self.request, f"Role '{self.object.name}' updated successfully.")
        return response


@method_decorator(require_reauth, name='dispatch')
class DynamicRoleDeleteView(RBACRoleAdminMixin, View):
    """
    Replaces hard deletion with audited soft deactivation or reactivation.
    Protects System Owner and guards against last-privileged-user lockout.
    """
    success_url = reverse_lazy('admin_panel:role_list')
    required_action = 'edit'

    def post(self, request, pk):
        role = get_object_or_404(Role, pk=pk)

        # 1. System Owner protection
        if role.is_system_protected or role.code == 'system_owner':
            messages.error(request, "Protected System Owner role cannot be modified, disabled, or deleted.")
            log_rbac_event(request.user, 'unauthorized_system_owner_deactivation_attempt', target=role, summary="Attempted deactivation on system_owner", request=request)
            return redirect(self.success_url)

        # 2. Super Admin boundary
        if role.code == 'super_admin' and not request.user.is_superuser:
            messages.error(request, "Only a System Owner (superuser) can deactivate or activate the Super Admin role.")
            log_rbac_event(request.user, 'unauthorized_super_admin_deactivation_attempt', target=role, summary="Non-superuser attempted deactivation on super_admin", request=request)
            return redirect(self.success_url)

        action = request.POST.get('action', 'deactivate')
        if action == 'activate' or (not role.is_active and action != 'deactivate'):
            role.is_active = True
            role.save(update_fields=['is_active', 'updated_at'])
            log_rbac_event(request.user, 'role_activated', target=role, summary=f"Activated role '{role.name}' ({role.code})", request=request)
            messages.success(request, f"Role '{role.name}' activated successfully.")
            return redirect(self.success_url)

        # Deactivation lockout check
        is_privileged = (
            role.code in ['admin', 'super_admin', 'system_owner'] or
            role.role_permissions.filter(
                permission__codename='accounts.edit',
                data_scope='global'
            ).exists()
        )

        if is_privileged:
            other_active_privileged = Role.objects.filter(
                is_active=True
            ).exclude(pk=role.pk).filter(
                Q(code__in=['admin', 'super_admin', 'system_owner']) |
                Q(role_permissions__permission__codename='accounts.edit', role_permissions__data_scope='global')
            ).distinct()

            active_privileged_users = UserRoleAssignment.objects.filter(
                role__in=other_active_privileged,
                user__is_active=True
            ).exists()

            active_superusers = User.objects.filter(is_superuser=True, is_active=True).exists()

            if not (active_privileged_users or active_superusers):
                messages.error(request, "Cannot deactivate role: it is the last effective privileged role (lockout prevention).")
                log_rbac_event(request.user, 'role_lockout_prevented', target=role, summary=f"Deactivation blocked on role '{role.code}' to prevent administrative lockout", request=request)
                return redirect(self.success_url)

        role.is_active = False
        role.save(update_fields=['is_active', 'updated_at'])
        log_rbac_event(request.user, 'role_deactivated', target=role, summary=f"Deactivated role '{role.name}' ({role.code})", request=request)
        messages.success(request, f"Role '{role.name}' deactivated successfully.")
        return redirect(self.success_url)


class DynamicRoleMatrixView(RBACRoleAdminMixin, DetailView):
    model = Role
    template_name = 'admin_panel/roles/role_matrix.html'
    context_object_name = 'role'
    required_action = 'view'

    def get_object(self, queryset=None):
        pk = self.kwargs.get(self.pk_url_kwarg)
        try:
            return super().get_object(queryset)
        except Exception:
            if pk == 1:
                role, _ = Role.objects.get_or_create(
                    id=1,
                    defaults={
                        'name': 'System Owner',
                        'code': 'system_owner',
                        'is_system_protected': True,
                        'is_active': True,
                        'description': 'Protected recovery role with full system privileges.'
                    }
                )
                return role
            raise

    def dispatch(self, request, *args, **kwargs):
        role = self.get_object()
        if role.code == 'super_admin' and not request.user.is_superuser:
            messages.error(request, "Only a System Owner (superuser) can configure the Super Admin permission matrix.")
            log_rbac_event(request.user, 'unauthorized_super_admin_matrix_attempt', target=role, summary="Non-superuser attempted to view super_admin permission matrix", request=request)
            return redirect('admin_panel:role_list')
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        """Handle atomic save via POST to matrix view."""
        role = self.get_object()
        return RoleMatrixSaveView().handle_save(request, role)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        role = self.object
        user = self.request.user

        # Ensure permissions are synchronized idempotently
        if not Permission.objects.filter(module__is_active=True).exists():
            RBACRegistryService.sync_database()

        # Fetch canonical hierarchy and lookup maps
        hierarchy_tree = RBACRegistryService.get_canonical_hierarchy()
        flat_nodes = RBACRegistryService.get_all_nodes_flat()
        nodes_by_id, parent_map, children_map, descendants_map = RBACRegistryService.get_node_lookup_maps()

        # Fetch currently assigned RolePermission records
        role_perms = RolePermission.objects.filter(role=role).select_related('permission')
        assigned_perm_codes = {rp.permission.codename: rp.data_scope for rp in role_perms}

        is_sys_owner = role.is_system_protected or role.code == 'system_owner'
        actor_is_super = user.is_superuser
        actor_resolved = PermissionEngine.get_user_resolved_permissions(user) if not actor_is_super else {}

        can_edit = (
            actor_is_super or
            PermissionEngine.evaluate(user, 'accounts.edit').allowed or
            PermissionEngine.evaluate(user, 'roles.edit').allowed
        ) and not is_sys_owner

        # Build initial selections, indeterminates, scopes, and disabled actions
        selections = {}
        indeterminates = {}
        scopes = {}
        disabled_actions = {}

        for node in flat_nodes:
            nid = node['id']
            prefix = node['perm_prefix']
            selections[nid] = {}
            indeterminates[nid] = {}
            disabled_actions[nid] = {}
            scopes[nid] = DataScope.GLOBAL

            for act in ['view', 'add', 'edit', 'update', 'delete']:
                code = f"{prefix}.{act}"
                is_granted = is_sys_owner or (code in assigned_perm_codes)

                selections[nid][act] = is_granted
                indeterminates[nid][act] = False

                if code in assigned_perm_codes:
                    scopes[nid] = assigned_perm_codes[code]

                # Actor ceiling
                if not actor_is_super:
                    actor_p = actor_resolved.get(code)
                    disabled_actions[nid][act] = not (actor_p and actor_p.get('granted'))
                else:
                    disabled_actions[nid][act] = False

            # Node-level All state
            acts_granted = sum(1 for act in ['view', 'add', 'edit', 'update', 'delete'] if selections[nid][act])
            if acts_granted == 5:
                selections[nid]['all'] = True
                indeterminates[nid]['all'] = False
            elif acts_granted > 0:
                selections[nid]['all'] = False
                indeterminates[nid]['all'] = True
            else:
                selections[nid]['all'] = False
                indeterminates[nid]['all'] = False

        # Propagate states up to menus, submodules, and modules
        for level in ['menu', 'submodule', 'module']:
            for node in [n for n in flat_nodes if n['level'] == level]:
                nid = node['id']
                cids = children_map.get(nid, [])
                if not cids:
                    continue

                for act in ['view', 'add', 'edit', 'update', 'delete']:
                    child_states = [selections[cid][act] for cid in cids if cid in selections]
                    child_indets = [indeterminates[cid][act] for cid in cids if cid in indeterminates]
                    all_true = all(child_states) if child_states else False
                    any_true = any(child_states) or any(child_indets)

                    direct_granted = selections[nid].get(act, False)
                    if direct_granted:
                        selections[nid][act] = True
                        indeterminates[nid][act] = False
                    elif all_true and not any(child_indets):
                        selections[nid][act] = True
                        indeterminates[nid][act] = False
                    elif any_true:
                        selections[nid][act] = False
                        indeterminates[nid][act] = True
                    else:
                        selections[nid][act] = False
                        indeterminates[nid][act] = False

                acts_true = [selections[nid][act] for act in ['add', 'edit', 'delete', 'update']]
                acts_indet = [indeterminates[nid][act] for act in ['add', 'edit', 'delete', 'update']]
                if all(acts_true) and not any(acts_indet):
                    selections[nid]['all'] = True
                    indeterminates[nid]['all'] = False
                elif any(acts_true) or any(acts_indet):
                    selections[nid]['all'] = False
                    indeterminates[nid]['all'] = True
                else:
                    selections[nid]['all'] = False
                    indeterminates[nid]['all'] = False

        # Legacy modules_with_perms for compatibility
        modules = Module.objects.filter(is_active=True).prefetch_related(
            'permissions', 'permissions__action'
        ).order_by('sort_order', 'name')
        modules_with_perms = []
        for mod in modules:
            modules_with_perms.append({
                'module': mod,
                'permissions': mod.permissions.select_related('action').order_by('action__name')
            })

        matrix_bundle = {
            'role_id': role.id,
            'role_code': role.code,
            'role_name': role.name,
            'is_system_protected': is_sys_owner,
            'can_edit': can_edit,
            'selections': selections,
            'indeterminates': indeterminates,
            'scopes': scopes,
            'disabled_actions': disabled_actions,
            'parent_map': parent_map,
            'children_map': children_map,
            'descendants_map': {k: list(v) for k, v in descendants_map.items()},
        }

        context['hierarchy_tree'] = hierarchy_tree
        context['flat_nodes'] = flat_nodes
        context['matrix_bundle_json'] = json.dumps(matrix_bundle)
        context['modules_with_perms'] = modules_with_perms
        context['role_perm_ids'] = set(role_perms.values_list('permission_id', flat=True))
        context['perm_scope_map'] = {rp.permission_id: rp.data_scope for rp in role_perms}
        context['total_permissions_count'] = Permission.objects.count() or (len(flat_nodes) * 4)
        context['active_permissions_count'] = role_perms.count() if not is_sys_owner else (len(flat_nodes) * 4)
        context['data_scope_choices'] = DataScope.choices
        context['is_superuser'] = actor_is_super
        context['can_edit'] = can_edit
        return context


@method_decorator(require_reauth, name='dispatch')
class RoleMatrixSaveView(RBACRoleAdminMixin, View):
    required_action = 'edit'

    def post(self, request, pk=None, role_id=None):
        r_id = pk or role_id or request.POST.get('role_id')
        role = get_object_or_404(Role, pk=r_id)
        return self.handle_save(request, role)

    def handle_save(self, request, role):
        if role.is_system_protected or role.code == 'system_owner':
            return JsonResponse({'status': 'error', 'message': 'System Owner permissions are protected and cannot be modified.'}, status=403)

        if role.code == 'super_admin' and not request.user.is_superuser:
            return JsonResponse({'status': 'error', 'message': 'Only a System Owner (superuser) can configure Super Admin permissions.'}, status=403)

        selections = {}
        scopes = {}
        try:
            if request.content_type == 'application/json':
                payload = json.loads(request.body.decode('utf-8'))
                selections = payload.get('selections', {})
                scopes = payload.get('scopes', {})
            else:
                raw_json = request.POST.get('matrix_payload')
                if raw_json:
                    payload = json.loads(raw_json)
                    selections = payload.get('selections', {})
                    scopes = payload.get('scopes', {})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': f"Invalid payload format: {e}"}, status=400)

        try:
            added, removed = RolePermissionAssignmentService.sync_role_permissions(
                role=role,
                selections=selections,
                data_scopes=scopes,
                actor=request.user,
                request=request
            )
            return JsonResponse({
                'status': 'ok',
                'message': f"Permissions for role '{role.name}' saved successfully. (+{added}, -{removed})",
                'added': added,
                'removed': removed
            })
        except (ValidationError, PermissionDenied) as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=403)
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': f"An unexpected error occurred: {e}"}, status=500)


@method_decorator(require_reauth, name='post')
class RoleMembersView(RBACRoleAdminMixin, View):
    required_action = 'edit'

    def get(self, request, pk):
        role = get_object_or_404(Role, pk=pk)
        if role.code == 'super_admin' and not request.user.is_superuser:
            messages.error(request, "Only a System Owner (superuser) can view or manage Super Admin membership.")
            return redirect('admin_panel:role_list')

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
            'is_superuser': request.user.is_superuser,
        }
        return render(request, 'admin_panel/roles/role_members.html', context)

    def post(self, request, pk):
        role = get_object_or_404(Role, pk=pk)

        if role.is_system_protected or role.code == 'system_owner':
            messages.error(request, "System Owner memberships cannot be altered through the UI.")
            log_rbac_event(request.user, 'unauthorized_system_owner_membership_change', target=role, summary="Attempted UI membership change on system_owner", request=request)
            return redirect('admin_panel:role_members', pk=role.pk)

        if role.code == 'super_admin' and not request.user.is_superuser:
            messages.error(request, "Only a System Owner (superuser) can assign or remove Super Admin members.")
            log_rbac_event(request.user, 'unauthorized_super_admin_membership_change', target=role, summary="Non-superuser attempted membership change on super_admin", request=request)
            return redirect('admin_panel:role_members', pk=role.pk)

        action = request.POST.get('action')
        if action == 'add':
            user_ids = request.POST.getlist('user_ids')
            added_count = 0
            if user_ids:
                users_to_add = User.objects.filter(pk__in=user_ids)
                for user in users_to_add:
                    current_roles = list(Role.objects.filter(user_assignments__user=user, is_active=True))
                    if role not in current_roles:
                        current_roles.append(role)
                    try:
                        added, _ = RoleAssignmentService.sync_user_roles(
                            user=user,
                            target_roles=current_roles,
                            actor=request.user,
                            request=request,
                            preserve_protected=True
                        )
                        if added:
                            added_count += len(added)
                    except (ValidationError, PermissionDenied) as e:
                        messages.error(request, f"Cannot assign role '{role.name}' to {user.email or user.phone}: {e}")
                        return redirect('admin_panel:role_members', pk=role.pk)
            messages.success(request, f"Successfully added {added_count} user(s) to role '{role.name}'.")

        elif action == 'remove':
            user_id = request.POST.get('user_id')
            if user_id:
                target_user = User.objects.filter(pk=user_id).first()
                if target_user:
                    current_roles = [r for r in Role.objects.filter(user_assignments__user=target_user, is_active=True) if r.pk != role.pk]
                    try:
                        _, removed = RoleAssignmentService.sync_user_roles(
                            user=target_user,
                            target_roles=current_roles,
                            actor=request.user,
                            request=request,
                            preserve_protected=True
                        )
                        if removed:
                            messages.info(request, f"Removed user '{target_user.email or target_user.phone}' from role '{role.name}'.")
                    except (ValidationError, PermissionDenied) as e:
                        messages.error(request, f"Cannot remove role '{role.name}': {e}")
                        return redirect('admin_panel:role_members', pk=role.pk)

        return redirect('admin_panel:role_members', pk=role.pk)


@method_decorator(require_reauth, name='dispatch')
class RolePermissionToggleView(RBACRoleAdminMixin, View):
    required_action = 'edit'

    def post(self, request, role_id, perm_id):
        role = get_object_or_404(Role, pk=role_id)
        perm = get_object_or_404(Permission, pk=perm_id)

        if role.is_system_protected or role.code == 'system_owner':
            log_rbac_event(request.user, 'unauthorized_system_owner_perm_toggle', target=role, summary="Attempted permission toggle on system_owner", request=request)
            return JsonResponse({'status': 'error', 'message': 'System Owner permissions cannot be modified via the UI.'}, status=403)

        if role.code == 'super_admin' and not request.user.is_superuser:
            log_rbac_event(request.user, 'unauthorized_super_admin_perm_toggle', target=role, summary="Non-superuser attempted permission toggle on super_admin", request=request)
            return JsonResponse({'status': 'error', 'message': 'Only a System Owner (superuser) can configure Super Admin permissions.'}, status=403)

        if not request.user.is_superuser:
            actor_resolved = PermissionEngine.get_user_resolved_permissions(request.user)
            actor_perm = actor_resolved.get(perm.codename)
            if not actor_perm or not actor_perm.get('granted'):
                log_rbac_event(request.user, 'unauthorized_perm_grant_attempt', target=role, summary=f"User attempted to grant unheld permission '{perm.codename}' to role '{role.code}'", request=request)
                return JsonResponse({'status': 'error', 'message': f"Cannot grant permission '{perm.codename}': you do not hold this permission."}, status=403)

            actor_scope = actor_perm.get('scope', DataScope.OWN)
        else:
            actor_scope = DataScope.GLOBAL

        existing = RolePermission.objects.filter(role=role, permission=perm).first()
        if existing:
            existing.delete()
            granted = False
            log_rbac_event(request.user, 'role_permission_revoked', target=role, summary=f"Revoked permission '{perm.codename}' from role '{role.code}'", request=request)
        else:
            RolePermission.objects.create(role=role, permission=perm, data_scope=actor_scope)
            granted = True
            log_rbac_event(request.user, 'role_permission_granted', target=role, summary=f"Granted permission '{perm.codename}' with scope '{actor_scope}' to role '{role.code}'", request=request)

        return JsonResponse({'status': 'ok', 'granted': granted, 'role_id': role_id, 'perm_id': perm_id})


@method_decorator(require_reauth, name='dispatch')
class RolePermissionScopeView(RBACRoleAdminMixin, View):
    required_action = 'edit'

    def post(self, request, role_id, perm_id):
        role = get_object_or_404(Role, pk=role_id)
        perm = get_object_or_404(Permission, pk=perm_id)
        new_scope = request.POST.get('data_scope', DataScope.GLOBAL)

        if role.is_system_protected or role.code == 'system_owner':
            log_rbac_event(request.user, 'unauthorized_system_owner_scope_change', target=role, summary="Attempted scope change on system_owner", request=request)
            return JsonResponse({'status': 'error', 'message': 'System Owner permissions cannot be modified via the UI.'}, status=403)

        if role.code == 'super_admin' and not request.user.is_superuser:
            log_rbac_event(request.user, 'unauthorized_super_admin_scope_change', target=role, summary="Non-superuser attempted scope change on super_admin", request=request)
            return JsonResponse({'status': 'error', 'message': 'Only a System Owner (superuser) can configure Super Admin scopes.'}, status=403)

        if not request.user.is_superuser:
            actor_resolved = PermissionEngine.get_user_resolved_permissions(request.user)
            actor_perm = actor_resolved.get(perm.codename)
            if not actor_perm or not actor_perm.get('granted'):
                log_rbac_event(request.user, 'unauthorized_scope_change_attempt', target=role, summary=f"User attempted to modify scope for unheld permission '{perm.codename}'", request=request)
                return JsonResponse({'status': 'error', 'message': f"Cannot configure scope for '{perm.codename}': you do not hold this permission."}, status=403)

            actor_scope = actor_perm.get('scope', DataScope.OWN)
            actor_rank = SCOPE_HIERARCHY.get(actor_scope, 0)
            requested_rank = SCOPE_HIERARCHY.get(new_scope, 0)

            if requested_rank > actor_rank:
                log_rbac_event(request.user, 'unauthorized_scope_elevation_attempt', target=role, summary=f"Scope elevation attempt: requested '{new_scope}' (rank {requested_rank}) but actor has '{actor_scope}' (rank {actor_rank})", request=request)
                return JsonResponse({
                    'status': 'error',
                    'message': f"Privilege violation: Cannot grant data scope '{new_scope}' which exceeds your effective scope '{actor_scope}'."
                }, status=403)

        rp, created = RolePermission.objects.get_or_create(role=role, permission=perm)
        old_scope = rp.data_scope
        rp.data_scope = new_scope
        rp.save()

        log_rbac_event(request.user, 'role_scope_changed', target=role, summary=f"Changed scope for '{perm.codename}' on role '{role.code}' from '{old_scope}' to '{new_scope}'", request=request)
        return JsonResponse({'status': 'ok', 'scope': new_scope})


@method_decorator(require_reauth, name='post')
class UserPermissionsView(RBACRoleAdminMixin, DetailView):
    model = User
    template_name = 'admin_panel/roles/user_permissions.html'
    context_object_name = 'target_user'
    required_action = 'edit'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        target = self.object

        roles_qs = RoleAssignmentService.get_assignable_roles_queryset(actor=self.request.user)

        context['all_roles'] = roles_qs.order_by('name')
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

        if not request.user or not request.user.is_authenticated:
            messages.error(request, "Authentication required to manage user roles.")
            return redirect('admin_panel:user_permissions', pk=target.pk)

        submitted_ids = set()
        for r_id in role_ids:
            try:
                submitted_ids.add(int(r_id))
            except (ValueError, TypeError):
                messages.error(request, "Invalid role ID format submitted.")
                return redirect('admin_panel:user_permissions', pk=target.pk)

        assignable_roles = RoleAssignmentService.get_assignable_roles_queryset(actor=request.user)
        target_roles = list(assignable_roles.filter(pk__in=submitted_ids))

        found_ids = {r.id for r in target_roles}
        invalid_ids = submitted_ids - found_ids
        if invalid_ids:
            messages.error(request, "One or more selected roles are inactive, unauthorized, protected, or invalid.")
            return redirect('admin_panel:user_permissions', pk=target.pk)

        try:
            RoleAssignmentService.sync_user_roles(
                user=target,
                target_roles=target_roles,
                actor=request.user,
                request=request,
                preserve_protected=True
            )
            messages.success(request, f"Updated role assignments for {target.email or target.phone}.")
        except (ValidationError, PermissionDenied) as e:
            messages.error(request, f"Failed to update roles: {e}")
        except Exception as e:
            messages.error(request, f"An unexpected error occurred: {e}")

        return redirect('admin_panel:user_permissions', pk=target.pk)


@method_decorator(require_reauth, name='dispatch')
class UserPermissionOverrideSaveView(RBACRoleAdminMixin, View):
    required_action = 'edit'

    def post(self, request, pk):
        target = get_object_or_404(User, pk=pk)
        perm_id = request.POST.get('permission_id')
        is_granted = request.POST.get('is_granted') == 'true'
        data_scope = request.POST.get('data_scope') or None

        perm = get_object_or_404(Permission, pk=perm_id)

        if not request.user.is_superuser:
            actor_resolved = PermissionEngine.get_user_resolved_permissions(request.user)
            actor_perm = actor_resolved.get(perm.codename)
            if not actor_perm or not actor_perm.get('granted'):
                return JsonResponse({'status': 'error', 'message': f"Cannot override permission '{perm.codename}' you do not hold."}, status=403)
            if data_scope:
                actor_scope = actor_perm.get('scope', DataScope.OWN)
                if SCOPE_HIERARCHY.get(data_scope, 0) > SCOPE_HIERARCHY.get(actor_scope, 0):
                    return JsonResponse({'status': 'error', 'message': f"Cannot grant scope '{data_scope}' exceeding your scope '{actor_scope}'."}, status=403)

        override, _ = UserPermissionOverride.objects.get_or_create(user=target, permission=perm)
        override.is_granted = is_granted
        override.data_scope = data_scope
        override.save()
        PermissionEngine.invalidate_user_cache(target)

        log_rbac_event(request.user, 'user_permission_overridden', target=target, summary=f"Set permission override '{perm.codename}' (granted={is_granted}, scope={data_scope}) for '{target.email}'", request=request)
        messages.success(request, f"Permission override for '{perm.codename}' saved.")
        return redirect('admin_panel:user_permissions', pk=target.pk)



from django.db.models import Q
from apps.audit.models import AuditEvent
from apps.notifications.models import AuditLog


class AdminAuditLogView(AdminRequiredMixin, View):
    def get(self, request):
        action_filter = request.GET.get('action', '').strip()
        module_filter = request.GET.get('module', '').strip()
        search_query = request.GET.get('q', '').strip()

        logs = AuditEvent.objects.select_related('actor_user').order_by('-created_at')

        if module_filter:
            logs = logs.filter(module__iexact=module_filter)
        if action_filter:
            logs = logs.filter(action__iexact=action_filter)
        if search_query:
            logs = logs.filter(
                Q(actor_user__email__icontains=search_query) |
                Q(actor_user__phone__icontains=search_query) |
                Q(actor_role__icontains=search_query) |
                Q(object_label__icontains=search_query) |
                Q(object_type__icontains=search_query) |
                Q(object_id__icontains=search_query) |
                Q(reason_note__icontains=search_query) |
                Q(action__icontains=search_query) |
                Q(module__icontains=search_query)
            )

        action_types = AuditEvent.objects.values_list('action', flat=True).distinct()
        modules = AuditEvent.objects.values_list('module', flat=True).distinct()

        context = {
            'logs': logs[:150],
            'action_filter': action_filter,
            'module_filter': module_filter,
            'search_query': search_query,
            'action_types': sorted([a for a in action_types if a]),
            'modules': sorted([m for m in modules if m]),
        }

        if request.headers.get('HX-Request') == 'true':
            return render(request, 'admin_panel/audit/audit_log_list_partial.html', context)
        return render(request, 'admin_panel/audit/audit_log_list.html', context)

    def post(self, request):
        messages.info(request, "Compliance Notice: AuditEvent records are immutable enterprise records and cannot be purged.")
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
        recent_security_logs = AuditEvent.objects.select_related('actor_user').order_by('-created_at')[:25]

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
