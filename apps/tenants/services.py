import zoneinfo
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from apps.accounts.engine import PermissionEngine
from apps.audit.models import AuditEvent
from apps.audit.utils import get_request_device, get_request_ip
from apps.audit.services import mask_sensitive_data, _role_for_user
from apps.tenants.models import Tenant, TenantMembership, CompanyConfiguration


class CompanyConfigurationService:
    """
    Service for auditing and securing tenant-scoped Company Configuration.
    Enforces canonical tenant context, RBAC permissions, select_for_update row locking,
    secrets masking, and immutable audit logging.
    """

    @classmethod
    def get_canonical_tenant(cls, request):
        """
        Resolves the tenant exclusively from the authenticated canonical request context.
        Fails closed (returns None) if user has no active tenant membership.
        Superuser access is supported via canonical active tenant.
        """
        user = getattr(request, 'user', None)
        if not user or not user.is_authenticated:
            return None

        # 1. Resolve through active membership
        membership = (
            TenantMembership.objects.filter(
                user=user,
                is_active=True,
                tenant__status='active'
            )
            .select_related('tenant')
            .first()
        )
        if membership:
            return membership.tenant

        # 2. Superuser fallback to request.tenant or first active tenant
        if user.is_superuser:
            req_tenant = getattr(request, 'tenant', None)
            if req_tenant and req_tenant.status == 'active':
                return req_tenant
            return Tenant.objects.filter(status='active').first()

        # Regular authenticated users without active tenant membership fail closed
        return None

    @classmethod
    def check_permission(cls, user, action_type='view'):
        """
        Validates settings.view for read and settings.edit for update.
        Superuser access remains supported.
        """
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return True

        codename = 'settings.edit' if action_type == 'edit' else 'settings.view'
        result = PermissionEngine.evaluate(user, codename, action_type=action_type)
        if result.allowed:
            return True

        # Role fallback for standard administrator roles
        user_role = getattr(user, 'role', '')
        if user_role in ['admin', 'system_owner', 'super_admin']:
            return True

        return False

    @classmethod
    def validate_no_forgery(cls, request, canonical_tenant):
        """
        Ensures client-submitted company/tenant parameters match the canonical tenant.
        Forged tenant, tenant_id, company, or company_id values raise PermissionDenied.
        """
        forged_val = (
            request.POST.get('company_id') or request.GET.get('company_id') or
            request.POST.get('tenant_id') or request.GET.get('tenant_id') or
            request.POST.get('company') or request.GET.get('company') or
            request.POST.get('tenant') or request.GET.get('tenant')
        )
        if forged_val is not None and str(forged_val).strip() != '':
            val_str = str(forged_val).strip()
            allowed_identifiers = {
                str(canonical_tenant.pk),
                str(canonical_tenant.uuid),
                str(canonical_tenant.slug)
            }
            if val_str not in allowed_identifiers:
                raise PermissionDenied("Forbidden: Forged company/tenant identifier.")

    @classmethod
    def get_configuration(cls, request):
        """
        Reads company configuration for the authenticated canonical tenant.
        Requires settings.view permission. Secrets are never exposed.
        """
        if not cls.check_permission(request.user, action_type='view'):
            raise PermissionDenied("You do not have permission to view company settings.")

        canonical_tenant = cls.get_canonical_tenant(request)
        if not canonical_tenant:
            raise PermissionDenied("No active company context found for user.")

        cls.validate_no_forgery(request, canonical_tenant)

        config, _ = CompanyConfiguration.objects.get_or_create(
            tenant=canonical_tenant,
            defaults={
                'company_name': canonical_tenant.name,
                'timezone': 'UTC',
                'date_format': 'YYYY-MM-DD',
                'time_format': '24h',
                'currency': 'BDT',
            }
        )

        return config

    @classmethod
    def serialize_config(cls, config, mask_secrets=True):
        """
        Serializes configuration, masking sensitive fields such as api_secret.
        """
        data = {
            'id': config.id,
            'tenant_id': config.tenant_id,
            'tenant_slug': config.tenant.slug,
            'company_name': config.company_name or config.tenant.name,
            'timezone': config.timezone,
            'date_format': config.date_format,
            'time_format': config.time_format,
            'currency': config.currency,
            'contact_email': config.contact_email,
            'api_secret': '••••••••' if (mask_secrets and config.api_secret) else config.api_secret,
            'created_at': config.created_at.isoformat() if config.created_at else None,
            'updated_at': config.updated_at.isoformat() if config.updated_at else None,
        }
        return data

    @classmethod
    def update_configuration(cls, request, update_data):
        """
        Updates company configuration inside a transaction with select_for_update.
        Requires settings.edit permission. Records audit entry without storing sensitive values.
        """
        if not cls.check_permission(request.user, action_type='edit'):
            raise PermissionDenied("You do not have permission to update company settings.")

        canonical_tenant = cls.get_canonical_tenant(request)
        if not canonical_tenant:
            raise PermissionDenied("No active company context found for user.")

        cls.validate_no_forgery(request, canonical_tenant)

        # Validate updates inside transaction.atomic() with row lock
        with transaction.atomic():
            config = (
                CompanyConfiguration.objects.select_for_update()
                .filter(tenant=canonical_tenant)
                .first()
            )
            if not config:
                config = CompanyConfiguration.objects.create(
                    tenant=canonical_tenant,
                    company_name=canonical_tenant.name,
                    timezone='UTC',
                    date_format='YYYY-MM-DD',
                    time_format='24h',
                    currency='BDT',
                )
                # Re-lock after creation
                config = CompanyConfiguration.objects.select_for_update().get(pk=config.pk)

            # Snapshot before
            before_data = cls.serialize_config(config, mask_secrets=True)

            changed_fields = []

            # 1. Timezone update & validation
            if 'timezone' in update_data:
                tz_val = str(update_data['timezone']).strip()
                if not tz_val:
                    raise ValidationError({'timezone': 'Timezone cannot be empty.'})
                try:
                    zoneinfo.ZoneInfo(tz_val)
                except Exception:
                    raise ValidationError({'timezone': f"Invalid timezone '{tz_val}'."})

                if config.timezone != tz_val:
                    config.timezone = tz_val
                    changed_fields.append('timezone')

            # 2. Company Name
            if 'company_name' in update_data:
                name_val = str(update_data['company_name']).strip()
                if name_val and config.company_name != name_val:
                    config.company_name = name_val
                    changed_fields.append('company_name')

            # 3. Date format
            if 'date_format' in update_data:
                df_val = str(update_data['date_format']).strip()
                if df_val and config.date_format != df_val:
                    config.date_format = df_val
                    changed_fields.append('date_format')

            # 4. Currency
            if 'currency' in update_data:
                cur_val = str(update_data['currency']).strip().upper()
                if cur_val and config.currency != cur_val:
                    config.currency = cur_val
                    changed_fields.append('currency')

            # 5. Contact email
            if 'contact_email' in update_data:
                email_val = str(update_data['contact_email']).strip()
                if config.contact_email != email_val:
                    config.contact_email = email_val
                    changed_fields.append('contact_email')

            # 6. API secret (sensitive - never logged in raw form)
            if 'api_secret' in update_data:
                sec_val = str(update_data['api_secret']).strip()
                if sec_val and not sec_val.startswith('••••') and config.api_secret != sec_val:
                    config.api_secret = sec_val
                    changed_fields.append('api_secret')

            # Persist if changes occurred
            if changed_fields:
                config.save()
                after_data = cls.serialize_config(config, mask_secrets=True)

                # Record audit event without exposing secrets
                try:
                    AuditEvent.objects.create(
                        actor_user=request.user,
                        actor_role=_role_for_user(request.user),
                        module='settings',
                        object_type='CompanyConfiguration',
                        object_id=str(config.pk),
                        object_label=f"Company Config ({canonical_tenant.name})",
                        action='updated',
                        before_data=mask_sensitive_data(before_data),
                        after_data=mask_sensitive_data(after_data),
                        changed_fields={'fields': changed_fields},
                        reason_note=f"Updated company configuration for {canonical_tenant.name}: {', '.join(changed_fields)}",
                        request_path=getattr(request, 'path', '/tenants/settings/company/'),
                        request_method=getattr(request, 'method', 'POST'),
                        ip_address=get_request_ip(request) if request else None,
                        session_device=get_request_device(request) if request else '',
                        created_at=timezone.now(),
                    )
                except Exception:
                    pass

            return config
