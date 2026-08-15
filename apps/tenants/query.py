from django.db import models


class TenantQuerySetMixin:
    """Mixin for querysets to support explicit tenant filtering."""
    def for_tenant(self, tenant):
        return self.filter(tenant=tenant)


def for_tenant(queryset_or_model, tenant):
    """
    Explicitly filters a queryset or model for a given tenant.
    Does not use automatic/magic global filters.
    """
    if hasattr(queryset_or_model, '_default_manager'):
        return queryset_or_model._default_manager.filter(tenant=tenant)
    return queryset_or_model.filter(tenant=tenant)
