from django.db.models.signals import post_delete, post_save, pre_save

from .context import get_current_request
from .services import AuditService
from .utils import serialize_instance

# Models to track across operations
TRACKED_MODEL_PATHS = [
    ("apps.employees.models", "Employee"),
    ("apps.employees.models", "EmployeeProfile"),
    ("apps.projects.models", "Project"),
    ("apps.projects.models", "ProjectTask"),
    ("apps.projects.models", "DailyProgressLog"),
    ("apps.projects.models", "ManpowerDeployment"),
    ("apps.projects.models", "ProjectMaterial"),
    ("apps.projects.models", "TaskTemplate"),
    ("apps.schedule.models", "ScheduleEvent"),
    ("apps.leave.models", "LeaveRequest"),
    ("apps.expense.models", "Expense"),
    ("apps.branches.models", "Branch"),
    # Settings, Security & Governance models
    ("apps.accounts.models", "Role"),
    ("apps.accounts.models", "RolePermission"),
    ("apps.accounts.models", "UserRoleAssignment"),
    ("apps.accounts.models", "UserPermissionOverride"),
    ("apps.accounts.models", "SecurityPolicy"),
    ("apps.accounts.models", "UserSecurityProfile"),
    ("apps.branches.models", "OfficeSchedule"),
    ("apps.attendance.models", "AttendancePolicy"),
    ("apps.backups.models", "GoogleDriveConfig"),
    ("apps.backups.models", "BackupRecord"),
]


def _capture_before_save(sender, instance, raw=False, **kwargs):
    if raw or kwargs.get("raw", False):
        return
    if not instance.pk:
        instance._audit_before_snapshot = None
        return
    try:
        prior = sender.objects.filter(pk=instance.pk).first()
        instance._audit_before_snapshot = serialize_instance(prior) if prior else None
    except Exception:
        instance._audit_before_snapshot = None


def _create_post_save_audit(sender, instance, created, raw=False, **kwargs):
    if raw or kwargs.get("raw", False):
        return
    if getattr(instance, "_audit_skip_signal", False):
        try:
            delattr(instance, "_audit_skip_signal")
        except AttributeError:
            pass
        return
    if getattr(instance, "_audit_logged", False):
        return

    # Avoid recursive auditing if AuditEvent itself
    if instance.__class__.__name__ in ("AuditEvent", "TrashEntry", "AuditAccessLog"):
        return

    request = get_current_request()
    actor = getattr(request, "user", None) if request else None
    before = getattr(instance, "_audit_before_snapshot", None)
    if hasattr(instance, "_audit_before_snapshot"):
        try:
            delattr(instance, "_audit_before_snapshot")
        except AttributeError:
            pass

    try:
        if created:
            AuditService.log_model_change(instance, action="created", before={}, actor=actor, request=request)
        else:
            AuditService.log_model_change(instance, action="updated", before=before or {}, actor=actor, request=request)
    except Exception:
        pass


def _create_post_delete_audit(sender, instance, **kwargs):
    if kwargs.get("raw", False):
        return
    if getattr(instance, "_audit_skip_signal", False):
        try:
            delattr(instance, "_audit_skip_signal")
        except AttributeError:
            pass
        return
    if getattr(instance, "_audit_logged", False):
        return

    # Avoid recursive auditing
    if instance.__class__.__name__ in ("AuditEvent", "TrashEntry", "AuditAccessLog"):
        return

    request = get_current_request()
    actor = getattr(request, "user", None) if request else None
    before = getattr(instance, "_audit_before_snapshot", None)
    if hasattr(instance, "_audit_before_snapshot"):
        try:
            delattr(instance, "_audit_before_snapshot")
        except AttributeError:
            pass

    if not before:
        try:
            before = serialize_instance(instance)
        except Exception:
            before = {"pk": instance.pk}
    try:
        AuditService.log_model_change(instance, action="deleted", before=before, after={}, actor=actor, request=request)
    except Exception:
        pass


def register_audit_signals():
    import importlib
    for mod_path, model_name in TRACKED_MODEL_PATHS:
        try:
            mod = importlib.import_module(mod_path)
            model_cls = getattr(mod, model_name, None)
            if model_cls:
                pre_save.connect(_capture_before_save, sender=model_cls, weak=False, dispatch_uid=f"audit_presave_{model_name}")
                post_save.connect(_create_post_save_audit, sender=model_cls, weak=False, dispatch_uid=f"audit_postsave_{model_name}")
                post_delete.connect(_create_post_delete_audit, sender=model_cls, weak=False, dispatch_uid=f"audit_postdelete_{model_name}")
        except Exception:
            pass


register_audit_signals()
