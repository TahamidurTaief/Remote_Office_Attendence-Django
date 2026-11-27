import os
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, JsonResponse, FileResponse
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from .models import BackupRecord, GoogleDriveConfig
from .utils import create_backup
from .gdrive import upload_to_drive, test_drive_connection
from .encryption import generate_wrapped_key, decrypt_file_to_bytes, unwrap_key


def _admin_required(view_func):
    from functools import wraps

    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        from apps.accounts.engine import PermissionEngine
        if not (request.user.is_superuser or PermissionEngine.evaluate(request.user, 'accounts.edit').allowed or PermissionEngine.evaluate(request.user, 'settings.edit').allowed):
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


def _apply_private_no_store_headers(response, filename=None):
    response["Cache-Control"] = "private, no-store, no-cache, must-revalidate, max-age=0"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    response["X-Content-Type-Options"] = "nosniff"
    if filename is not None:
        safe_name = os.path.basename(filename).replace('"', "").replace("\r", "").replace("\n", "")
        response["Content-Disposition"] = f'attachment; filename="{safe_name}"'
    return response


@_admin_required
@require_POST
def create_manual_backup(request):
    from .exporter import create_portable_backup

    backup_format = request.POST.get("format", "legacy")
    try:
        if backup_format == "portable":
            backup = create_portable_backup(
                backup_type="manual",
                created_by=request.user,
            )
        else:
            backup = create_backup(
                backup_type="manual",
                created_by=request.user,
            )
        if backup.status == "completed" and backup.file_path:
            if os.path.exists(backup.file_path):
                content_type = (
                    "application/zip"
                    if backup.file_name.endswith(".zip")
                    else "application/json"
                )
                if backup.is_encrypted:
                    config = GoogleDriveConfig.get_config()
                    plaintext = decrypt_file_to_bytes(backup.file_path, config.master_key_wrapped)
                    response = HttpResponse(plaintext, content_type=content_type)
                    return _apply_private_no_store_headers(response, filename=backup.file_name)
                else:
                    response = FileResponse(
                        open(backup.file_path, "rb"),
                        as_attachment=True,
                        filename=os.path.basename(backup.file_name),
                        content_type=content_type,
                    )
                    return _apply_private_no_store_headers(response, filename=backup.file_name)
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
    """
    Download a backup file.
    - If the file is encrypted, decrypt it in-memory and serve the plaintext.
    - If the file is plaintext, stream it directly.
    """
    backup = get_object_or_404(BackupRecord, pk=pk)
    if not backup.file_path or not os.path.exists(backup.file_path):
        messages.error(request, "Backup file not found.")
        return redirect("backups:backup_list")

    content_type = (
        "application/zip"
        if backup.file_name.endswith(".zip")
        else "application/json"
    )
    if backup.is_encrypted:
        config = GoogleDriveConfig.get_config()
        try:
            plaintext = decrypt_file_to_bytes(backup.file_path, config.master_key_wrapped)
        except Exception as e:
            messages.error(request, f"Decryption failed: {e}")
            return redirect("backups:backup_list")
        response = HttpResponse(plaintext, content_type=content_type)
        return _apply_private_no_store_headers(response, filename=backup.file_name)

    response = FileResponse(
        open(backup.file_path, "rb"),
        as_attachment=True,
        filename=os.path.basename(backup.file_name),
        content_type=content_type,
    )
    return _apply_private_no_store_headers(response, filename=backup.file_name)


# ── Encryption management ─────────────────────────────────────────────────────

@_admin_required
@require_POST
def manage_encryption(request):
    """
    Handles three actions via POST field `action`:
      generate  – create an initial MEK and enable encryption (never replaces an existing key)
      rotate    – blocked when encrypted backups depend on the current key (no versioned-key schema)
      toggle    – flip encryption_enabled without touching the key
    """
    from django.db import transaction

    action = request.POST.get("action", "")

    with transaction.atomic():
        GoogleDriveConfig.get_config()
        config = GoogleDriveConfig.objects.select_for_update().get(pk=1)
        has_existing_key = bool((config.master_key_wrapped or "").strip())
        has_encrypted_backups = BackupRecord.objects.filter(is_encrypted=True).exists()

        key_is_valid = False
        if has_existing_key:
            try:
                unwrap_key(config.master_key_wrapped)
                key_is_valid = True
            except ValueError:
                key_is_valid = False

        if action in ("generate", "rotate"):
            if has_encrypted_backups:
                msg = (
                    "Key rotation/replacement blocked: existing encrypted backups depend on the current key "
                    "and key-version retention is not yet implemented."
                )
                messages.error(request, msg)
                return HttpResponse(msg, status=409)

            if has_existing_key and not key_is_valid:
                msg = "Existing encryption key is corrupt or unreadable; refusing destructive replacement."
                messages.error(request, msg)
                return HttpResponse(msg, status=409)

            if action == "generate" and has_existing_key:
                msg = "An encryption key already exists; repeated generate cannot replace it."
                messages.error(request, msg)
                return HttpResponse(msg, status=409)

            if action == "rotate" and not has_existing_key:
                msg = "No existing encryption key to rotate. Generate an initial key first."
                messages.error(request, msg)
                return HttpResponse(msg, status=409)

            config.master_key_wrapped = generate_wrapped_key()
            config.encryption_enabled = True
            config.save(update_fields=["master_key_wrapped", "encryption_enabled", "updated_at"])
            label = "generated" if action == "generate" else "rotated"
            messages.success(
                request,
                f"Encryption key {label}. New backups will be encrypted.",
            )

        elif action == "toggle":
            if not config.encryption_enabled and (not has_existing_key or not key_is_valid):
                msg = "Cannot enable encryption without a valid encryption key."
                messages.error(request, msg)
                return HttpResponse(msg, status=409)
            config.encryption_enabled = not config.encryption_enabled
            config.save(update_fields=["encryption_enabled", "updated_at"])
            state = "enabled" if config.encryption_enabled else "disabled"
            messages.success(request, f"Backup encryption {state}.")

        else:
            messages.error(request, "Unknown encryption action.")

    if request.headers.get("HX-Request"):
        # Return a small status snippet for htmx swap
        icon = "🔒" if config.encryption_enabled else "🔓"
        return HttpResponse(
            f'<span id="enc-status">{icon} Encryption {"ON" if config.encryption_enabled else "OFF"}</span>'
        )

    return redirect("backups:backup_list")


# ── Portable archive validation & dry-run preview (no import execution) ───────

@_admin_required
@require_POST
def preview_portable_import(request):
    """
    Validate an uploaded Trackme portable ZIP archive and return a dry-run
    additive import preview bound to the requesting user and archive digest.
    """
    import uuid
    from .importer import (
        ArchiveLimitError,
        ArchiveLimits,
        ArchiveValidationError,
        STAGING_DIR,
        bind_preview_to_user,
        cleanup_expired_staging,
        validate_and_preview_archive,
    )

    cleanup_expired_staging()
    uploaded = request.FILES.get("archive")
    if not uploaded:
        return JsonResponse({"error": "No archive file uploaded."}, status=400)

    limits = ArchiveLimits()
    if uploaded.size is not None and uploaded.size > limits.max_compressed_bytes:
        return JsonResponse(
            {"error": f"Compressed archive exceeds limit ({limits.max_compressed_bytes} bytes)."},
            status=413,
        )

    os.makedirs(STAGING_DIR, exist_ok=True)
    staged_path = os.path.join(STAGING_DIR, f"stage_{request.user.pk}_{uuid.uuid4().hex}.zip")

    try:
        written = 0
        with open(staged_path, "wb") as dst:
            for chunk in uploaded.chunks():
                written += len(chunk)
                if written > limits.max_compressed_bytes:
                    raise ArchiveLimitError(
                        f"Compressed archive exceeds limit ({limits.max_compressed_bytes} bytes)."
                    )
                dst.write(chunk)

        preview = validate_and_preview_archive(staged_path, limits=limits)
        preview_id = bind_preview_to_user(request.user.pk, preview, staged_path)
        return _apply_private_no_store_headers(JsonResponse(
            {
                "preview_id": preview_id,
                "archive_sha256": preview["archive_sha256"],
                "preview": preview,
            },
            status=200,
        ))
    except ArchiveLimitError as exc:
        if os.path.exists(staged_path):
            os.remove(staged_path)
        return JsonResponse({"error": str(exc)}, status=413)
    except ArchiveValidationError as exc:
        if os.path.exists(staged_path):
            os.remove(staged_path)
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception as exc:
        if os.path.exists(staged_path):
            os.remove(staged_path)
        return JsonResponse({"error": f"Preview failed: {exc}"}, status=400)


@_admin_required
def get_import_preview_status(request, preview_id):
    """Return a cached preview only if bound to the requesting user and digest."""
    from .importer import get_bound_preview

    expected_sha256 = request.GET.get("sha256")
    bound = get_bound_preview(preview_id, request.user.pk, expected_sha256=expected_sha256)
    if not bound:
        return JsonResponse({"error": "Preview not found, expired, or unauthorized."}, status=404)
    return JsonResponse(
        {
            "preview_id": bound["preview_id"],
            "archive_sha256": bound["archive_sha256"],
            "preview": bound["preview"],
        },
        status=200,
    )


@_admin_required
@require_POST
def execute_portable_import(request):
    """
    Execute an additive import for a previously validated, user-and-digest-bound
    preview. Revalidates digest and destination conflicts at execution time.
    """
    from django.core.cache import cache
    from .importer import (
        ArchiveLimitError,
        ArchiveValidationError,
        ImportConflictError,
        execute_additive_import,
        get_bound_preview,
    )

    preview_id = (request.POST.get("preview_id") or "").strip()
    archive_sha256 = (request.POST.get("archive_sha256") or "").strip()
    if not preview_id or not archive_sha256:
        return JsonResponse({"error": "preview_id and archive_sha256 are required."}, status=400)

    bound = get_bound_preview(preview_id, request.user.pk, expected_sha256=archive_sha256)
    if not bound:
        return JsonResponse({"error": "Preview not found, expired, or unauthorized."}, status=404)

    staged_path = bound.get("staged_path", "")
    try:
        result = execute_additive_import(staged_path, expected_sha256=archive_sha256)
        cache.delete(f"trackme_import_preview:{preview_id}")
        if staged_path and os.path.exists(staged_path):
            os.remove(staged_path)
        return _apply_private_no_store_headers(JsonResponse(result, status=200))
    except ImportConflictError as exc:
        return JsonResponse({"error": str(exc)}, status=409)
    except ArchiveLimitError as exc:
        return JsonResponse({"error": str(exc)}, status=413)
    except ArchiveValidationError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({"error": f"Import failed: {exc}"}, status=500)
