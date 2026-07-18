from django.shortcuts import redirect
from django.contrib.auth.mixins import AccessMixin

class RoleRequiredMixin(AccessMixin):
    allowed_roles = []

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
            
        if request.user.role not in self.allowed_roles:
            # Redirect to their appropriate dashboard if wrong role
            if request.user.role == 'admin':
                return redirect('/admin-panel/dashboard/')
            elif request.user.role in ['staff', 'manager']:
                return redirect('/staff/home/')
            return redirect('/login/')
            
        return super().dispatch(request, *args, **kwargs)

class AdminRequiredMixin(RoleRequiredMixin):
    allowed_roles = ['admin']

class StaffRequiredMixin(RoleRequiredMixin):
    allowed_roles = ['staff', 'manager']


class PermissionRequiredMixin(StaffRequiredMixin):
    required_permission = None

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
            
        if request.user.role not in self.allowed_roles:
            if request.user.role == 'admin':
                return redirect('/admin-panel/dashboard/')
            elif request.user.role in ['staff', 'manager']:
                return redirect('/staff/home/')
            return redirect('/login/')

        if self.required_permission and not request.user.has_perm(self.required_permission):
            return self.handle_no_permission()

        return super().dispatch(request, *args, **kwargs)
