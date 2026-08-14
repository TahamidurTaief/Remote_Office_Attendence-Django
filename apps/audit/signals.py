from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from apps.employees.models import Employee
from .context import get_current_request
from .services import AuditService
from .utils import serialize_instance


@receiver(pre_save, sender=Employee)
def capture_employee_before_save(sender, instance, **kwargs):
    if not instance.pk:
        instance._audit_before_snapshot = None
        return
    prior = sender.objects.filter(pk=instance.pk).first()
    instance._audit_before_snapshot = serialize_instance(prior) if prior else None


@receiver(post_save, sender=Employee)
def create_employee_audit(sender, instance, created, **kwargs):
    if getattr(instance, "_audit_skip_signal", False):
        delattr(instance, "_audit_skip_signal")
        return
    request = get_current_request()
    actor = getattr(request, "user", None) if request else None
    before = getattr(instance, "_audit_before_snapshot", None)
    if created:
        AuditService.log_model_change(instance, action="created", before={}, actor=actor, request=request)
        return
    AuditService.log_model_change(instance, action="updated", before=before or {}, actor=actor, request=request)

