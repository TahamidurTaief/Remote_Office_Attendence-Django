import logging
from typing import Iterable, Optional, Set, Tuple, List, Dict, Any
from django.db import transaction
from django.core.exceptions import PermissionDenied, ValidationError
from django.contrib.auth import get_user_model
from apps.accounts.rbac_models import Role, UserRoleAssignment, RolePermission, DataScope
from apps.accounts.engine import PermissionEngine, SCOPE_HIERARCHY
from apps.audit.services import AuditService
from apps.notifications.models import log_audit

logger = logging.getLogger(__name__)
User = get_user_model()


class RoleAssignmentService:
    """
    Atomic role-assignment service for employee and user accounts.
    - Computes assignment diffs (added vs removed roles)
    - Preserves protected roles (system_owner, super_admin, out-of-scope assignments)
    - Prevents privilege escalation: non-superusers cannot grant or remove roles exceeding their effective access
    - Validates server-side that actor is authenticated and authorized (fails closed on actor=None)
    - Rejects inactive role assignments
    - Provides explicit trusted_internal pathway for internal fixtures/tasks
    - Records assigned_by
    - Maps CustomUser.role persona ('admin', 'manager', 'staff')
    - Invalidates PermissionEngine cache
    - Creates comprehensive audit logs
    - Ensures atomic rollback on failure
    """

    COMPATIBILITY_ROLE_MAP = {
        'system_owner': 'admin',
        'super_admin': 'admin',
        'admin': 'admin',
        'administrator': 'admin',
        'manager': 'manager',
        'branch_manager': 'manager',
        'project_manager': 'manager',
        'department_head': 'manager',
    }

    @classmethod
    def get_assignable_roles_queryset(cls, actor=None):
        """
        Returns roles queryset that the actor is authorized to view and assign.
        - Fails closed if actor is None or unauthenticated.
        - 'system_owner' is NEVER assignable via employee forms.
        - 'super_admin' is assignable ONLY by superusers / System Owner.
        - Non-superusers can only assign active roles that do not exceed their own effective permissions/scopes.
        """
        if not actor or not actor.is_authenticated:
            return Role.objects.none()

        qs = Role.objects.filter(is_active=True).exclude(code='system_owner').exclude(is_system_protected=True)
        if not actor.is_superuser:
            qs = qs.exclude(code='super_admin')

        # Filter out roles whose permissions exceed actor's effective permissions or scopes
        if not actor.is_superuser:
            actor_perms = PermissionEngine.get_user_resolved_permissions(actor)
            valid_role_ids = []
            for role in qs.prefetch_related('role_permissions__permission'):
                role_exceeds = False
                for rp in role.role_permissions.all():
                    perm_code = rp.permission.codename
                    actor_p = actor_perms.get(perm_code)
                    if not actor_p or not actor_p.get('granted'):
                        role_exceeds = True
                        break
                    req_rank = SCOPE_HIERARCHY.get(rp.data_scope, 0)
                    act_rank = SCOPE_HIERARCHY.get(actor_p.get('scope', DataScope.OWN), 0)
                    if req_rank > act_rank:
                        role_exceeds = True
                        break
                if not role_exceeds:
                    valid_role_ids.append(role.id)
            qs = qs.filter(id__in=valid_role_ids)

        return qs.order_by('name')

    @classmethod
    def compute_compatibility_persona(cls, roles: Iterable[Role]) -> str:
        """
        Computes the CustomUser.role backward-compatibility persona.
        Returns 'admin', 'manager', or 'staff'.
        Never returns custom arbitrary role codes.
        """
        role_codes = {r.code.lower() for r in roles}
        if any(code in role_codes for code in ['system_owner', 'super_admin', 'admin', 'administrator']):
            return 'admin'
        if any(code in role_codes for code in ['manager', 'branch_manager', 'project_manager', 'department_head']):
            return 'manager'
        return 'staff'

    @classmethod
    def validate_role_authority(
        cls,
        actor,
        target_roles: Iterable[Role],
        trusted_internal: bool = False
    ):
        """
        Validates whether actor has authority to grant each role in target_roles.
        Fails closed on actor=None or unauthenticated actor unless trusted_internal=True.
        Rejects inactive roles and system_owner.
        """
        if trusted_internal:
            return

        if not actor or not actor.is_authenticated:
            raise PermissionDenied("Authentication required to manage user roles.")

        is_super = getattr(actor, 'is_superuser', False)

        for r in target_roles:
            # 1. Reject inactive roles
            if not r.is_active:
                raise ValidationError(f"Role '{r.name}' is inactive and cannot be assigned.")

            # 2. Strict system_owner protection
            if r.code == 'system_owner' or r.is_system_protected:
                raise PermissionDenied("The System Owner role cannot be assigned via employee forms.")

            # 3. Super admin restriction
            if r.code == 'super_admin' and not is_super:
                raise PermissionDenied("Only a superuser or System Owner can assign the Super Admin role.")

        if is_super:
            return

        actor_perms = PermissionEngine.get_user_resolved_permissions(actor)
        for r in target_roles:
            for rp in r.role_permissions.select_related('permission').all():
                perm_code = rp.permission.codename
                actor_perm = actor_perms.get(perm_code)
                if not actor_perm or not actor_perm.get('granted'):
                    raise PermissionDenied(
                        f"Privilege escalation: You cannot assign role '{r.name}' because you do not hold permission '{perm_code}'."
                    )
                req_rank = SCOPE_HIERARCHY.get(rp.data_scope, 0)
                act_rank = SCOPE_HIERARCHY.get(actor_perm.get('scope', DataScope.OWN), 0)
                if req_rank > act_rank:
                    raise PermissionDenied(
                        f"Privilege escalation: You cannot assign role '{r.name}' with scope '{rp.data_scope}' exceeding your scope '{actor_perm.get('scope')}'."
                    )

    @classmethod
    def validate_role_removal_authority(cls, actor, role: Role, trusted_internal: bool = False):
        """
        Validates whether actor has authority to remove an existing role assignment.
        Non-superusers cannot remove roles exceeding their authority or protected roles.
        """
        if trusted_internal:
            return

        if not actor or not actor.is_authenticated:
            raise PermissionDenied("Authentication required to remove user roles.")

        # system_owner is strictly protected from removal
        if role.code == 'system_owner' or role.is_system_protected:
            raise PermissionDenied("The System Owner role cannot be removed.")

        if getattr(actor, 'is_superuser', False):
            return

        if role.code == 'super_admin':
            raise PermissionDenied("Only a superuser can remove the Super Admin role.")

        actor_perms = PermissionEngine.get_user_resolved_permissions(actor)
        for rp in role.role_permissions.select_related('permission').all():
            perm_code = rp.permission.codename
            actor_perm = actor_perms.get(perm_code)
            if not actor_perm or not actor_perm.get('granted'):
                raise PermissionDenied(
                    f"Unauthorized removal: You cannot remove role '{role.name}' because you do not hold permission '{perm_code}'."
                )
            req_rank = SCOPE_HIERARCHY.get(rp.data_scope, 0)
            act_rank = SCOPE_HIERARCHY.get(actor_perm.get('scope', DataScope.OWN), 0)
            if req_rank > act_rank:
                raise PermissionDenied(
                    f"Unauthorized removal: You cannot remove role '{role.name}' with scope '{rp.data_scope}' exceeding your scope '{actor_perm.get('scope')}'."
                )

    @classmethod
    def invalidate_user_permissions(cls, user):
        """
        Invalidates cached permissions on the user instance and related objects.
        """
        if not user:
            return
        if hasattr(user, '_resolved_permissions_cache'):
            delattr(user, '_resolved_permissions_cache')
        try:
            from django.core.cache import cache
            cache.delete(f"user_permissions_{user.pk}")
            cache.delete(f"rbac_user_perms_{user.pk}")
        except Exception:
            pass

    @classmethod
    @transaction.atomic
    def sync_user_roles(
        cls,
        *,
        user,
        target_roles: Iterable[Role],
        actor=None,
        request=None,
        preserve_protected: bool = True,
        trusted_internal: bool = False
    ) -> Tuple[List[Role], List[Role]]:
        """
        Diff-based atomic role assignment.
        - Validates actor authority on both addition AND removal
        - Retains protected roles (system_owner, super_admin, and roles outside actor's authority)
        - Records assigned_by and assigned_at
        - Synchronizes CustomUser.role persona
        - Logs audit events
        - Invalidates permissions cache
        - Fails closed if actor is unauthenticated unless trusted_internal=True
        Returns (added_roles, removed_roles).
        """
        if not user:
            raise ValidationError("Target user must be specified for role sync.")

        target_roles_list = list(target_roles)

        # 1. Authority validation for additions and target roles
        cls.validate_role_authority(actor, target_roles_list, trusted_internal=trusted_internal)

        # 2. Existing assignments
        existing_assignments = list(
            UserRoleAssignment.objects.filter(user=user).select_related('role')
        )
        existing_role_map = {a.role_id: a for a in existing_assignments}
        target_role_map = {r.id: r for r in target_roles_list}

        # 3. Determine preserved assignments that cannot be touched by actor
        preserved_role_ids: Set[int] = set()

        for role_id, assignment in existing_role_map.items():
            r = assignment.role
            # system_owner is ALWAYS protected
            if r.code == 'system_owner' or r.is_system_protected:
                if preserve_protected:
                    preserved_role_ids.add(role_id)
                    continue
                else:
                    raise PermissionDenied("The System Owner role cannot be removed.")

            if not trusted_internal:
                # super_admin is protected for non-superusers
                if r.code == 'super_admin' and not getattr(actor, 'is_superuser', False):
                    if preserve_protected:
                        preserved_role_ids.add(role_id)
                        continue
                    else:
                        raise PermissionDenied("Only a superuser can remove the Super Admin role.")

                # Check if non-superuser lacks authority over this existing role
                if not getattr(actor, 'is_superuser', False):
                    try:
                        cls.validate_role_removal_authority(actor, r, trusted_internal=False)
                    except PermissionDenied:
                        if preserve_protected:
                            preserved_role_ids.add(role_id)
                        else:
                            raise

        # 4. Compute diffs
        # Roles to add: in target, not in existing
        added_roles = [
            r for r_id, r in target_role_map.items()
            if r_id not in existing_role_map
        ]

        # Roles to remove: in existing, not in target, and not preserved
        removed_assignments = [
            a for r_id, a in existing_role_map.items()
            if r_id not in target_role_map and r_id not in preserved_role_ids
        ]
        removed_roles = [a.role for a in removed_assignments]

        # 5. Apply removal
        for a in removed_assignments:
            r = a.role
            cls.validate_role_removal_authority(actor, r, trusted_internal=trusted_internal)
            a.delete()
            # Audit removal
            AuditService.log_event(
                actor=actor if (actor and actor.is_authenticated) else None,
                action="user_role_removed",
                instance=user,
                module="accounts",
                object_type="UserRoleAssignment",
                object_id=str(user.pk),
                object_label=f"{user.email or user.phone} - {r.name}",
                before={'role_id': r.id, 'role_code': r.code, 'role_name': r.name},
                after={},
                request=request
            )
            log_audit(
                actor=actor if (actor and actor.is_authenticated) else None,
                action="user_role_removed",
                target=user,
                summary=f"Removed role '{r.name}' from user '{user.email or user.phone}'",
                ip=request.META.get('REMOTE_ADDR') if request else None
            )

        # 6. Apply addition
        for r in added_roles:
            UserRoleAssignment.objects.create(
                user=user,
                role=r,
                assigned_by=actor if (actor and actor.is_authenticated) else None
            )
            AuditService.log_event(
                actor=actor if (actor and actor.is_authenticated) else None,
                action="user_role_assigned",
                instance=user,
                module="accounts",
                object_type="UserRoleAssignment",
                object_id=str(user.pk),
                object_label=f"{user.email or user.phone} - {r.name}",
                before={},
                after={'role_id': r.id, 'role_code': r.code, 'role_name': r.name},
                request=request
            )
            log_audit(
                actor=actor if (actor and actor.is_authenticated) else None,
                action="user_role_assigned",
                target=user,
                summary=f"Assigned role '{r.name}' to user '{user.email or user.phone}'",
                ip=request.META.get('REMOTE_ADDR') if request else None
            )

        # 7. Update compatibility persona on CustomUser based on ALL active roles
        final_active_roles = list(
            Role.objects.filter(user_assignments__user=user, is_active=True).distinct()
        )
        compat_persona = cls.compute_compatibility_persona(final_active_roles)
        if user.role != compat_persona:
            user.role = compat_persona
            user.save(update_fields=['role'])

        # 8. Invalidate permission caches
        cls.invalidate_user_permissions(user)

        return added_roles, removed_roles


class RolePermissionAssignmentService:
    """
    Atomic permission matrix assignment and synchronization service for dynamic roles.
    - Validates role IDs and permission IDs server-side.
    - Enforces actor authority, effective permissions, and data-scope ceilings.
    - Protects system_owner and other protected roles.
    - Records before and after permission states in the audit system.
    - Invalidate all relevant PermissionEngine caches immediately.
    - Rolls back the complete update if any permission operation fails.
    """

    @classmethod
    def validate_perm_grant_authority(
        cls,
        actor,
        perm_codename: str,
        requested_scope: str = DataScope.GLOBAL,
        trusted_internal: bool = False
    ):
        """
        Enforces that non-superuser actor cannot grant permissions or scopes exceeding their authority.
        """
        if trusted_internal or getattr(actor, 'is_superuser', False):
            return

        if not actor or not actor.is_authenticated:
            raise PermissionDenied("Authentication required to assign role permissions.")

        actor_perms = PermissionEngine.get_user_resolved_permissions(actor)
        actor_p = actor_perms.get(perm_codename)

        # Check compatibility aliases if exact perm not in actor_p
        if not actor_p or not actor_p.get('granted'):
            if perm_codename.endswith('.add'):
                alt = perm_codename[:-4] + '.create'
                actor_p = actor_perms.get(alt)
            elif perm_codename.endswith('.update'):
                alt = perm_codename[:-7] + '.edit'
                actor_p = actor_perms.get(alt)

        if not actor_p or not actor_p.get('granted'):
            raise PermissionDenied(
                f"Privilege escalation: You cannot grant permission '{perm_codename}' because you do not hold this permission."
            )

        req_rank = SCOPE_HIERARCHY.get(requested_scope, 0)
        act_rank = SCOPE_HIERARCHY.get(actor_p.get('scope', DataScope.OWN), 0)
        if req_rank > act_rank:
            raise PermissionDenied(
                f"Privilege escalation: You cannot grant scope '{requested_scope}' for '{perm_codename}' which exceeds your effective scope '{actor_p.get('scope')}'."
            )

    @classmethod
    @transaction.atomic
    def sync_role_permissions(
        cls,
        *,
        role: Role,
        selections: Dict[str, Any],
        data_scopes: Optional[Dict[str, str]] = None,
        actor=None,
        request=None,
        trusted_internal: bool = False
    ) -> Tuple[int, int]:
        """
        Atomically synchronizes all permissions for a dynamic role.
        """
        from apps.accounts.rbac_registry import RBACRegistryService

        if not role or not role.pk:
            raise ValidationError("Valid persisted role is required.")

        # 1. System Owner protection
        if role.is_system_protected or role.code == 'system_owner':
            raise PermissionDenied("System Owner permissions are protected and cannot be modified.")

        # 2. Super Admin protection
        if role.code == 'super_admin' and not (getattr(actor, 'is_superuser', False) or trusted_internal):
            raise PermissionDenied("Only a System Owner (superuser) can configure Super Admin permissions.")

        # 3. Check general actor permission to edit roles
        if not (
            getattr(actor, 'is_superuser', False)
            or trusted_internal
            or getattr(actor, 'role', '') in ('admin', 'system_owner')
            or PermissionEngine.evaluate(actor, 'accounts.edit').allowed
        ):
            raise PermissionDenied("Insufficient RBAC permissions to administer system role permissions.")

        data_scopes = data_scopes or {}

        # 4. Map existing RolePermission records
        existing_rps = {
            rp.permission.codename: rp
            for rp in RolePermission.objects.filter(role=role).select_related('permission')
        }
        before_state = {
            code: {'granted': True, 'scope': rp.data_scope}
            for code, rp in existing_rps.items()
        }

        # 5. Build desired permissions map: { codename: scope }
        desired_perms: Dict[str, str] = {}

        for node_key, actions in selections.items():
            if isinstance(actions, dict):
                for act_code, is_checked in actions.items():
                    if act_code == 'all':
                        continue
                    if is_checked:
                        clean_key = (
                            node_key.replace('mod_', '')
                            .replace('sub_', '')
                            .replace('menu_', '')
                            .replace('smenu_', '')
                        )
                        codename = f"{clean_key}.{act_code}"
                        scope = data_scopes.get(clean_key, data_scopes.get(codename, DataScope.GLOBAL))
                        cls.validate_perm_grant_authority(actor, codename, scope, trusted_internal=trusted_internal)
                        desired_perms[codename] = scope
            elif isinstance(actions, bool):
                if actions:
                    codename = node_key
                    clean_key = (
                        codename.rsplit('.', 1)[0]
                        .replace('mod_', '')
                        .replace('sub_', '')
                        .replace('menu_', '')
                        .replace('smenu_', '')
                    )
                    scope = data_scopes.get(clean_key, data_scopes.get(codename, DataScope.GLOBAL))
                    cls.validate_perm_grant_authority(actor, codename, scope, trusted_internal=trusted_internal)
                    desired_perms[codename] = scope

        # 6. Apply deletions (permissions unselected)
        deleted_count = 0
        for code, rp in existing_rps.items():
            if code not in desired_perms:
                rp.delete()
                deleted_count += 1

        # 7. Apply additions and updates
        added_count = 0
        for code, scope in desired_perms.items():
            perm = RBACRegistryService.ensure_permission(code)
            rp, created = RolePermission.objects.get_or_create(
                role=role,
                permission=perm,
                defaults={'data_scope': scope}
            )
            if created:
                added_count += 1
            elif rp.data_scope != scope:
                rp.data_scope = scope
                rp.save(update_fields=['data_scope'])

        after_state = {
            code: {'granted': True, 'scope': scope}
            for code, scope in desired_perms.items()
        }

        # 8. Audit Logging
        summary = f"Updated permissions for role '{role.name}' ({role.code}): +{added_count}, -{deleted_count}"
        AuditService.log_event(
            actor=actor if (actor and actor.is_authenticated) else None,
            action="role_permissions_matrix_updated",
            instance=role,
            module="accounts",
            object_type="Role",
            object_id=str(role.pk),
            object_label=role.name,
            before=before_state,
            after=after_state,
            request=request
        )
        log_audit(
            actor=actor if (actor and actor.is_authenticated) else None,
            action="role_permissions_matrix_updated",
            target=role,
            summary=summary,
            ip=request.META.get('REMOTE_ADDR') if request else None
        )

        # 9. Invalidate PermissionEngine caches for all users with this role
        assigned_users = User.objects.filter(role_assignments__role=role)
        for u in assigned_users:
            RoleAssignmentService.invalidate_user_permissions(u)
            try:
                from apps.accounts.permissions import clear_user_perm_cache
                clear_user_perm_cache(u)
            except Exception:
                pass

        return added_count, deleted_count
