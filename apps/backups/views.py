import os
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, JsonResponse, FileResponse
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from .models import BackupRecord, GoogleDriveConfig
from .utils import create_backup
from .gdrive import upload_to_drive, test_drive_connection


def _admin_required(view_func):
    from functools import wraps

    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if request.user.role != "admin":
            from django.http import HttpResponseForbidden
            return HttpResponseForbidden("Admins only.")
        return view_func(request, *args, **kwargs)

    return _wrapped


@_admin_required
def backup_list(request):
    backups = BackupRecord.objects.select_related('created_by').all()[:50]
    config = GoogleDriveConfig.get_config()
    stats = {
        "total": BackupRecord.objects.count(),
        "completed": BackupRecord.objects.filter(status="completed").count(),
        "drive_uploaded": BackupRecord.objects.filter(gdrive_uploaded=True).count(),
        "last_backup": BackupRecord.objects.filter(status="completed").first(),
    }
    return render(request, "backups/backup_list.html", {
        "backups": backups,
        "config": config,
        "stats": stats,
    })


@_admin_required
@require_POST
def create_manual_backup(request):
    try:
        backup = create_backup(
            backup_type="manual",
            created_by=request.user,
        )
        if backup.status == "completed" and backup.file_path:
            if os.path.exists(backup.file_path):
                response = FileResponse(
                    open(backup.file_path, "rb"),
                    content_type="application/json",
                )
                response["Content-Disposition"] = (
                    f"attachment; filename=\"{backup.file_name}\""
                )
                return response
            messages.error(request, "Backup file not found on disk.")
    except Exception as e:
        messages.error(request, f"Backup failed: {str(e)}")
    return redirect("backups:backup_list")


@_admin_required
@require_POST
def upload_to_drive_view(request, pk):
    backup = get_object_or_404(BackupRecord, pk=pk)
    config = GoogleDriveConfig.get_config()
    if not config.is_enabled:
        messages.error(request, "Google Drive is disabled.")
        return redirect("backups:backup_list")
    if not backup.file_path or not os.path.exists(backup.file_path):
        messages.error(request, "Backup file not found.")
        return redirect("backups:backup_list")
    try:
        upload_to_drive(backup, backup.file_path, config)
        messages.success(request, "Uploaded to Google Drive!")
    except Exception as e:
        messages.error(request, str(e))
    return redirect("backups:backup_list")


@_admin_required
@require_POST
def save_drive_config(request):
    config = GoogleDriveConfig.get_config()
    config.is_enabled = "is_enabled" in request.POST
    config.service_account_json = request.POST.get("service_account_json", "")
    config.folder_id = request.POST.get("folder_id", "")
    config.auto_daily_enabled = "auto_daily_enabled" in request.POST
    config.auto_3day_enabled = "auto_3day_enabled" in request.POST
    config.auto_upload_to_drive = "auto_upload_to_drive" in request.POST
    try:
        keep_local = int(request.POST.get("keep_local_copies", 7))
    except (TypeError, ValueError):
        keep_local = 7
    config.keep_local_copies = max(1, keep_local)
    config.save()
    messages.success(request, "Settings saved!")
    return redirect("backups:backup_list")


@_admin_required
@require_POST
def test_drive_connection_view(request):
    config = GoogleDriveConfig.get_config()
    success, message = test_drive_connection(
        config.service_account_json,
        config.folder_id,
    )
    if request.headers.get("HX-Request"):
        icon = "✅" if success else "❌"
        return HttpResponse(f"{icon} {message}")
    return JsonResponse({"success": success, "message": message})


@_admin_required
@require_POST
def delete_backup(request, pk):
    backup = get_object_or_404(BackupRecord, pk=pk)
    if backup.file_path and os.path.exists(backup.file_path):
        os.remove(backup.file_path)
        db_filepath = backup.file_path.replace(".json", ".sqlite3")
        if os.path.exists(db_filepath):
            os.remove(db_filepath)
    backup.delete()
    messages.success(request, "Backup deleted.")
    return redirect("backups:backup_list")


@_admin_required
def download_backup(request, pk):
    backup = get_object_or_404(BackupRecord, pk=pk)
    if not backup.file_path or not os.path.exists(backup.file_path):
        messages.error(request, "Backup file not found.")
        return redirect("backups:backup_list")
    response = FileResponse(
        open(backup.file_path, "rb"),
        content_type="application/json",
    )
    response["Content-Disposition"] = (
        f"attachment; filename=\"{backup.file_name}\""
    )
    return response
