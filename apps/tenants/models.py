import uuid
from django.db import models
from django.conf import settings


class BaseTimestampModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Tenant(BaseTimestampModel):
    STATUS_CHOICES = (
        ('active', 'Active'),
        ('inactive', 'Inactive'),
    )

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, db_index=True)
    status = models.CharField(max_length=50, default='active', choices=STATUS_CHOICES)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        before = {}
        if not is_new:
            try:
                orig = Tenant.objects.get(pk=self.pk)
                from apps.audit.utils import serialize_instance
                before = serialize_instance(orig)
            except Exception:
                pass

        super().save(*args, **kwargs)

        try:
            from apps.audit.services import AuditService
            action = 'created' if is_new else 'updated'
            AuditService.log_model_change(self, action=action, before=before)
        except Exception:
            pass

    def delete(self, *args, **kwargs):
        try:
            from apps.audit.utils import serialize_instance
            before = serialize_instance(self)
        except Exception:
            before = {}

        super().delete(*args, **kwargs)

        try:
            from apps.audit.services import AuditService
            AuditService.log_event(
                action='deleted',
                instance=self,
                before=before,
                object_type=self.__class__.__name__,
                object_id=str(self.pk),
                object_label=str(self)
            )
        except Exception:
            pass


class TenantBaseModel(BaseTimestampModel):
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.PROTECT,
        related_name='%(class)s_related',
        db_index=True
    )

    class Meta:
        abstract = True


class TenantSoftDeleteModel(TenantBaseModel):
    is_trashed = models.BooleanField(default=False, db_index=True)
    trashed_at = models.DateTimeField(null=True, blank=True)
    trashed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='%(class)s_trashed'
    )

    class Meta:
        abstract = True


class TenantMembership(BaseTimestampModel):
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name='memberships'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='tenant_memberships'
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ('tenant', 'user')

    def __str__(self):
        return f"{self.user} - {self.tenant} ({'Active' if self.is_active else 'Inactive'})"


class TenantTestModel(TenantBaseModel):
    name = models.CharField(max_length=255)

    def __str__(self):
        return self.name

