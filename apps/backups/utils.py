import json
import os
from django.utils import timezone
from django.conf import settings
from apps.attendance.models import Attendance
from apps.employees.models import EmployeeProfile
from apps.branches.models import Branch
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
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(
                backup_data,
                f,
                indent=2,
                ensure_ascii=False,
                default=str,
            )

        file_size = os.path.getsize(filepath)
        record_count = (
            len(backup_data["attendance"]) + len(backup_data["expired_attendance"])
        )

        backup.status = "completed"
        backup.file_size = file_size
        backup.record_count = record_count
        backup.save()

        config = GoogleDriveConfig.get_config()
        if config.is_enabled and config.auto_upload_to_drive:
            try:
                upload_to_drive(backup, filepath, config)
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
        backup.file_path = ""
        backup.file_size = 0
        backup.save(update_fields=["file_path", "file_size"])
