from django.utils.deprecation import MiddlewareMixin
from .context import set_current_tenant, get_user_tenant, clear_current_tenant


class TenantMiddleware(MiddlewareMixin):
    """
    Middleware that resolves the current tenant based on the authenticated user,
    sets request.tenant, and configures the thread-local context.
    """
    def process_request(self, request):
        if hasattr(request, 'user') and request.user.is_authenticated:
            tenant = get_user_tenant(request.user)
        else:
            from .context import get_default_tenant
            tenant = get_default_tenant()

        request.tenant = tenant
        set_current_tenant(tenant)

    def process_response(self, request, response):
        clear_current_tenant()
        return response

    def process_exception(self, request, exception):
        clear_current_tenant()
        return None
