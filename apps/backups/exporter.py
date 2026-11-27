"""
Portable Trackme ZIP Archive Exporter.

Produces a self-contained, versioned ZIP archive containing:
  - manifest.json: source identity, model versions, schema inventory,
    explicit exclusions, counts, SHA-256 checksums, and consistency proof.
  - data/<app_label>.<ModelName>/chunk_XXXXX.jsonl: chunked, streaming JSONL
    records with stable source-record references, exact Decimal/DateTime/NULL
    preservation, FK references, and M2M relationships (including cyclic).
  - media/<storage_path>: actual bytes of every referenced FileField/ImageField,
    streamed without loading whole files or datasets into memory.
"""

from __future__ import annotations

import datetime
import decimal
import hashlib
import json
import os
import posixpath
import socket
import tempfile
import uuid
import zipfile
from typing import Any, Callable

import django
from django.apps import apps
from django.conf import settings
from django.core.files.storage import default_storage
from django.db import connection, transaction
from django.db.migrations.recorder import MigrationRecorder
from django.db.models import Count, Max, Min
from django.utils import timezone


ARCHIVE_FORMAT = "trackme-portable-archive"
ARCHIVE_FORMAT_VERSION = "1.0.0"
DEFAULT_CHUNK_SIZE = 500
MEDIA_STREAM_CHUNK_BYTES = 64 * 1024

# Apps that hold business domain models in Trackme.
BUSINESS_APP_LABELS = (
    "tenants",
    "accounts",
    "audit",
    "employees",
    "attendance",
    "branches",
    "admin_panel",
    "notifications",
    "backups",
    "leave",
    "projects",
    "schedule",
    "expense",
    "workflow",
    "payroll",
)

# Models explicitly excluded from portable business exports, with reasons.
EXCLUDED_MODELS: dict[str, dict[str, str]] = {
    "sessions.Session": {
        "category": "active_sessions",
        "reason": "Active HTTP session state is ephemeral and environment-bound.",
    },
    "accounts.UserSession": {
        "category": "active_sessions",
        "reason": "Active user session keys and IP/device session state are excluded.",
    },
    "accounts.PasswordResetOTP": {
        "category": "otps_and_tokens",
        "reason": "One-time passwords and reset tokens are transient security secrets.",
    },
    "accounts.TrustedDevice": {
        "category": "security_credentials",
        "reason": "Device trust hashes are environment-specific security tokens.",
    },
    "accounts.LoginProtection": {
        "category": "ephemeral_security_state",
        "reason": "Transient brute-force lockout counters are not portable business data.",
    },
    "accounts.WorkspaceLockEvent": {
        "category": "active_sessions",
        "reason": "Session-bound workspace lock events reference ephemeral UserSession rows.",
    },
    "accounts.UserSecurityProfile": {
        "category": "security_credentials",
        "reason": "Contains MFA TOTP secrets, backup codes, PIN hashes, and workspace password hashes.",
    },
    "backups.GoogleDriveConfig": {
        "category": "external_service_credentials",
        "reason": "Contains Google Service Account JSON credentials and wrapped backup encryption keys.",
    },
    "backups.BackupRecord": {
        "category": "operational_backup_metadata",
        "reason": "Local backup execution history is operational metadata, not restorable business data.",
    },
    "admin_panel.AISetting": {
        "category": "external_service_credentials",
        "reason": "Contains external AI provider API keys and fallback credential configs.",
    },
    "notifications.WebPushSubscription": {
        "category": "external_service_credentials",
        "reason": "Contains browser push subscription endpoints and p256dh/auth keys.",
    },
    "tenants.TenantTestModel": {
        "category": "test_only",
        "reason": "Internal tenant isolation test fixture model.",
    },
    "admin.LogEntry": {
        "category": "framework_internal",
        "reason": "Django admin log is framework-internal; business audit uses audit.AuditEvent.",
    },
    "contenttypes.ContentType": {
        "category": "framework_internal",
        "reason": "Framework-managed registry; references are exported as natural keys.",
    },
    "auth.Permission": {
        "category": "framework_internal",
        "reason": "Framework-managed permissions; Trackme RBAC uses accounts.Permission.",
    },
    "auth.Group": {
        "category": "framework_internal",
        "reason": "Framework-managed groups; Trackme RBAC uses accounts.Role.",
    },
}

# Field-level exclusions on exported models (secrets, session keys, unused framework M2Ms).
EXCLUDED_FIELDS: dict[str, dict[str, str]] = {
    "accounts.CustomUser": {
        "password": "Credential hash excluded from portable business archive.",
        "active_session_key": "Active session key excluded.",
        "groups": "References excluded framework auth.Group.",
        "user_permissions": "References excluded framework auth.Permission.",
    },
    "tenants.CompanyConfiguration": {
        "api_secret": "External API secret excluded from portable business archive.",
    },
}


class IncompleteBackupError(RuntimeError):
    """Raised when referenced media is missing or unreadable."""


class ExportConsistencyError(RuntimeError):
    """Raised when concurrent data changes are detected during export."""


_SENSITIVE_AUDIT_OBJECT_TYPES = frozenset({
    "GoogleDriveConfig",
    "UserSecurityProfile",
    "PasswordResetOTP",
    "UserSession",
    "TrustedDevice",
    "AISetting",
    "WebPushSubscription",
})


def _make_record_ref(model_label: str, pk: Any) -> str:
    return f"{model_label}:{pk}"


def _serialize_scalar(value: Any, field) -> Any:
    if value is None:
        return None
    internal_type = field.get_internal_type()
    if internal_type == "DecimalField":
        if isinstance(value, decimal.Decimal):
            return str(value)
        return str(decimal.Decimal(value))
    if internal_type == "DateTimeField":
        if isinstance(value, datetime.datetime):
            if timezone.is_naive(value) and settings.USE_TZ:
                value = timezone.make_aware(value, timezone.get_default_timezone())
            return value.isoformat()
        return str(value)
    if internal_type in ("DateField", "TimeField"):
        return value.isoformat() if hasattr(value, "isoformat") else str(value)
    if internal_type == "UUIDField":
        return str(value)
    if internal_type == "FileField":
        return value.name if getattr(value, "name", "") else None
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _get_model_versions() -> dict[str, str]:
    """Return latest applied migration name per app_label."""
    versions: dict[str, str] = {}
    try:
        rows = (
            MigrationRecorder.Migration.objects.order_by("app", "id")
            .values_list("app", "name")
        )
        for app_label, name in rows:
            versions[app_label] = name
    except Exception:
        pass
    return versions


def inventory_models() -> tuple[list[Any], dict[str, dict[str, str]], dict[str, Any]]:
    """
    Dynamically inventory every concrete, non-proxy model in the project.

    Returns:
        (included_models, excluded_models_info, completeness_report)
    """
    included: list[Any] = []
    excluded_info: dict[str, dict[str, str]] = {}

    for model in apps.get_models():
        if model._meta.proxy or model._meta.abstract:
            continue
        label = model._meta.label
        if label in EXCLUDED_MODELS:
            excluded_info[label] = dict(EXCLUDED_MODELS[label])
            continue
        if model._meta.app_label in BUSINESS_APP_LABELS:
            included.append(model)
        else:
            excluded_info[label] = {
                "category": "non_business_app",
                "reason": f"App '{model._meta.app_label}' is outside Trackme business modules.",
            }

    included.sort(key=lambda m: m._meta.label)
    covered_apps = sorted({m._meta.app_label for m in included})
    # Every business app that defines concrete models should be represented.
    apps_with_models = sorted(
        {
            m._meta.app_label
            for m in apps.get_models()
            if not m._meta.proxy
            and not m._meta.abstract
            and m._meta.app_label in BUSINESS_APP_LABELS
            and m._meta.label not in EXCLUDED_MODELS
        }
    )
    missing_apps = sorted(set(apps_with_models) - set(covered_apps))

    completeness = {
        "all_business_modules_covered": len(missing_apps) == 0,
        "business_apps_covered": covered_apps,
        "missing_business_modules": missing_apps,
    }
    return included, excluded_info, completeness


def _capture_model_signature(model) -> dict[str, Any]:
    """Lightweight aggregate signature to detect concurrent inserts/deletes/updates."""
    qs = model._default_manager.all()
    agg_kwargs = {
        "_cnt": Count("pk"),
        "_min_pk": Min("pk"),
        "_max_pk": Max("pk"),
    }
    field_names = {f.name for f in model._meta.concrete_fields}
    has_updated_at = "updated_at" in field_names
    if has_updated_at:
        agg_kwargs["_max_updated"] = Max("updated_at")

    res = qs.aggregate(**agg_kwargs)
    return {
        "count": res["_cnt"] or 0,
        "min_pk": str(res["_min_pk"]) if res["_min_pk"] is not None else None,
        "max_pk": str(res["_max_pk"]) if res["_max_pk"] is not None else None,
        "max_updated": (
            res["_max_updated"].isoformat()
            if has_updated_at and res.get("_max_updated") is not None
            else None
        ),
    }


def _normalize_archive_media_path(storage_name: str) -> str:
    clean = storage_name.replace("\\", "/").lstrip("/")
    norm = posixpath.normpath(clean)
    if norm.startswith("..") or posixpath.isabs(norm):
        raise IncompleteBackupError(f"Unsafe media path encountered: {storage_name!r}")
    return f"media/{norm}"


def _stream_media_into_zip(
    zf: zipfile.ZipFile,
    storage_name: str,
    model_label: str,
    pk: Any,
    field_name: str,
    archived_media: dict[str, dict[str, Any]],
) -> str:
    """
    Stream a referenced media file into the ZIP archive in 64KB chunks.
    Raises IncompleteBackupError if the file is missing or unreadable.
    """
    if storage_name in archived_media:
        entry = archived_media[storage_name]
        entry["references"].append(
            {"model": model_label, "pk": pk, "field": field_name}
        )
        return entry["archive_path"]

    try:
        exists = default_storage.exists(storage_name)
    except Exception as exc:
        raise IncompleteBackupError(
            f"Incomplete backup: unable to check media '{storage_name}' "
            f"referenced by {model_label}:{pk}.{field_name}: {exc}"
        ) from exc

    if not exists:
        raise IncompleteBackupError(
            f"Incomplete backup: missing referenced media '{storage_name}' "
            f"for {model_label}:{pk}.{field_name}"
        )

    archive_path = _normalize_archive_media_path(storage_name)
    sha256 = hashlib.sha256()
    size = 0

    try:
        with default_storage.open(storage_name, "rb") as src:
            with zf.open(archive_path, "w") as dst:
                while True:
                    buf = src.read(MEDIA_STREAM_CHUNK_BYTES)
                    if not buf:
                        break
                    dst.write(buf)
                    sha256.update(buf)
                    size += len(buf)
    except IncompleteBackupError:
        raise
    except Exception as exc:
        raise IncompleteBackupError(
            f"Incomplete backup: failed reading media '{storage_name}' "
            f"for {model_label}:{pk}.{field_name}: {exc}"
        ) from exc

    archived_media[storage_name] = {
        "archive_path": archive_path,
        "storage_name": storage_name,
        "size": size,
        "sha256": sha256.hexdigest(),
        "references": [{"model": model_label, "pk": pk, "field": field_name}],
    }
    return archive_path


def _build_content_type_map() -> dict[int, str]:
    from django.contrib.contenttypes.models import ContentType

    return {
        ct.pk: f"{ct.app_label}.{ct.model}"
        for ct in ContentType.objects.all()
    }


def _get_source_identity() -> dict[str, Any]:
    tenants_info: list[dict[str, Any]] = []
    try:
        Tenant = apps.get_model("tenants", "Tenant")
        tenants_info = [
            {
                "id": t.pk,
                "uuid": str(t.uuid),
                "slug": t.slug,
                "name": t.name,
                "status": t.status,
            }
            for t in Tenant.objects.order_by("pk")
        ]
    except Exception:
        tenants_info = []

    return {
        "project": "Trackme",
        "django_version": django.get_version(),
        "db_vendor": connection.vendor,
        "time_zone": settings.TIME_ZONE,
        "host": socket.gethostname(),
        "tenants": tenants_info,
    }


def export_portable_archive(
    output_path: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    pre_finalize_hook: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """
    Stream all business data and referenced media into a portable Trackme ZIP archive.

    Args:
        output_path: Destination .zip file path.
        chunk_size: Number of DB rows per keyset-paginated JSONL chunk.
        pre_finalize_hook: Optional hook invoked after data export and before
            consistency verification (used in tests to simulate concurrent edits).

    Returns:
        The manifest dict written into manifest.json.
    """
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    tmp_fd, tmp_zip_path = tempfile.mkstemp(
        dir=os.path.dirname(os.path.abspath(output_path)),
        suffix=".zip.tmp",
    )
    os.close(tmp_fd)

    included_models, excluded_models_info, completeness = inventory_models()
    if not completeness["all_business_modules_covered"]:
        raise RuntimeError(
            f"Incomplete model inventory: missing business modules {completeness['missing_business_modules']}"
        )

    model_versions = _get_model_versions()
    ct_map = _build_content_type_map()
    archived_media: dict[str, dict[str, Any]] = {}
    models_manifest: dict[str, Any] = {}
    pre_signatures: dict[str, dict[str, Any]] = {}
    total_records = 0

    try:
        with transaction.atomic():
            # Capture pre-export consistency baseline for every included model.
            for model in included_models:
                pre_signatures[model._meta.label] = _capture_model_signature(model)

            with zipfile.ZipFile(
                tmp_zip_path,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
                allowZip64=True,
            ) as zf:
                for model in included_models:
                    label = model._meta.label
                    excluded_field_map = EXCLUDED_FIELDS.get(label, {})
                    pk_field = model._meta.pk

                    concrete_fields = [
                        f for f in model._meta.concrete_fields
                        if f.name not in excluded_field_map
                    ]
                    m2m_fields = [
                        f for f in model._meta.many_to_many
                        if f.name not in excluded_field_map
                    ]

                    fk_meta: dict[str, Any] = {}
                    media_field_names: list[str] = []
                    field_schema: list[dict[str, str]] = []

                    for f in concrete_fields:
                        ftype = f.get_internal_type()
                        field_schema.append({"name": f.name, "type": ftype})
                        if f.is_relation:
                            fk_meta[f.name] = {
                                "target_model": f.related_model._meta.label,
                                "relation_type": ftype,
                            }
                        if ftype == "FileField":
                            media_field_names.append(f.name)

                    m2m_meta: dict[str, Any] = {
                        f.name: {
                            "target_model": f.related_model._meta.label,
                            "through_model": f.remote_field.through._meta.label,
                        }
                        for f in m2m_fields
                    }

                    chunks_meta: list[dict[str, Any]] = []
                    model_record_count = 0
                    last_pk = None
                    chunk_index = 0

                    while True:
                        qs = model._default_manager.order_by("pk")
                        if last_pk is not None:
                            qs = qs.filter(pk__gt=last_pk)
                        batch = list(qs[:chunk_size])
                        if not batch:
                            break

                        chunk_index += 1
                        batch_pks = [obj.pk for obj in batch]
                        last_pk = batch_pks[-1]

                        # Batch-fetch M2M links for this chunk to avoid N+1 queries.
                        m2m_by_pk: dict[str, dict[Any, list[Any]]] = {}
                        for m2m in m2m_fields:
                            through = m2m.remote_field.through
                            from_col = m2m.m2m_column_name()
                            to_col = m2m.m2m_reverse_name()
                            # Map column name to attname on through model
                            from_att = next(
                                tf.attname for tf in through._meta.concrete_fields if tf.column == from_col
                            )
                            to_att = next(
                                tf.attname for tf in through._meta.concrete_fields if tf.column == to_col
                            )
                            links: dict[Any, list[Any]] = {}
                            rows = (
                                through._default_manager
                                .filter(**{f"{from_att}__in": batch_pks})
                                .order_by(from_att, to_att)
                                .values_list(from_att, to_att)
                            )
                            for src_id, dst_id in rows:
                                links.setdefault(src_id, []).append(dst_id)
                            m2m_by_pk[m2m.name] = links

                        chunk_rel_path = f"data/{label}/chunk_{chunk_index:05d}.jsonl"
                        chunk_sha256 = hashlib.sha256()
                        chunk_count = 0
                        chunk_media_queue: list[tuple[str, str, Any, str]] = []

                        with zf.open(chunk_rel_path, "w") as chunk_file:
                            for obj in batch:
                                fields_payload: dict[str, Any] = {}
                                for f in concrete_fields:
                                    if f.is_relation:
                                        raw_fk = getattr(obj, f.attname)
                                        if raw_fk is None:
                                            fields_payload[f.name] = None
                                        elif f.related_model._meta.label == "contenttypes.ContentType":
                                            fields_payload[f.name] = {
                                                "target_model": "contenttypes.ContentType",
                                                "natural_key": ct_map.get(raw_fk),
                                                "pk": raw_fk,
                                            }
                                        else:
                                            target_label = f.related_model._meta.label
                                            fields_payload[f.name] = {
                                                "target_model": target_label,
                                                "pk": raw_fk if isinstance(raw_fk, (int, str)) else str(raw_fk),
                                                "ref": _make_record_ref(target_label, raw_fk),
                                            }
                                    else:
                                        val = getattr(obj, f.attname)
                                        if f.get_internal_type() == "FileField":
                                            storage_name = str(val) if val else ""
                                            if storage_name:
                                                archive_media_path = _normalize_archive_media_path(storage_name)
                                                chunk_media_queue.append(
                                                    (storage_name, label, obj.pk, f.name)
                                                )
                                                fields_payload[f.name] = {
                                                    "storage_name": storage_name,
                                                    "archive_path": archive_media_path,
                                                }
                                            else:
                                                fields_payload[f.name] = None
                                        else:
                                            fields_payload[f.name] = _serialize_scalar(val, f)

                                if label in ("audit.AuditEvent", "audit.TrashEntry"):
                                    if getattr(obj, "object_type", None) in _SENSITIVE_AUDIT_OBJECT_TYPES:
                                        for snap_key in ("before_data", "after_data", "changed_fields", "snapshot_data"):
                                            if snap_key in fields_payload and fields_payload[snap_key]:
                                                fields_payload[snap_key] = {"_redacted": "excluded_credential_or_session_model"}

                                m2m_payload: dict[str, list[dict[str, Any]]] = {}
                                for m2m in m2m_fields:
                                    target_label = m2m.related_model._meta.label
                                    target_ids = m2m_by_pk[m2m.name].get(obj.pk, [])
                                    m2m_payload[m2m.name] = [
                                        {
                                            "target_model": target_label,
                                            "pk": tid if isinstance(tid, (int, str)) else str(tid),
                                            "ref": _make_record_ref(target_label, tid),
                                        }
                                        for tid in target_ids
                                    ]

                                pk_val = obj.pk if isinstance(obj.pk, (int, str)) else str(obj.pk)
                                record_obj = {
                                    "ref": _make_record_ref(label, pk_val),
                                    "model": label,
                                    "pk": pk_val,
                                    "fields": fields_payload,
                                    "m2m": m2m_payload,
                                }
                                line_bytes = (
                                    json.dumps(record_obj, ensure_ascii=False, separators=(",", ":")) + "\n"
                                ).encode("utf-8")
                                chunk_file.write(line_bytes)
                                chunk_sha256.update(line_bytes)
                                chunk_count += 1

                        # ZipFile permits only one open write handle at a time, so
                        # stream queued media for this chunk after closing chunk_file.
                        for storage_name, m_label, m_pk, m_field in chunk_media_queue:
                            _stream_media_into_zip(
                                zf=zf,
                                storage_name=storage_name,
                                model_label=m_label,
                                pk=m_pk,
                                field_name=m_field,
                                archived_media=archived_media,
                            )

                        chunks_meta.append(
                            {
                                "path": chunk_rel_path,
                                "record_count": chunk_count,
                                "sha256": chunk_sha256.hexdigest(),
                            }
                        )
                        model_record_count += chunk_count

                    total_records += model_record_count
                    models_manifest[label] = {
                        "model_version": model_versions.get(model._meta.app_label, "unmigrated"),
                        "pk_field": pk_field.name,
                        "record_count": model_record_count,
                        "chunks": chunks_meta,
                        "fields": field_schema,
                        "foreign_keys": fk_meta,
                        "many_to_many": m2m_meta,
                        "media_fields": media_field_names,
                        "excluded_fields": excluded_field_map,
                    }

                if pre_finalize_hook is not None:
                    pre_finalize_hook()

                # Verify no concurrent inserts/deletes/updates occurred during export.
                inconsistencies: list[str] = []
                for model in included_models:
                    label = model._meta.label
                    pre_sig = pre_signatures[label]
                    post_sig = _capture_model_signature(model)
                    exported_cnt = models_manifest[label]["record_count"]
                    if exported_cnt != pre_sig["count"] or post_sig != pre_sig:
                        inconsistencies.append(
                            f"{label}: pre={pre_sig}, exported={exported_cnt}, post={post_sig}"
                        )

                if inconsistencies:
                    raise ExportConsistencyError(
                        "Inconsistent export detected due to concurrent data changes: "
                        + "; ".join(inconsistencies[:5])
                    )

                media_files_list = sorted(
                    archived_media.values(),
                    key=lambda item: item["archive_path"],
                )
                total_media_bytes = sum(item["size"] for item in media_files_list)

                manifest = {
                    "format": ARCHIVE_FORMAT,
                    "format_version": ARCHIVE_FORMAT_VERSION,
                    "created_at": timezone.now().isoformat(),
                    "source_identity": _get_source_identity(),
                    "model_versions": model_versions,
                    "consistency": {
                        "snapshot_strategy": "atomic_with_pre_post_signature_verification",
                        "consistent": True,
                        "verified_models": len(included_models),
                    },
                    "completeness": {
                        **completeness,
                        "excluded_models": excluded_models_info,
                        "excluded_fields": EXCLUDED_FIELDS,
                    },
                    "models": models_manifest,
                    "media": {
                        "file_count": len(media_files_list),
                        "total_bytes": total_media_bytes,
                        "files": media_files_list,
                    },
                    "totals": {
                        "model_count": len(models_manifest),
                        "record_count": total_records,
                        "media_count": len(media_files_list),
                    },
                }

                manifest_bytes = json.dumps(
                    manifest,
                    indent=2,
                    ensure_ascii=False,
                ).encode("utf-8")
                with zf.open("manifest.json", "w") as mf:
                    mf.write(manifest_bytes)

        os.replace(tmp_zip_path, output_path)
        return manifest
    except Exception:
        if os.path.exists(tmp_zip_path):
            os.remove(tmp_zip_path)
        raise


def create_portable_backup(
    backup_type: str = "manual",
    created_by=None,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    pre_finalize_hook: Callable[[], None] | None = None,
):
    """
    Create a portable Trackme ZIP BackupRecord without generating SQLite/SQL sidecars.
    Respects existing GoogleDriveConfig encryption and cleanup policies.
    """
    from .encryption import encrypt_file
    from .gdrive import upload_to_drive
    from .models import BackupRecord, GoogleDriveConfig
    from .utils import BACKUP_DIR, cleanup_old_backups

    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
    filename = f"trackme_backup_{timestamp}.zip"
    filepath = os.path.join(BACKUP_DIR, filename)

    backup = BackupRecord.objects.create(
        backup_type=backup_type,
        status="pending",
        file_name=filename,
        file_path=filepath,
        created_by=created_by,
    )

    try:
        manifest = export_portable_archive(
            filepath,
            chunk_size=chunk_size,
            pre_finalize_hook=pre_finalize_hook,
        )

        backup.file_size = os.path.getsize(filepath)
        backup.record_count = manifest["totals"]["record_count"]

        config = GoogleDriveConfig.get_config()
        if config.encryption_enabled and config.master_key_wrapped:
            try:
                encrypt_file(filepath, filepath, config.master_key_wrapped)
                backup.is_encrypted = True
            except Exception as enc_err:
                backup.status = "failed"
                backup.error_message = f"Encryption failed: {enc_err}"
                backup.save(update_fields=["status", "error_message", "is_encrypted"])
                raise RuntimeError(f"Encryption failed: {enc_err}") from enc_err

        backup.status = "completed"
        backup.save()

        if config.is_enabled and config.auto_upload_to_drive:
            try:
                upload_to_drive(
                    backup,
                    filepath,
                    config,
                    mimetype="application/zip",
                    is_primary=True,
                )
            except Exception as exc:
                backup.error_message = f"Drive upload failed: {exc}"
                backup.save(update_fields=["error_message"])

        cleanup_old_backups(config.keep_local_copies)
        return backup
    except Exception as exc:
        if os.path.exists(filepath):
            os.remove(filepath)
        backup.status = "failed"
        backup.error_message = str(exc)
        backup.save(update_fields=["status", "error_message"])
        raise
