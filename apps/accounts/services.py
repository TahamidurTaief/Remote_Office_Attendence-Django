import logging
from typing import Iterable, Optional, Set, Tuple, List
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
