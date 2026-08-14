from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class AuditEvent(models.Model):
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="platform_audit_events",
    )
    actor_role = models.CharField(max_length=64, blank=True)
    module = models.CharField(max_length=100, db_index=True)
    object_type = models.CharField(max_length=100, db_index=True)
    object_id = models.CharField(max_length=100, db_index=True)
    object_label = models.CharField(max_length=255, blank=True)
    action = models.CharField(max_length=100, db_index=True)
    before_data = models.JSONField(default=dict, blank=True)
    after_data = models.JSONField(default=dict, blank=True)
    changed_fields = models.JSONField(default=dict, blank=True)
    related_employee = models.ForeignKey(
        "employees.Employee",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    related_project = models.ForeignKey(
        "projects.Project",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    related_branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    request_path = models.CharField(max_length=255, blank=True)
    request_method = models.CharField(max_length=20, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    session_device = models.CharField(max_length=255, blank=True)
    reason_note = models.TextField(blank=True)
    content_type = models.ForeignKey(ContentType, on_delete=models.SET_NULL, null=True, blank=True)
    content_object_id = models.PositiveBigIntegerField(null=True, blank=True)
    content_object = GenericForeignKey("content_type", "content_object_id")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["module", "created_at"]),
            models.Index(fields=["action", "created_at"]),
            models.Index(fields=["object_type", "object_id", "created_at"]),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("AuditEvent records are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("AuditEvent records cannot be deleted.")


class TrashEntry(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_RESTORED = "restored"
    STATUS_PURGED = "permanently_deleted"
    STATUS_CHOICES = (
        (STATUS_ACTIVE, "Active"),
        (STATUS_RESTORED, "Restored"),
        (STATUS_PURGED, "Permanently Deleted"),
    )

    module = models.CharField(max_length=100, db_index=True)
    object_type = models.CharField(max_length=100)
    object_id = models.CharField(max_length=100, db_index=True)
    object_label = models.CharField(max_length=255, blank=True)
    content_type = models.ForeignKey(ContentType, on_delete=models.SET_NULL, null=True, blank=True)
    content_object_id = models.PositiveBigIntegerField(null=True, blank=True)
    content_object = GenericForeignKey("content_type", "content_object_id")
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="trash_entries_deleted",
    )
    deleted_at = models.DateTimeField(default=timezone.now, db_index=True)
    delete_reason = models.TextField(blank=True)
    restore_allowed = models.BooleanField(default=True)
    permanent_delete_allowed = models.BooleanField(default=True)
    dependency_summary = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_ACTIVE, db_index=True)
    restored_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="trash_entries_restored",
    )
    restored_at = models.DateTimeField(null=True, blank=True)
    permanently_deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="trash_entries_purged",
    )
    permanently_deleted_at = models.DateTimeField(null=True, blank=True)
    related_employee = models.ForeignKey(
        "employees.Employee",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="trash_entries",
    )
    related_project = models.ForeignKey(
        "projects.Project",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="trash_entries",
    )
    related_branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="trash_entries",
    )

    class Meta:
        ordering = ["-deleted_at"]
        indexes = [
            models.Index(fields=["module", "status", "deleted_at"]),
            models.Index(fields=["deleted_by", "deleted_at"]),
        ]

    def __str__(self):
        return f"{self.module}:{self.object_type}:{self.object_id}"


class AuditAccessLog(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="audit_access_logs")
    audit_event = models.ForeignKey(AuditEvent, on_delete=models.CASCADE, related_name="access_logs")
    accessed_at = models.DateTimeField(default=timezone.now)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    session_device = models.CharField(max_length=255, blank=True)
    reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-accessed_at"]

