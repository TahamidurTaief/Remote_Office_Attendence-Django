import contextvars
from django.conf import settings


_tenant_context = contextvars.ContextVar('current_tenant', default=None)


def set_current_tenant(tenant):
    """Sets the current tenant for the active context/thread."""
    _tenant_context.set(tenant)


def clear_current_tenant():
    """Clears the current tenant from the active context/thread."""
    _tenant_context.set(None)


def get_default_tenant():
    """
    Resolves the default tenant using the DEFAULT_TENANT_SLUG setting.
    Does NOT silently create tenants.
    """
    from .models import Tenant
    default_slug = getattr(settings, 'DEFAULT_TENANT_SLUG', 'signtech')
    try:
        return Tenant.objects.get(slug=default_slug, status='active')
    except Tenant.DoesNotExist:
        # Return first active tenant if default slug doesn't exist or is inactive
        return Tenant.objects.filter(status='active').first()


def get_user_tenant(user):
    """
    Resolves the tenant for a given user.
    In the current single-tenant runtime, resolves to the default tenant.
    """
    from .models import TenantMembership
    if not user or not user.is_authenticated:
        return get_default_tenant()

    membership = TenantMembership.objects.filter(user=user, is_active=True).select_related('tenant').first()
    if membership:
        return membership.tenant
    return get_default_tenant()


def get_current_tenant():
    """
    Returns the context-local tenant, or fallback to the resolved user/default tenant.
    """
    tenant = _tenant_context.get()
    if tenant:
        return tenant
    return get_default_tenant()


def get_request_tenant(request):
    """
    Resolves the tenant associated with the request.
    """
    if not request:
        return get_current_tenant()
    return getattr(request, 'tenant', None) or get_current_tenant()
