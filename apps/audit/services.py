from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.engine import PermissionEngine
from apps.accounts.mixins import RoleRequiredMixin
from apps.employees.hierarchy_services import OrgHierarchyService
from apps.notifications.models import AuditLog, ActivityLog, log_audit
from .context import get_current_request
from .models import AuditAccessLog, AuditEvent, TrashEntry
from .utils import diff_snapshots, get_request_device, get_request_ip, serialize_instance


def mask_sensitive_data(data):
    if isinstance(data, dict):
        masked = {}
        for k, v in data.items():
            k_lower = str(k).lower()
            if any(term in k_lower for term in ["password", "token", "secret", "pin", "nid", "national_id", "bank_account", "bank_routing", "card_number", "routing_number", "account_number", "session_key", "trusted_device", "backup_code"]):
                masked[k] = "********"
            else:
                masked[k] = mask_sensitive_data(v)
        return masked
    elif isinstance(data, list):
        return [mask_sensitive_data(item) for item in data]
    return data


def _role_for_user(user):
    if not user or not getattr(user, "is_authenticated", False):
        return ""
    roles = list(user.role_assignments.select_related("role").filter(role__is_active=True).values_list("role__code", flat=True))
    if roles:
        return ",".join(sorted(set(roles)))
    return getattr(user, "role", "") or ""


def _resolve_related(instance):
    related_employee = None
    related_project = None
    related_branch = None

    if not instance:
        return None, None, None

    model_name = instance.__class__.__name__

    if model_name == "Employee":
        related_employee = instance
        related_branch = getattr(instance, "branch", None)
    elif model_name == "EmployeeProfile":
        if hasattr(instance, "master_employee") and instance.master_employee:
            related_employee = instance.master_employee
        related_branch = getattr(instance, "branch", None)
    elif model_name == "Project":
        related_project = instance
        related_branch = getattr(instance, "branch", None)
    elif model_name == "Branch":
        related_branch = instance

    if hasattr(instance, "employee_master") and getattr(instance, "employee_master_id", None):
        related_employee = instance.employee_master

    if hasattr(instance, "responsible_person") and getattr(instance, "responsible_person_id", None):
        rp = instance.responsible_person
        if rp:
            if hasattr(rp, "master_employee") and rp.master_employee:
                related_employee = rp.master_employee
            elif rp.__class__.__name__ == "Employee":
                related_employee = rp
            if not related_branch:
                related_branch = getattr(rp, "branch", None)

    if hasattr(instance, "employee") and getattr(instance, "employee_id", None):
        employee_rel = instance.employee
        if employee_rel:
            if hasattr(employee_rel, "master_employee") and employee_rel.master_employee_id:
                related_employee = employee_rel.master_employee
            elif employee_rel.__class__.__name__ == "Employee":
                related_employee = employee_rel
            if not related_branch:
                related_branch = getattr(employee_rel, "branch", None)

    if hasattr(instance, "project") and getattr(instance, "project_id", None):
        related_project = instance.project
        if not related_branch and related_project:
            related_branch = getattr(related_project, "branch", None)

    if hasattr(instance, "branch") and getattr(instance, "branch_id", None):
        related_branch = instance.branch

    if related_employee and not related_branch:
        related_branch = getattr(related_employee, "branch", None)

    return related_employee, related_project, related_branch


class AuditService:
    @classmethod
    def log_event(cls, *, actor=None, action, instance=None, module="", object_type="", object_id="", object_label="", before=None, after=None, changed_fields=None, reason="", request=None):
        try:
            if instance is not None:
                # Prevent recursive audit logging
                if isinstance(instance, (AuditEvent, AuditAccessLog, TrashEntry)) or instance.__class__.__name__ in ("AuditEvent", "AuditAccessLog", "TrashEntry"):
                    return None

                module = module or instance._meta.app_label
                object_type = object_type or instance.__class__.__name__
                object_id = object_id or str(instance.pk or "")
                object_label = object_label or str(instance)
                content_type = ContentType.objects.get_for_model(instance.__class__)
                content_object_id = instance.pk
                related_employee, related_project, related_branch = _resolve_related(instance)
                # Hard deletion cascade protection: when any object is deleted, avoid foreign keys to objects that may be cascade deleted
                if action == "deleted":
                    related_employee = None
                    related_project = None
                    related_branch = None
            else:
                content_type = None
                content_object_id = None
                related_employee = related_project = related_branch = None

            before_masked = mask_sensitive_data(before or {})
            after_masked = mask_sensitive_data(after or {})
            changed_fields_masked = mask_sensitive_data(changed_fields or {})

            event = AuditEvent.objects.create(
                actor_user=actor if getattr(actor, "is_authenticated", False) else None,
                actor_role=_role_for_user(actor),
                module=module,
                object_type=object_type,
                object_id=object_id,
                object_label=object_label,
                action=action,
                before_data=before_masked,
                after_data=after_masked,
                changed_fields=changed_fields_masked,
                related_employee=related_employee,
                related_project=related_project,
                related_branch=related_branch,
                request_path=getattr(request, "path", "")[:255],
                request_method=getattr(request, "method", "")[:20],
                ip_address=get_request_ip(request) or None,
                session_device=get_request_device(request),
                reason_note=reason,
                content_type=content_type,
                content_object_id=content_object_id,
            )
            if actor and action:
                try:
                    log_audit(
                        actor=actor,
                        action=f"platform_{action}",
                        target=instance,
                        summary=f"{action} {object_type or ''} {object_label or object_id}".strip(),
                        ip=get_request_ip(request),
                        metadata={"module": module, "changed_fields": list((changed_fields_masked or {}).keys())},
                    )
                except Exception:
                    pass
            return event
        except Exception:
            # Audit failures must never roll back or block the primary business operation
            return None

    @classmethod
    def log_model_change(cls, instance, *, action, before=None, after=None, actor=None, reason="", request=None):
        before = before or {}
        after = after or serialize_instance(instance)
        changed_fields = diff_snapshots(before, after)
        if action == "updated" and not changed_fields:
            return None
        return cls.log_event(
            actor=actor,
            action=action,
            instance=instance,
            before=before,
            after=after,
            changed_fields=changed_fields,
            reason=reason,
            request=request,
        )

    @classmethod
    def get_scoped_events(cls, user):
        qs = AuditEvent.objects.select_related("actor_user", "related_employee", "related_project", "related_branch")
        if not user or not user.is_authenticated:
            return qs.none()
        if user.is_superuser:
            return qs

        role_codes = set(user.role_assignments.select_related("role").filter(role__is_active=True).values_list("role__code", flat=True))
        role_codes.add(getattr(user, "role", ""))
        role_codes.discard("")

        if "admin" in role_codes or "system_owner" in role_codes:
            return qs

        own_filter = Q(actor_user=user)
        emp_master = getattr(user, "employee_master", None)
        emp_profile = getattr(user, "employee_profile", None)

        if emp_master:
            own_filter |= Q(related_employee=emp_master)
        if emp_profile and getattr(emp_profile, "master_employee_id", None):
            own_filter |= Q(related_employee=emp_profile.master_employee)

        combined = own_filter

        if "manager" in role_codes and emp_master:
            subordinate_ids = list(OrgHierarchyService.get_all_subordinates(emp_master).values_list("id", flat=True))
            subordinate_ids.append(emp_master.id)
            combined |= Q(related_employee_id__in=subordinate_ids)

        if "hr" in role_codes:
            combined |= Q(module="employees") | Q(related_employee__isnull=False)

        if emp_profile and getattr(emp_profile, "is_project_manager", False):
            combined |= Q(related_project__project_managers=emp_profile)

        return qs.filter(combined).distinct()

    @classmethod
    def log_access(cls, user, event, *, reason="", request=None):
        request = request or get_current_request()
        return AuditAccessLog.objects.create(
            user=user,
            audit_event=event,
            ip_address=get_request_ip(request) or None,
            session_device=get_request_device(request),
            reason=reason,
        )


class TrashService:
    @classmethod
    def get_active_entry(cls, obj):
        return TrashEntry.objects.filter(
            content_type=ContentType.objects.get_for_model(obj.__class__),
            content_object_id=obj.pk,
            status=TrashEntry.STATUS_ACTIVE,
        ).first()

    @classmethod
    def get_scoped_entries(cls, user):
        qs = TrashEntry.objects.select_related("deleted_by", "related_employee", "related_project", "related_branch")
        if not user or not user.is_authenticated:
            return qs.none()
        if user.is_superuser:
            return qs

        role_codes = set(user.role_assignments.select_related("role").filter(role__is_active=True).values_list("role__code", flat=True))
        role_codes.add(getattr(user, "role", ""))
        role_codes.discard("")

        if "admin" in role_codes or "system_owner" in role_codes:
            return qs

        own_filter = Q(deleted_by=user)
        emp_master = getattr(user, "employee_master", None)
        emp_profile = getattr(user, "employee_profile", None)

        if emp_master:
            own_filter |= Q(related_employee=emp_master)
        if emp_profile and getattr(emp_profile, "master_employee_id", None):
            own_filter |= Q(related_employee=emp_profile.master_employee)

        combined = own_filter

        if "manager" in role_codes and emp_master:
            subordinate_ids = list(OrgHierarchyService.get_all_subordinates(emp_master).values_list("id", flat=True))
            subordinate_ids.append(emp_master.id)
            combined |= Q(related_employee_id__in=subordinate_ids)

        if "hr" in role_codes:
            combined |= Q(module="employees") | Q(related_employee__isnull=False)

        if emp_profile and getattr(emp_profile, "is_project_manager", False):
            combined |= Q(related_project__project_managers=emp_profile)

        return qs.filter(combined).distinct()

    @classmethod
    def get_dependencies(cls, obj):
        summary = {"blocked": False, "items": []}
        if obj.__class__.__name__ != "Employee":
            return summary

        profile = getattr(obj, "legacy_profile", None)
        from apps.attendance.models import Attendance
        from apps.leave.models import LeaveRequest
        from apps.payroll.models import EmployeePayrollCalculation
        from apps.expense.models import Expense
        from apps.employees.models import EmployeeDocument, AssetAssignment
        from apps.projects.models import Project, ProjectTask
        from apps.schedule.models import ScheduleEvent
        from apps.workflow.models import WorkflowInstance, WorkflowAction

        attendance_count = Attendance.objects.filter(employee=profile).count() if profile else 0
        leave_count = LeaveRequest.objects.filter(employee=profile).count() if profile else 0
        payroll_count = EmployeePayrollCalculation.objects.filter(employee=obj).count()
        expense_count = Expense.objects.filter(employee=profile).count() if profile else 0
        document_count = EmployeeDocument.objects.filter(employee_master=obj).count()
        asset_count = AssetAssignment.objects.filter(employee=profile).count() if profile else 0

        # projects/tasks
        project_count = 0
        task_count = 0
        if profile:
            project_count = Project.objects.filter(
                Q(project_managers=profile) |
                Q(site_engineers=profile) |
                Q(members=profile)
            ).distinct().count()
            task_count = ProjectTask.objects.filter(responsible_person=profile).count()

        # schedule
        schedule_count = ScheduleEvent.objects.filter(assigned_to=profile).count() if profile else 0

        # workflow
        workflow_count = 0
        if obj.user:
            workflow_count = (
                WorkflowInstance.objects.filter(initiated_by=obj.user).count() +
                WorkflowAction.objects.filter(actor=obj.user).count()
            )

        if attendance_count:
            summary["items"].append({"type": "attendance", "count": attendance_count, "message": "Attendance history exists."})
        if leave_count:
            summary["items"].append({"type": "leave", "count": leave_count, "message": "Leave history exists."})
        if payroll_count:
            summary["items"].append({"type": "payroll", "count": payroll_count, "message": "Payroll history exists."})
        if expense_count:
            summary["items"].append({"type": "expense", "count": expense_count, "message": "Expense history exists."})
        if document_count:
            summary["items"].append({"type": "document", "count": document_count, "message": "Employee document history exists."})
        if asset_count:
            summary["items"].append({"type": "asset", "count": asset_count, "message": "Asset assignments exist."})
        if project_count or task_count:
            summary["items"].append({"type": "projects", "count": project_count + task_count, "message": "Active project or task dependencies exist."})
        if schedule_count:
            summary["items"].append({"type": "schedule", "count": schedule_count, "message": "Schedule events exist."})
        if workflow_count:
            summary["items"].append({"type": "workflow", "count": workflow_count, "message": "Workflow history exists."})

        summary["blocked"] = bool(summary["items"])
        return summary


    @classmethod
    @transaction.atomic
    def soft_delete(cls, obj, *, actor=None, reason="", request=None):
        existing = cls.get_active_entry(obj)
        if existing:
            return existing, False

        before = serialize_instance(obj)
        if obj.__class__.__name__ != "Employee":
            raise ValidationError("Soft delete foundation is currently wired for Employee first.")

        obj._audit_skip_signal = True
        obj._allow_trashed_write = True
        obj._bypass_lifecycle_validation = True
        obj.is_trashed = True
        obj.trashed_at = timezone.now()
        obj.status = "archived"
        obj.save(update_fields=["is_trashed", "trashed_at", "status", "updated_at"])

        entry = TrashEntry.objects.create(
            module=obj._meta.app_label,
            object_type=obj.__class__.__name__,
            object_id=str(obj.pk),
            object_label=obj.get_full_name(),
            content_type=ContentType.objects.get_for_model(obj.__class__),
            content_object_id=obj.pk,
            deleted_by=actor if getattr(actor, "is_authenticated", False) else None,
            delete_reason=reason,
            restore_allowed=True,
            permanent_delete_allowed=True,
            dependency_summary=cls.get_dependencies(obj),
            metadata={"previous_status": before.get("status"), "snapshot": before},
            related_employee=obj,
            related_branch=obj.branch,
        )

        after = serialize_instance(obj)
        changed_fields = diff_snapshots(before, after)
        AuditService.log_event(
            actor=actor,
            action="deleted",
            instance=obj,
            before=before,
            after=after,
            changed_fields=changed_fields,
            reason=reason,
            request=request,
        )
        ActivityLog.objects.create(
            actor=actor if getattr(actor, "is_authenticated", False) else None,
            verb="deleted",
            target_content_type=ContentType.objects.get_for_model(obj.__class__),
            target_object_id=obj.pk,
            metadata={"module": obj._meta.app_label, "label": entry.object_label},
        )
        return entry, True

    @classmethod
    @transaction.atomic
    def restore(cls, entry, *, actor=None, request=None):
        if entry.status != TrashEntry.STATUS_ACTIVE:
            return entry, False
        obj = entry.content_object
        if not obj:
            raise ValidationError("This record no longer exists and cannot be restored.")
        if obj.__class__.__name__ != "Employee":
            raise ValidationError("Restore is currently wired for Employee first.")
        if not entry.restore_allowed:
            raise PermissionDenied("Restore is not allowed for this entry.")
        if not obj.is_trashed:
            entry.status = TrashEntry.STATUS_RESTORED
            entry.restored_by = actor if getattr(actor, "is_authenticated", False) else None
            entry.restored_at = timezone.now()
            entry.save(update_fields=["status", "restored_by", "restored_at"])
            return entry, False

        before = serialize_instance(obj)
        obj._audit_skip_signal = True
        obj._allow_trashed_write = True
        obj._allow_archived_write = True
        obj._bypass_lifecycle_validation = True
        obj.is_trashed = False
        obj.trashed_at = None
        obj.status = entry.metadata.get("previous_status") or "active"
        obj.save(update_fields=["is_trashed", "trashed_at", "status", "updated_at"])
        entry.status = TrashEntry.STATUS_RESTORED
        entry.restored_by = actor if getattr(actor, "is_authenticated", False) else None
        entry.restored_at = timezone.now()
        entry.save(update_fields=["status", "restored_by", "restored_at"])
        after = serialize_instance(obj)
        AuditService.log_event(
            actor=actor,
            action="restored",
            instance=obj,
            before=before,
            after=after,
            changed_fields=diff_snapshots(before, after),
            reason=entry.delete_reason,
            request=request,
        )
        return entry, True

    @classmethod
    @transaction.atomic
    def permanent_delete(cls, entry, *, actor=None, request=None):
        if entry.status == TrashEntry.STATUS_PURGED:
            return entry, False
        obj = entry.content_object
        if not obj:
            TrashEntry.objects.filter(pk=entry.pk).update(
                status=TrashEntry.STATUS_PURGED,
                permanently_deleted_by=actor if getattr(actor, "is_authenticated", False) else None,
                permanently_deleted_at=timezone.now(),
                content_type=None,
                content_object_id=None,
            )
            entry.refresh_from_db()
            return entry, False
        deps = cls.get_dependencies(obj)
        if deps["blocked"]:
            entry.dependency_summary = deps
            entry.permanent_delete_allowed = False
            entry.save(update_fields=["dependency_summary", "permanent_delete_allowed"])
            raise ValidationError("; ".join(item["message"] for item in deps["items"]))
        before = serialize_instance(obj)
        if getattr(obj, "user_id", None):
            obj._allow_trashed_write = True
            obj._allow_archived_write = True
            obj._bypass_lifecycle_validation = True
            obj.user = None
            obj.save(update_fields=["user"])
        obj.delete(hard=True)
        TrashEntry.objects.filter(pk=entry.pk).update(
            status=TrashEntry.STATUS_PURGED,
            content_type=None,
            content_object_id=None,
            permanently_deleted_by=actor if getattr(actor, "is_authenticated", False) else None,
            permanently_deleted_at=timezone.now(),
            permanent_delete_allowed=True,
            dependency_summary=deps,
        )
        entry.refresh_from_db()
        AuditService.log_event(
            actor=actor,
            action="permanently_deleted",
            module=entry.module,
            object_type=entry.object_type,
            object_id=entry.object_id,
            object_label=entry.object_label,
            before=before,
            after={},
            changed_fields={"deleted": {"before": before, "after": None}},
            reason=entry.delete_reason,
            request=request,
        )
        return entry, True
