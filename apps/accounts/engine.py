import logging
from django.db.models import Q
from apps.accounts.models import (
    DataScope, Permission, RolePermission, UserRoleAssignment,
    UserPermissionOverride, PermissionDependency, SecurityPolicy, ApprovalPolicy
)

logger = logging.getLogger(__name__)

SCOPE_HIERARCHY = {
    DataScope.GLOBAL: 6,
    DataScope.COMPANY: 5,
    DataScope.BRANCH: 4,
    DataScope.DEPARTMENT: 3,
    DataScope.TEAM: 2,
    DataScope.OWN: 1,
}


class PermissionResolutionResult:
    def __init__(self, allowed=False, reason="", data_scope=DataScope.OWN, read_only=False, mfa_required=False, needs_approval=False):
        self.allowed = allowed
        self.reason = reason
        self.data_scope = data_scope
        self.read_only = read_only
        self.mfa_required = mfa_required
        self.needs_approval = needs_approval

    def __bool__(self):
        return self.allowed


class PermissionEngine:

    @classmethod
    def get_user_resolved_permissions(cls, user):
        """
        Resolves union of permissions and effective data scopes for user.
        Uses request-scoped caching on user object.
        Returns dict: { 'module.action': {'permission': PermObj, 'scope': DataScope, 'granted': Bool} }
        """
        if not user or not user.is_authenticated:
            return {}

        if hasattr(user, '_resolved_permissions_cache'):
            return user._resolved_permissions_cache

        resolved = {}

        # Superuser shortcut
        if user.is_superuser:
            all_perms = Permission.objects.select_related('module', 'action').filter(module__is_active=True)
            for perm in all_perms:
                resolved[perm.codename] = {
                    'permission': perm,
                    'scope': DataScope.GLOBAL,
                    'granted': True
                }
            user._resolved_permissions_cache = resolved
            return resolved

        # 1. Fetch assigned active roles
        assigned_role_ids = list(
            UserRoleAssignment.objects.filter(user=user, role__is_active=True).values_list('role_id', flat=True)
        )

        # 2. Fetch role permissions
        role_perms = RolePermission.objects.filter(
            role_id__in=assigned_role_ids,
            permission__module__is_active=True
        ).select_related('permission', 'permission__module', 'permission__action')

        for rp in role_perms:
            code = rp.permission.codename
            scope = rp.data_scope
            if code not in resolved:
                resolved[code] = {
                    'permission': rp.permission,
                    'scope': scope,
                    'granted': True
                }
            else:
                # Union scope: keep highest scope rank
                curr_rank = SCOPE_HIERARCHY.get(resolved[code]['scope'], 0)
                new_rank = SCOPE_HIERARCHY.get(scope, 0)
                if new_rank > curr_rank:
                    resolved[code]['scope'] = scope

        # 3. Fetch direct user overrides
        overrides = UserPermissionOverride.objects.filter(
            user=user,
            permission__module__is_active=True
        ).select_related('permission')

        for ov in overrides:
            code = ov.permission.codename
            if not ov.is_granted:
                # Direct Revoke
                resolved[code] = {
                    'permission': ov.permission,
                    'scope': DataScope.OWN,
                    'granted': False
                }
            else:
                # Direct Grant
                scope = ov.data_scope or (resolved[code]['scope'] if code in resolved else DataScope.OWN)
                resolved[code] = {
                    'permission': ov.permission,
                    'scope': scope,
                    'granted': True
                }

        user._resolved_permissions_cache = resolved
        return resolved

    @classmethod
    def evaluate(cls, user, codename, required_scope=None, action_type='view'):
        """
        Executes full 9-layer resolution order for a given action.
        """
        if not user or not user.is_authenticated:
            return PermissionResolutionResult(allowed=False, reason="User is not authenticated.")

        # Superuser bypass
        if user.is_superuser:
            return PermissionResolutionResult(allowed=True, data_scope=DataScope.GLOBAL)

        # -------------------------------------------------------------
        # Layer 1: Employee Status Check
        # -------------------------------------------------------------
        emp_status = None
        emp_master = getattr(user, 'employee_master', None)
        emp_profile = getattr(user, 'employee_profile', None)

        if emp_master:
            emp_status = getattr(emp_master, 'status', None)
        elif emp_profile:
            emp_status = 'active' if emp_profile.is_active else 'suspended'

        ALLOWED_ACTIVE_STATES = ('active', 'probation', 'confirmed', 'transferred', 'promoted', 'demoted', 'notice_period')
        if emp_status and emp_status not in ALLOWED_ACTIVE_STATES and emp_status != 'archived':
            return PermissionResolutionResult(allowed=False, reason=f"Employee status '{emp_status}' is blocked from system access.")

        read_only = (emp_status == 'archived')
        if read_only and action_type in ('create', 'edit', 'delete', 'archive'):
            return PermissionResolutionResult(allowed=False, reason="Archived employee accounts are Read-Only.", read_only=True)

        # -------------------------------------------------------------
        # Layer 2-5: Permission & Dependency Check
        # -------------------------------------------------------------
        resolved_map = cls.get_user_resolved_permissions(user)
        perm_entry = resolved_map.get(codename)

        if not perm_entry or not perm_entry['granted']:
            return PermissionResolutionResult(allowed=False, reason=f"Missing required permission '{codename}'.", read_only=read_only)

        effective_scope = perm_entry['scope']

        # Verify prerequisite permission dependencies
        perm_obj = perm_entry['permission']
        req_deps = PermissionDependency.objects.filter(permission=perm_obj).select_related('requires_permission')
        for dep in req_deps:
            req_code = dep.requires_permission.codename
            req_entry = resolved_map.get(req_code)
            if not req_entry or not req_entry['granted']:
                return PermissionResolutionResult(
                    allowed=False,
                    reason=f"Permission '{codename}' requires prerequisite '{req_code}'.",
                    read_only=read_only
                )

        # -------------------------------------------------------------
        # Layer 6: Data Scope Comparison
        # -------------------------------------------------------------
        if required_scope:
            eff_rank = SCOPE_HIERARCHY.get(effective_scope, 0)
            req_rank = SCOPE_HIERARCHY.get(required_scope, 0)
            if eff_rank < req_rank:
                return PermissionResolutionResult(
                    allowed=False,
                    reason=f"Insufficient data scope for '{codename}'. Effective: {effective_scope}, Required: {required_scope}.",
                    data_scope=effective_scope,
                    read_only=read_only
                )

        # -------------------------------------------------------------
        # Layer 7: Security Policy Check
        # -------------------------------------------------------------
        mfa_required = False
        assigned_role_codes = list(
            UserRoleAssignment.objects.filter(user=user, role__is_active=True).values_list('role__code', flat=True)
        )
        sec_policies = SecurityPolicy.objects.filter(role__in=assigned_role_codes)
        if any(p.mfa_required for p in sec_policies):
            mfa_required = True

        # -------------------------------------------------------------
        # Layer 8: Approval Policy Check
        # -------------------------------------------------------------
        needs_approval = False
        if ApprovalPolicy.objects.filter(permission=perm_obj, is_active=True).exists():
            if action_type in ('release', 'approve', 'delete', 'export'):
                needs_approval = True

        # -------------------------------------------------------------
        # Layer 9: Business Validation Passed
        # -------------------------------------------------------------
        return PermissionResolutionResult(
            allowed=True,
            data_scope=effective_scope,
            read_only=read_only,
            mfa_required=mfa_required,
            needs_approval=needs_approval
        )

    @classmethod
    def filter_by_data_scope(cls, user, queryset, codename, employee_field='employee', branch_field='branch', dept_field='department'):
        """
        Applies automated data scope filtering to QuerySet based on user's resolved scope.
        """
        if not user or not user.is_authenticated:
            return queryset.none()

        if user.is_superuser:
            return queryset

        eval_res = cls.evaluate(user, codename)
        if not eval_res.allowed:
            return queryset.none()

        scope = eval_res.data_scope

        if scope in (DataScope.GLOBAL, DataScope.COMPANY):
            return queryset

        emp_master = getattr(user, 'employee_master', None)
        emp_profile = getattr(user, 'employee_profile', None)

        if scope == DataScope.OWN:
            q_filter = Q()
            if emp_master:
                q_filter |= Q(**{f"{employee_field}__master_employee": emp_master})
            if emp_profile:
                q_filter |= Q(**{f"{employee_field}": emp_profile})
            return queryset.filter(q_filter)

        if scope == DataScope.TEAM:
            q_filter = Q()
            if emp_master:
                # Direct reports
                q_filter |= Q(**{f"{employee_field}__master_employee__reporting_manager": emp_master})
                q_filter |= Q(**{f"{employee_field}__master_employee": emp_master})
            return queryset.filter(q_filter)

        if scope == DataScope.BRANCH:
            user_branch = None
            if emp_master and emp_master.branch:
                user_branch = emp_master.branch
            elif emp_profile and emp_profile.branch:
                user_branch = emp_profile.branch

            if user_branch:
                return queryset.filter(**{f"{branch_field}": user_branch})
            return queryset.none()

        if scope == DataScope.DEPARTMENT:
            user_dept = None
            if emp_master and emp_master.department:
                user_dept = emp_master.department

            if user_dept:
                return queryset.filter(**{f"{dept_field}": user_dept})
            return queryset.none()

        return queryset
