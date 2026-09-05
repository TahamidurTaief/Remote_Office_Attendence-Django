from django.contrib.auth.mixins import AccessMixin
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseForbidden
from apps.accounts.engine import PermissionEngine


class RBACPermissionRequiredMixin(AccessMixin):
    required_permission = None
    required_scope = None
    action_type = 'view'

    def handle_no_permission(self):
        if not self.request.user.is_authenticated:
            return super().handle_no_permission()
        if self.request.headers.get('HX-Request'):
            response = HttpResponseForbidden("Permission denied.")
            response['HX-Reswap'] = 'none'
            return response
        raise PermissionDenied("You do not have permission to perform this action.")

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

    def handle_no_permission(self):
        if not self.request.user.is_authenticated:
            return super().handle_no_permission()
        if self.request.headers.get('HX-Request'):
            response = HttpResponseForbidden("Permission denied.")
            response['HX-Reswap'] = 'none'
            return response
        user_role_codes = list(
            self.request.user.role_assignments.filter(role__is_active=True).values_list('role__code', flat=True)
        )
        if not user_role_codes and getattr(self.request.user, 'role', None):
            user_role_codes = [self.request.user.role]
        from django.shortcuts import redirect
        if any(r in ['admin', 'system_owner', 'super_admin'] for r in user_role_codes):
            return redirect('/admin-panel/dashboard/')
        elif any(r in ['staff', 'manager', 'employee'] for r in user_role_codes):
            return redirect('/staff/home/')
        raise PermissionDenied("You do not have permission to access this resource.")

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        if request.user.is_superuser:
            return super().dispatch(request, *args, **kwargs)

        # Resolve active dynamic roles
        user_role_codes = list(
            request.user.role_assignments.filter(role__is_active=True).values_list('role__code', flat=True)
        )
        if not user_role_codes and getattr(request.user, 'role', None):
            user_role_codes = [request.user.role]

        if not any(r in self.allowed_roles for r in user_role_codes):
            return self.handle_no_permission()

        return super().dispatch(request, *args, **kwargs)


class AdminRequiredMixin(RoleRequiredMixin):
    allowed_roles = ['admin', 'system_owner', 'super_admin']


class StaffRequiredMixin(RoleRequiredMixin):
    allowed_roles = ['staff', 'manager', 'employee', 'admin', 'system_owner', 'super_admin']


class PermissionRequiredMixin(RBACPermissionRequiredMixin):
    pass
