import logging
from django.db.models import Q
from apps.accounts.models import (
    DataScope, Role, Permission, RolePermission, UserRoleAssignment,
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
    def invalidate_user_cache(cls, user):
        """
        Invalidates cached permissions on the user instance and distributed cache.
        """
        if not user:
            return
        for attr in ('_resolved_permissions_cache', '_effective_perms', '_has_perm_cache'):
            if hasattr(user, attr):
                try:
                    delattr(user, attr)
                except Exception:
                    pass
        user_id = getattr(user, 'pk', None) or getattr(user, 'id', None)
        if user_id:
            try:
                from django.core.cache import cache
                cache.delete(f"user_permissions_{user_id}")
                cache.delete(f"rbac_user_perms_{user_id}")
            except Exception:
                pass

    @classmethod
    def get_user_resolved_permissions(cls, user):
        """
        Resolves union of permissions and effective data scopes for user.
        Uses request-scoped caching on user object and Django cache.
        Returns dict: { 'module.action': {'permission': PermObj, 'scope': DataScope, 'granted': Bool} }
        """
        if not user or not user.is_authenticated:
            return {}

        if hasattr(user, '_resolved_permissions_cache'):
            return user._resolved_permissions_cache

        user_id = getattr(user, 'pk', None) or getattr(user, 'id', None)
        cache_key = f"rbac_user_perms_{user_id}" if user_id else None
        if cache_key:
            try:
                from django.core.cache import cache
                cached_perms = cache.get(cache_key)
                if cached_perms is not None:
                    user._resolved_permissions_cache = cached_perms
                    return cached_perms
            except Exception:
                pass

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
            if cache_key:
                try:
                    from django.core.cache import cache
                    cache.set(cache_key, resolved, timeout=3600)
                except Exception:
                    pass
            return resolved

        # 1. Fetch assigned active roles
        assigned_role_ids = list(
            UserRoleAssignment.objects.filter(user=user, role__is_active=True).values_list('role_id', flat=True)
        )
        if not assigned_role_ids and getattr(user, 'role', None):
            if not Permission.objects.exists():
                try:
                    from apps.accounts.rbac_registry import RBACRegistryService
                    RBACRegistryService.sync_database()
                except Exception:
                    pass
            role_obj = Role.objects.filter(code=user.role, is_active=True).first()
            if not role_obj:
                role_obj = Role.objects.create(
                    code=user.role,
                    name=getattr(user, 'get_role_display', lambda: user.role.capitalize())(),
                    is_active=True
                )
            UserRoleAssignment.objects.get_or_create(user=user, role=role_obj)
            assigned_role_ids = [role_obj.id]

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
                # Direct Revoke / Explicit denial strictly overrides every role grant
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
        if cache_key:
            try:
                from django.core.cache import cache
                cache.set(cache_key, resolved, timeout=3600)
            except Exception:
                pass
        return resolved

    @classmethod
    def evaluate(cls, user, codename, required_scope=None, action_type='view'):
        """
        Executes full 9-layer resolution order for a given action.
        Independent action evaluation: treats add, edit, update, delete, view, approve, export independently.
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
            if getattr(emp_master, 'is_trashed', False):
                return PermissionResolutionResult(allowed=False, reason="Trashed employee accounts are blocked from system access.")
        elif emp_profile:
            emp_status = 'active' if emp_profile.is_active else 'suspended'

        ALLOWED_ACTIVE_STATES = ('active', 'probation', 'confirmed', 'transferred', 'promoted', 'demoted', 'notice_period')
        if emp_status and emp_status not in ALLOWED_ACTIVE_STATES and emp_status != 'archived':
            return PermissionResolutionResult(allowed=False, reason=f"Employee status '{emp_status}' is blocked from system access.")

        read_only = (emp_status == 'archived')
        if read_only and action_type in ('create', 'add', 'edit', 'update', 'delete', 'archive'):
            return PermissionResolutionResult(allowed=False, reason="Archived employee accounts are Read-Only.", read_only=True)

        # -------------------------------------------------------------
        # Layer 2-5: Permission & Dependency Check
        # -------------------------------------------------------------
        resolved_map = cls.get_user_resolved_permissions(user)
        perm_entry = resolved_map.get(codename)

        if perm_entry is not None and not perm_entry.get('granted'):
            # Explicitly revoked or denied
            return PermissionResolutionResult(allowed=False, reason=f"Permission '{codename}' is explicitly revoked.", read_only=read_only)

        if not perm_entry or not perm_entry.get('granted'):
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
    def check_object_scope(cls, user, obj, codename=None, action_type='view'):
        """
        Validates whether user has authorized object-level data scope to access obj.
        Returns True if allowed, False otherwise.
        """
        if not user or not user.is_authenticated:
            return False

        if user.is_superuser:
            return True

        # If codename provided, evaluate permission first
        if codename:
            eval_res = cls.evaluate(user, codename, action_type=action_type)
            if not eval_res.allowed:
                return False
            scope = eval_res.data_scope
        else:
            scope = DataScope.GLOBAL

        if scope in (DataScope.GLOBAL, DataScope.COMPANY):
            return True

        emp_master = getattr(user, 'employee_master', None)
        emp_profile = getattr(user, 'employee_profile', None)

        if scope == DataScope.OWN:
            if hasattr(obj, 'user_id') and obj.user_id == user.id:
                return True
            if hasattr(obj, 'user') and obj.user == user:
                return True
            if hasattr(obj, 'created_by_id') and obj.created_by_id == user.id:
                return True
            if emp_master and hasattr(obj, 'employee') and getattr(obj.employee, 'master_employee_id', None) == emp_master.id:
                return True
            if emp_profile and hasattr(obj, 'employee') and (obj.employee == emp_profile or getattr(obj.employee, 'id', None) == emp_profile.id):
                return True
            if emp_profile and hasattr(obj, 'responsible_person') and obj.responsible_person == emp_profile:
                return True
            return False

        if scope == DataScope.BRANCH:
            user_branch = getattr(emp_master, 'branch', None) or getattr(emp_profile, 'branch', None)
            if not user_branch:
                return False

            obj_branch = None
            if hasattr(obj, 'branch'):
                obj_branch = obj.branch
            elif hasattr(obj, 'project') and hasattr(obj.project, 'branch'):
                obj_branch = obj.project.branch
            elif hasattr(obj, 'employee') and hasattr(obj.employee, 'branch'):
                obj_branch = obj.employee.branch
            elif hasattr(obj, 'user') and hasattr(obj.user, 'employee_profile') and obj.user.employee_profile:
                obj_branch = obj.user.employee_profile.branch

            return obj_branch == user_branch

        if scope == DataScope.DEPARTMENT:
            user_dept = getattr(emp_master, 'department', None)
            if not user_dept:
                return False
            obj_dept = getattr(obj, 'department', None)
            return obj_dept == user_dept

        if scope == DataScope.TEAM:
            if not emp_master:
                return False
            from apps.employees.hierarchy_services import OrgHierarchyService
            subordinate_ids = list(OrgHierarchyService.get_all_subordinates(emp_master).values_list('id', flat=True))
            subordinate_ids.append(emp_master.id)
            if hasattr(obj, 'employee') and hasattr(obj.employee, 'master_employee_id'):
                return obj.employee.master_employee_id in subordinate_ids
            return False

        return False

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

            # Check if model itself is Employee or has ownership fields
            model_name = getattr(queryset.model, '__name__', '')
            if model_name == 'EmployeeMaster' and emp_master:
                q_filter |= Q(pk=emp_master.pk)
            elif model_name == 'EmployeeProfile' and emp_profile:
                q_filter |= Q(pk=emp_profile.pk)

            field_names = {f.name for f in queryset.model._meta.get_fields()}
            if 'created_by' in field_names and not (emp_master or emp_profile):
                q_filter |= Q(created_by=user)
            elif 'user' in field_names and not (emp_master or emp_profile):
                q_filter |= Q(user=user)

            return queryset.filter(q_filter) if q_filter else queryset.none()

        if scope == DataScope.TEAM:
            q_filter = Q()
            if emp_master:
                from apps.employees.hierarchy_services import OrgHierarchyService
                subordinate_ids = list(OrgHierarchyService.get_all_subordinates(emp_master).values_list('id', flat=True))
                subordinate_ids.append(emp_master.id)
                q_filter |= Q(**{f"{employee_field}__master_employee_id__in": subordinate_ids})
            return queryset.filter(q_filter) if q_filter else queryset.none()

        if scope == DataScope.BRANCH:
            user_branch = None
            if emp_master and emp_master.branch:
                user_branch = emp_master.branch
            elif emp_profile and emp_profile.branch:
                user_branch = emp_profile.branch

            if user_branch:
                if getattr(queryset.model, '__name__', '') == 'Branch':
                    return queryset.filter(pk=user_branch.pk)
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

    @classmethod
    def get_scoped_object_or_404(cls, model_or_qs, user, codename, pk, action_type='view', branch_field='branch', employee_field='employee', dept_field='department'):
        """
        Retrieves an individual object filtered by the user's authorized data scope,
        or raises Http404 / PermissionDenied.
        """
        from django.shortcuts import get_object_or_404
        from django.db.models import QuerySet
        from django.core.exceptions import PermissionDenied

        eval_res = cls.evaluate(user, codename, action_type=action_type)
        if not eval_res.allowed:
            raise PermissionDenied(f"Permission denied for {codename}: {eval_res.reason}")

        qs = model_or_qs if isinstance(model_or_qs, QuerySet) else model_or_qs.objects.all()
        scoped_qs = cls.filter_by_data_scope(
            user=user,
            queryset=qs,
            codename=codename,
            employee_field=employee_field,
            branch_field=branch_field,
            dept_field=dept_field
        )
        return get_object_or_404(scoped_qs, pk=pk)
