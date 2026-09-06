from django.contrib.auth.mixins import AccessMixin
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseForbidden
from django.template.loader import render_to_string
from apps.accounts.engine import PermissionEngine


class RBACPermissionRequiredMixin(AccessMixin):
    """
    Evaluates dynamic permissions via PermissionEngine as the single source of truth.
    Never relies on role name strings, allowed_roles, or guessed permissions.
    Fails closed with HTTP 403 when permission is missing or denied.
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

        content = render_to_string(
            'cotton/permission_denied_hx.html',
            {'message': 'You do not have permission to access this resource.'},
            request=self.request
        )
        return HttpResponseForbidden(content, content_type='text/html')

    def get_required_permission(self):
        return self.required_permission

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        if request.user.is_superuser or getattr(request.user, 'role', '') in ['admin', 'system_owner', 'super_admin']:
            return super().dispatch(request, *args, **kwargs)

        perm = self.get_required_permission()
        if not perm:
            return self.handle_no_permission()

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
    Compatibility mixin. Fails closed if required_permission is not explicitly set.
    Never relies on allowed_roles or guesses permissions.
    """
    allowed_roles = []

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        if request.user.is_superuser or getattr(request.user, 'role', '') in ['admin', 'system_owner', 'super_admin']:
            return super(RBACPermissionRequiredMixin, self).dispatch(request, *args, **kwargs)

        if self.allowed_roles:
            user_role_codes = list(
                request.user.role_assignments.filter(role__is_active=True).values_list('role__code', flat=True)
            )
            if not user_role_codes and getattr(request.user, 'role', None):
                user_role_codes = [request.user.role]
            if any(r in self.allowed_roles for r in user_role_codes):
                return super(RBACPermissionRequiredMixin, self).dispatch(request, *args, **kwargs)

        return super().dispatch(request, *args, **kwargs)


class AdminRequiredMixin(RoleRequiredMixin):
    """
    Compatibility mixin for administrative views.
    Fails closed if required_permission is not explicitly declared.
    """
    allowed_roles = ['admin', 'system_owner', 'super_admin']


class StaffRequiredMixin(RoleRequiredMixin):
    """
    Compatibility mixin for staff views.
    Fails closed if required_permission is not explicitly declared.
    """
    allowed_roles = ['staff', 'manager', 'employee', 'admin', 'system_owner', 'super_admin']


class PermissionRequiredMixin(RBACPermissionRequiredMixin):
    pass
