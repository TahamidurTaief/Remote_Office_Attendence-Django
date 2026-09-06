import logging
from typing import Optional, Set
from django.core.exceptions import PermissionDenied
from apps.tenants.models import Tenant, TenantMembership

logger = logging.getLogger(__name__)


class AIPayrollPermissionService:
    """
    Tenant-scoped, default-denied, auditable permission authority
    for AI agents and automated assistants operating on payroll.
    Separates capabilities into:
      - read: inspect current active configuration and history
      - simulate: zero-side-effect calculation dry-runs
      - propose: draft provisional changes for human review
      - approve: workflow stage sign-off (requires elevated human delegation)
      - execute: administrative execution (strictly restricted; autonomous salary distribution prohibited)
    """

    PERM_READ = 'payroll.configuration.read'
    PERM_SIMULATE = 'payroll.configuration.simulate'
    PERM_PROPOSE = 'payroll.configuration.propose'
    PERM_APPROVE = 'payroll.configuration.approve'
    PERM_EXECUTE = 'payroll.configuration.execute'

    ALL_PERMISSIONS = {
        PERM_READ,
        PERM_SIMULATE,
        PERM_PROPOSE,
        PERM_APPROVE,
        PERM_EXECUTE,
    }

    # Autonomous execution of financial disbursements is strictly blocked
    BLOCKED_AUTONOMOUS_ACTIONS = {
        'payroll.distribute_salary',
        'payroll.execute_disbursement',
        'payroll.autonomous_distribution',
    }

    @classmethod
    def validate_tenant_boundary(
        cls,
        user,
        target_tenant: Optional[Tenant],
        request_tenant: Optional[Tenant] = None
    ) -> bool:
        """
        Guards against forged tenant IDs or cross-tenant context bleeding.
        Logs a security audit event on violation and raises PermissionDenied.
        """
        if not target_tenant:
            cls._log_security_event(
                user=user,
                action='tenant_boundary_missing',
                reason="Attempted payroll action with null target tenant."
            )
            raise PermissionDenied("Target tenant context is missing.")

        if request_tenant and request_tenant.id != target_tenant.id:
            cls._log_security_event(
                user=user,
                action='forged_tenant_access_attempt',
                reason=f"Security violation: Request tenant ID {request_tenant.id} does not match target tenant ID {target_tenant.id}."
            )
            raise PermissionDenied("Cross-tenant isolation violation: forged tenant context detected.")

        # Superusers bypass membership check, but tenant boundary match is still enforced above
        if getattr(user, 'is_superuser', False):
            return True

        # Check tenant membership
        has_membership = TenantMembership.objects.filter(
            tenant=target_tenant,
            user=user,
            is_active=True
        ).exists()

        if not has_membership:
            cls._log_security_event(
                user=user,
                action='unauthorized_tenant_access',
                reason=f"User {getattr(user, 'email', str(user))} is not an active member of tenant {target_tenant.name}."
            )
            raise PermissionDenied("Unauthorized: You are not a member of this tenant.")

        return True

    @classmethod
    def check_permission(
        cls,
        user,
        target_tenant: Tenant,
        action: str,
        request_tenant: Optional[Tenant] = None
    ) -> bool:
        """
        Evaluates whether an actor/AI possesses the requested permission.
        Strictly default-denied.
        """
        # Block autonomous salary distribution outright
        if action in cls.BLOCKED_AUTONOMOUS_ACTIONS:
            cls._log_security_event(
                user=user,
                action='blocked_autonomous_action_attempt',
                reason=f"Attempted blocked autonomous action: {action}. Autonomous salary distribution is disabled."
            )
            return False

        # Validate tenant boundary first
        try:
            cls.validate_tenant_boundary(user, target_tenant, request_tenant)
        except PermissionDenied:
            return False

        if not user or not getattr(user, 'is_authenticated', False):
            return False

        # Superuser has operational access except for blocked autonomous actions
        if getattr(user, 'is_superuser', False):
            return True

        # Check explicit permissions or role capabilities
        user_perms = cls.get_user_ai_permissions(user)

        # Standard action codename or specific AI perm
        has_perm = (action in user_perms)

        # Also fallback to check PermissionEngine for standard accounts/payroll permissions
        if not has_perm:
            from apps.accounts.engine import PermissionEngine
            # Map configuration actions to RBAC codenames
            action_map = {
                cls.PERM_READ: 'payroll.view',
                cls.PERM_SIMULATE: 'payroll.view',
                cls.PERM_PROPOSE: 'payroll.edit',
                cls.PERM_APPROVE: 'payroll.approve',
                cls.PERM_EXECUTE: 'payroll.update',
            }
            mapped_perm = action_map.get(action)
            if mapped_perm:
                res = PermissionEngine.evaluate(user=user, codename=mapped_perm)
                if res.allowed:
                    has_perm = True

        if not has_perm:
            cls._log_security_event(
                user=user,
                action='ai_permission_denied',
                reason=f"User/agent lacked required permission: {action} on tenant {target_tenant.name}."
            )

        return has_perm

    @classmethod
    def assert_permission(
        cls,
        user,
        target_tenant: Tenant,
        action: str,
        request_tenant: Optional[Tenant] = None
    ):
        """Asserts permission, raising PermissionDenied with audit logging if not granted."""
        allowed = cls.check_permission(user, target_tenant, action, request_tenant)
        if not allowed:
            raise PermissionDenied(f"Permission denied: Missing required permission '{action}' on this tenant.")

    @classmethod
    def get_user_ai_permissions(cls, user) -> Set[str]:
        """
        Retrieves the granted AI permissions set for the user/agent.
        Default-denied: returns only explicitly granted permissions or role mappings.
        """
        perms = set()
        if not user or not getattr(user, 'is_authenticated', False):
            return perms

        # Check user's granted permissions via overrides
        if hasattr(user, 'permission_overrides'):
            for po in user.permission_overrides.filter(is_granted=True).select_related('permission'):
                perms.add(po.permission.codename)

        # Check roles
        role = getattr(user, 'role', '')
        if role in ['admin', 'manager', 'finance', 'accounts']:
            perms.add(cls.PERM_READ)
            perms.add(cls.PERM_SIMULATE)
            perms.add(cls.PERM_PROPOSE)
            if role in ['admin', 'finance']:
                perms.add(cls.PERM_APPROVE)
                # Note: PERM_EXECUTE for configuration editing, NOT autonomous distribution
                perms.add(cls.PERM_EXECUTE)

        return perms

    @classmethod
    def _log_security_event(cls, user, action: str, reason: str):
        """Logs security audit violations into AuditService."""
        try:
            from apps.audit.services import AuditService
            AuditService.log_event(
                actor=user if getattr(user, 'is_authenticated', False) else None,
                action=action,
                module='payroll',
                object_type='AIPayrollPermission',
                object_id='security_guard',
                object_label='AI Security Guard',
                reason=reason
            )
        except Exception as e:
            logger.warning("Failed to record security audit log: %s", e)
