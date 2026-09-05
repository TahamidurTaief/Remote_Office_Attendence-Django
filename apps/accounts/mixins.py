from django.contrib.auth.mixins import AccessMixin
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseForbidden
from django.template.loader import render_to_string
from apps.accounts.engine import PermissionEngine


class RBACPermissionRequiredMixin(AccessMixin):
    """
    Evaluates dynamic permissions via PermissionEngine as the single source of truth.
    Never relies on role name strings.
    Fails closed with HTTP 403 and reusable Cotton alert partial on HTMX requests.
    Redirects unauthenticated users to login (302).
    """
    required_permission = None
    required_scope = None
    action_type = 'view'

    def handle_no_permission(self):
        if not self.request.user.is_authenticated:
            return super().handle_no_permission()

        if self.request.headers.get('HX-Request'):
            content = render_to_string(
                'cotton/permission_denied_hx.html',
                {'message': 'You do not have permission to perform this action.'},
                request=self.request
            )
            response = HttpResponseForbidden(content, content_type='text/html')
            response['HX-Reswap'] = 'none'
            return response

        if self.request.content_type == 'application/json' or self.request.headers.get('Accept') == 'application/json':
            from django.http import JsonResponse
            return JsonResponse({'status': 'error', 'message': 'You do not have permission to perform this action.'}, status=403)

        from django.shortcuts import redirect
        return redirect('/staff/home/')

    def get_required_permission(self):
        return self.required_permission

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        if request.user.is_superuser:
            return super().dispatch(request, *args, **kwargs)

        perm = self.get_required_permission()
        if perm:
            eval_res = PermissionEngine.evaluate(
                user=request.user,
                codename=perm,
                required_scope=self.required_scope,
                action_type=self.action_type
            )
            if not eval_res.allowed:
                return self.handle_no_permission()
            request.resolved_permission_result = eval_res

        return super().dispatch(request, *args, **kwargs)


class RoleRequiredMixin(RBACPermissionRequiredMixin):
    """
    Compatibility mixin replacing legacy role name checks with PermissionEngine resolution.
    Never authorizes users based on CustomUser.role string.
    """
    allowed_roles = []

    def get_required_permission(self):
        if self.required_permission:
            return self.required_permission
        if hasattr(self, 'model') and self.model:
            return f"{self.model._meta.app_label}.view"
        if any(r in ['admin', 'system_owner', 'super_admin'] for r in self.allowed_roles):
            return 'dashboard.view'
        return 'attendance.view'


class AdminRequiredMixin(RBACPermissionRequiredMixin):
    """
    Enforces administrative permissions dynamically via PermissionEngine.
    Never authorizes based on role string.
    """
    default_permission = 'dashboard.view'

    def get_required_permission(self):
        if self.required_permission:
            return self.required_permission
        if hasattr(self, 'model') and self.model:
            return f"{self.model._meta.app_label}.view"
        return self.default_permission


class StaffRequiredMixin(RBACPermissionRequiredMixin):
    """
    Enforces staff self-service permissions dynamically via PermissionEngine.
    Never authorizes based on role string.
    """
    default_permission = 'attendance.view'

    def get_required_permission(self):
        if self.required_permission:
            return self.required_permission
        if hasattr(self, 'model') and self.model:
            return f"{self.model._meta.app_label}.view"
        return self.default_permission


class PermissionRequiredMixin(RBACPermissionRequiredMixin):
    pass
