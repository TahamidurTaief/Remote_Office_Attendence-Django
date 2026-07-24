from django.shortcuts import redirect
from django.contrib.auth.mixins import AccessMixin
from apps.accounts.engine import PermissionEngine


class RBACPermissionRequiredMixin(AccessMixin):
    required_permission = None
    required_scope = None
    action_type = 'view'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        if self.required_permission:
            eval_res = PermissionEngine.evaluate(
                user=request.user,
                codename=self.required_permission,
                required_scope=self.required_scope,
                action_type=self.action_type
            )

            if not eval_res.allowed:
                return self.handle_no_permission()

            request.resolved_permission_result = eval_res

        return super().dispatch(request, *args, **kwargs)


class RoleRequiredMixin(AccessMixin):
    allowed_roles = []

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        if request.user.is_superuser:
            return super().dispatch(request, *args, **kwargs)

        # Resolve dynamic roles or fallback to user.role property
        user_role_codes = [assignment.role.code for assignment in request.user.role_assignments.select_related('role').filter(role__is_active=True)]
        if not user_role_codes and hasattr(request.user, 'role'):
            user_role_codes = [request.user.role]

        if not any(r in self.allowed_roles for r in user_role_codes):
            if 'admin' in user_role_codes or 'system_owner' in user_role_codes:
                return redirect('/admin-panel/dashboard/')
            elif any(r in ['staff', 'manager', 'employee'] for r in user_role_codes):
                return redirect('/staff/home/')
            return redirect('/login/')

        return super().dispatch(request, *args, **kwargs)


class AdminRequiredMixin(RoleRequiredMixin):
    allowed_roles = ['admin', 'system_owner']


class StaffRequiredMixin(RoleRequiredMixin):
    allowed_roles = ['staff', 'manager', 'employee', 'admin', 'system_owner']


class PermissionRequiredMixin(RBACPermissionRequiredMixin):
    pass
