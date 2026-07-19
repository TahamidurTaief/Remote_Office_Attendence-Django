import json
import os
from django.utils import timezone
from django.conf import settings
from apps.attendance.models import Attendance
from apps.employees.models import EmployeeProfile
from apps.branches.models import Branch
from apps.leave.models import LeaveRequest
from apps.projects.models import Project, ProjectTask, DailyProgressLog, ManpowerDeployment, ProjectMaterial
from apps.expense.models import Expense
from .gdrive import upload_to_drive

BACKUP_DIR = os.path.join(settings.BASE_DIR, "backups", "files")


def ensure_backup_dir():
    os.makedirs(BACKUP_DIR, exist_ok=True)


def serialize_attendance(qs):
    data = []
    for att in qs.select_related(
        "employee", "employee__branch"
    ).prefetch_related("locations"):
        record = {
            "id": att.id,
            "employee_id": att.employee.employee_id,
            "employee_name": att.employee.full_name,
            "date": str(att.date),
            "check_in_time": str(att.check_in_time) if att.check_in_time else None,
            "check_out_time": str(att.check_out_time) if att.check_out_time else None,
            "total_hours": str(att.total_hours) if att.total_hours else None,
            "attendance_type": att.attendance_type,
            "status": att.status,
            "note": att.note,
            "overtime_minutes": att.overtime_minutes,
            "is_early_checkout": att.is_early_checkout,
            "is_expired": att.is_expired,
            "locations": [
                {
                    "lat": str(loc.latitude),
                    "lng": str(loc.longitude),
                    "address": loc.address,
                    "event": loc.event,
                    "timestamp": str(loc.timestamp),
                    "accuracy": loc.accuracy,
                }
                for loc in att.locations.all()
            ],
        }
        data.append(record)
    return data


def serialize_leaves(qs):
    data = []
    for req in qs.select_related("employee", "leave_type"):
        data.append({
            "id": req.id,
            "employee_id": req.employee.employee_id if req.employee else None,
            "leave_type": req.leave_type.name if req.leave_type else None,
            "start_date": str(req.start_date),
            "end_date": str(req.end_date),
            "number_of_days": req.number_of_days,
            "reason": req.reason,
            "status": req.status,
            "requested_at": str(req.requested_at) if req.requested_at else None,
            "reviewed_by": req.reviewed_by.email if req.reviewed_by else None,
            "reviewed_at": str(req.reviewed_at) if req.reviewed_at else None,
            "rejection_reason": req.rejection_reason,
            "sync_uuid": str(req.sync_uuid) if req.sync_uuid else None,
        })
    return data

def serialize_projects(qs):
    data = []
    for p in qs.select_related("project_type", "branch").prefetch_related("project_managers", "site_engineers", "project_members"):
        data.append({
            "id": p.id,
            "name": p.name,
            "client_name": p.client_name,
            "project_type": p.project_type.name if p.project_type else None,
            "start_date": str(p.start_date),
            "completion_date": str(p.completion_date) if p.completion_date else None,
            "status": p.status,
            "progress_percent": p.progress_percent,
            "branch_name": p.branch.name if p.branch else None,
            "project_managers": [m.employee_id for m in p.project_managers.all()],
            "site_engineers": [m.employee_id for m in p.site_engineers.all()],
            "project_members": [m.employee_id for m in p.project_members.all()],
        })
    return data

def serialize_tasks(qs):
    data = []
    for t in qs.select_related("project", "responsible_person"):
        data.append({
            "id": t.id,
            "project_name": t.project.name if t.project else None,
            "order": t.order,
            "activity": t.activity,
            "responsible_person_id": t.responsible_person.employee_id if t.responsible_person else None,
            "planned_start": str(t.planned_start) if t.planned_start else None,
            "planned_finish": str(t.planned_finish) if t.planned_finish else None,
            "duration_days": t.duration_days,
            "status": t.status,
            "remarks": t.remarks,
            "points": t.points,
            "completed_at": str(t.completed_at) if t.completed_at else None,
            "employee_note": t.employee_note,
            "progress_percent": t.progress_percent,
            "pending_progress_percent": t.pending_progress_percent,
            "pending_employee_note": t.pending_employee_note,
        })
    return data

def serialize_progress_logs(qs):
    data = []
    for log in qs.select_related("project", "logged_by"):
        data.append({
            "id": log.id,
            "project_name": log.project.name if log.project else None,
            "date": str(log.date),
            "planned_work": log.planned_work,
            "completed_work": log.completed_work,
            "manpower_count": log.manpower_count,
            "delay_reason": log.delay_reason,
            "supervisor_name": log.supervisor_name,
            "logged_by": log.logged_by.email if log.logged_by else None,
            "sync_uuid": str(log.sync_uuid) if log.sync_uuid else None,
        })
    return data

def serialize_manpower_deployments(qs):
    data = []
    for dep in qs.select_related("project"):
        data.append({
            "id": dep.id,
            "project_name": dep.project.name if dep.project else None,
            "date": str(dep.date),
            "trade": dep.trade,
            "required_count": dep.required_count,
            "present_count": dep.present_count,
            "sync_uuid": str(dep.sync_uuid) if dep.sync_uuid else None,
        })
    return data

def serialize_project_materials(qs):
    data = []
    for m in qs.select_related("project"):
        data.append({
            "id": m.id,
            "project_name": m.project.name if m.project else None,
            "material_name": m.material_name,
            "unit": m.unit,
            "required_qty": str(m.required_qty),
            "received_qty": str(m.received_qty),
            "remarks": m.remarks,
        })
    return data

def serialize_expenses(qs):
    data = []
    for exp in qs.select_related("employee", "project", "reviewed_by"):
        data.append({
            "id": exp.id,
            "employee_id": exp.employee.employee_id if exp.employee else None,
            "project_name": exp.project.name if exp.project else None,
            "amount": str(exp.amount),
            "category": exp.category,
            "description": exp.description,
            "status": exp.status,
            "rejection_reason": exp.rejection_reason,
            "requested_at": str(exp.requested_at),
            "reviewed_by": exp.reviewed_by.email if exp.reviewed_by else None,
            "reviewed_at": str(exp.reviewed_at) if exp.reviewed_at else None,
            "sync_uuid": str(exp.sync_uuid) if exp.sync_uuid else None,
        })
    return data




def create_backup(backup_type="manual", created_by=None):
    from .models import BackupRecord, GoogleDriveConfig

    ensure_backup_dir()

    timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
    filename = f"fieldtrack_backup_{timestamp}.json"
    filepath = os.path.join(BACKUP_DIR, filename)

    backup = BackupRecord.objects.create(
        backup_type=backup_type,
        status="pending",
        file_name=filename,
        file_path=filepath,
        created_by=created_by,
    )

    try:
        backup_data = {
            "meta": {
                "created_at": timezone.now().isoformat(),
                "backup_type": backup_type,
                "version": "1.0",
                "project": "FieldTrack",
            },
            "employees": list(
                EmployeeProfile.objects.select_related(
                    "user", "branch"
                ).values(
                    "employee_id",
                    "full_name",
                    "department",
                    "designation",
                    "phone",
                    "is_active",
                    "overtime_enabled",
                    "branch__name",
                    "joined_date",
                )
            ),
            "attendance": serialize_attendance(
                Attendance.objects.filter(is_expired=False)
            ),
            "expired_attendance": serialize_attendance(
                Attendance.objects.filter(is_expired=True)
            ),
            "branches": list(
                Branch.objects.values(
                    "name",
                    "address",
                    "latitude",
                    "longitude",
                    "radius_meters",
                    "is_active",
                )
            ),
            "leaves": serialize_leaves(LeaveRequest.objects.all()),
            "projects": serialize_projects(Project.objects.all()),
            "tasks": serialize_tasks(ProjectTask.objects.all()),
            "progress_logs": serialize_progress_logs(DailyProgressLog.objects.all()),
            "manpower_deployments": serialize_manpower_deployments(ManpowerDeployment.objects.all()),
            "project_materials": serialize_project_materials(ProjectMaterial.objects.all()),
            "expenses": serialize_expenses(Expense.objects.all()),
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(
                backup_data,
                f,
                indent=2,
                ensure_ascii=False,
                default=str,
            )

        # Create raw SQLite database backup safely via sqlite3 backup API
        db_filename = filename.replace(".json", ".sqlite3")
        db_filepath = filepath.replace(".json", ".sqlite3")
        
        import sqlite3
        from django.db import connection
        connection.ensure_connection()
        src_conn = connection.connection
        dst_conn = sqlite3.connect(db_filepath)
        try:
            with dst_conn:
                src_conn.backup(dst_conn)
        finally:
            dst_conn.close()

        file_size = os.path.getsize(filepath)
        record_count = (
            len(backup_data["attendance"]) +
            len(backup_data["expired_attendance"]) +
            len(backup_data["leaves"]) +
            len(backup_data["projects"]) +
            len(backup_data["tasks"]) +
            len(backup_data["progress_logs"]) +
            len(backup_data["manpower_deployments"]) +
            len(backup_data["project_materials"]) +
            len(backup_data["expenses"])
        )

        backup.status = "completed"
        backup.file_size = file_size
        backup.record_count = record_count
        backup.save()

        config = GoogleDriveConfig.get_config()
        if config.is_enabled and config.auto_upload_to_drive:
            try:
                # Upload JSON backup (primary)
                upload_to_drive(backup, filepath, config, mimetype="application/json", is_primary=True)
                # Upload SQLite db backup
                upload_to_drive(backup, db_filepath, config, filename=db_filename, mimetype="application/x-sqlite3", is_primary=False)
            except Exception as e:
                backup.error_message = f"Drive upload failed: {str(e)}"
                backup.save(update_fields=["error_message"])

        cleanup_old_backups(config.keep_local_copies)

        return backup

    except Exception as e:
        backup.status = "failed"
        backup.error_message = str(e)
        backup.save(update_fields=["status", "error_message"])
        raise


def cleanup_old_backups(keep_count=7):
    """Keep only the last N backup files."""
    from .models import BackupRecord

    try:
        keep_count = int(keep_count)
    except (TypeError, ValueError):
        keep_count = 7
    keep_count = max(1, keep_count)

    old_backups = BackupRecord.objects.filter(
        status="completed"
    ).order_by("-created_at")[keep_count:]

    for backup in old_backups:
        if backup.file_path and os.path.exists(backup.file_path):
            os.remove(backup.file_path)
            db_filepath = backup.file_path.replace(".json", ".sqlite3")
            if os.path.exists(db_filepath):
                os.remove(db_filepath)
        backup.file_path = ""
        backup.file_size = 0
        backup.save(update_fields=["file_path", "file_size"])
