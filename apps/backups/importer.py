"""
Portable Trackme ZIP Archive Validator & Additive Dry-Run Previewer.

Validates uploaded portable archives against strict resource, path, schema,
checksum, type, and reference rules, then computes a read-only additive import
preview (creates, exact duplicates, conflicts, unresolved references) using
stable natural identities — never assuming source PKs identify destination rows
and never mutating business data.
"""

from __future__ import annotations

import datetime
import decimal
import hashlib
import json
import os
import posixpath
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from typing import Any

from django.apps import apps
from django.conf import settings
from django.core.cache import cache
from django.core.files.base import File
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone

from .exporter import (
    ARCHIVE_FORMAT,
    ARCHIVE_FORMAT_VERSION,
    EXCLUDED_FIELDS,
    inventory_models,
)

DEFAULT_MAX_COMPRESSED_BYTES = 50 * 1024 * 1024  # 50 MiB
DEFAULT_MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024  # 200 MiB
DEFAULT_MAX_ENTRY_COUNT = 10_000
DEFAULT_MAX_COMPRESSION_RATIO = 100.0
DEFAULT_MAX_SINGLE_FILE_BYTES = 25 * 1024 * 1024  # 25 MiB
STREAM_READ_CHUNK_BYTES = 64 * 1024
PREVIEW_TTL_SECONDS = 15 * 60

STAGING_DIR = os.path.join(settings.BASE_DIR, "backups", "staging")

UNSAFE_MEDIA_EXTENSIONS = frozenset({
    ".exe", ".bat", ".cmd", ".sh", ".bash", ".ps1", ".vbs", ".vbe",
    ".js", ".jse", ".wsf", ".wsh", ".msi", ".msp", ".dll", ".so",
    ".dylib", ".py", ".pyc", ".php", ".phtml", ".jsp", ".asp", ".aspx",
    ".cgi", ".pl", ".scr", ".com", ".jar", ".hta",
})

# Stable natural-identity lookup rules per model.
# Values are tuples of field names (or ("fk_field", ...) resolved via archive->dest map).
STABLE_IDENTITY_RULES: dict[str, list[tuple[str, ...]]] = {
    "tenants.Tenant": [("slug",), ("uuid",)],
    "tenants.CompanyConfiguration": [("tenant",)],
    "tenants.TenantMembership": [("tenant", "user")],
    "accounts.Module": [("code",)],
    "accounts.Action": [("code",)],
    "accounts.Permission": [("codename",), ("module", "action")],
    "accounts.PermissionDependency": [("permission", "requires_permission")],
    "accounts.Role": [("code",), ("name",)],
    "accounts.RolePermission": [("role", "permission")],
    "accounts.UserRoleAssignment": [("user", "role")],
    "accounts.UserPermissionOverride": [("user", "permission")],
    "accounts.ApprovalChainStep": [("policy", "step_number")],
    "accounts.CustomUser": [("email",)],
    "accounts.SecurityPolicy": [("role",)],
    "branches.Branch": [("name",)],
    "branches.OfficeSchedule": [("branch",)],
    "branches.Holiday": [("branch", "date", "name")],
    "employees.Department": [("name",)],
    "employees.Designation": [("name", "department")],
    "employees.Bank": [("code",), ("name",)],
    "employees.BankBranch": [("bank", "routing_number")],
    "employees.Employee": [("employee_number",), ("uuid",), ("user",)],
    "employees.EmployeeProfile": [("employee_id",), ("user",), ("phone",)],
    "employees.EmployeeLeaveRule": [("employee", "leave_type")],
    "employees.EmployeeLocationSync": [("sync_uuid",)],
    "employees.Asset": [("asset_tag",)],
    "employees.EmployeeDocument": [("employee", "title", "document_type")],
    "employees.EmployeeBankAccount": [("employee", "bank", "account_number")],
    "attendance.AttendancePolicy": [("branch",)],
    "attendance.Attendance": [("sync_uuid",), ("employee", "date")],
    "attendance.AttendanceLocation": [("sync_uuid",)],
    "attendance.AttendanceAbsentLog": [("employee", "date")],
    "attendance.OvertimeRequest": [("employee", "date")],
    "attendance.SyncLog": [("sync_batch_id",)],
    "leave.LeaveType": [("name",)],
    "leave.LeaveBalance": [("employee", "leave_type", "year")],
    "leave.LeaveRequest": [("sync_uuid",)],
    "projects.ProjectType": [("name",)],
    "projects.Project": [("name", "branch")],
    "projects.TaskTemplate": [("name",)],
    "projects.TaskTemplateItem": [("template", "order", "activity")],
    "projects.ProjectTask": [("project", "order", "activity")],
    "projects.TaskDependency": [("predecessor", "successor")],
    "projects.DailyProgressLog": [("sync_uuid",)],
    "projects.ManpowerDeployment": [("sync_uuid",), ("project", "date", "trade")],
    "projects.ProjectMaterial": [("project", "material_name")],
    "projects.ProjectSignOff": [("project",)],
    "projects.GanttImportBatch": [("uuid",)],
    "expense.ExpenseCategory": [("code",), ("name",)],
    "expense.Expense": [("sync_uuid",)],
    "workflow.WorkflowDefinition": [("code",)],
    "workflow.WorkflowStep": [("workflow", "step_number")],
    "payroll.SalaryComponent": [("code",)],
    "payroll.SalaryStructure": [("name",)],
    "payroll.SalaryStructureComponent": [("salary_structure", "salary_component")],
    "payroll.EmployeeSalaryAssignment": [("employee", "salary_structure", "effective_from")],
    "payroll.PayrollPolicy": [("branch",)],
    "payroll.PayrollConfiguration": [("tenant", "version")],
    "payroll.PayrollRun": [("name", "period_start", "period_end")],
    "payroll.EmployeePayrollCalculation": [("payroll_run", "employee")],
    "payroll.PayrollAdjustment": [("sync_uuid",)],
    "payroll.PayrollPaymentDestination": [("employee",)],
    "audit.AuditEvent": [("uuid",)],
    "audit.PinnedMenuItem": [("user", "menu_key")],
}


class ArchiveValidationError(ValueError):
    """Base error for rejected or invalid Trackme archives."""


class ArchiveLimitError(ArchiveValidationError):
    """Raised when compressed/uncompressed size, entry count, or ratio limits are exceeded."""


class ImportConflictError(ArchiveValidationError):
    """Raised when destination conflicts or unresolved references block additive import."""


@dataclass
class ArchiveLimits:
    max_compressed_bytes: int = DEFAULT_MAX_COMPRESSED_BYTES
    max_uncompressed_bytes: int = DEFAULT_MAX_UNCOMPRESSED_BYTES
    max_entry_count: int = DEFAULT_MAX_ENTRY_COUNT
    max_compression_ratio: float = DEFAULT_MAX_COMPRESSION_RATIO
    max_single_file_bytes: int = DEFAULT_MAX_SINGLE_FILE_BYTES


@dataclass
class PreviewBucket:
    creates: list[dict[str, Any]] = field(default_factory=list)
    exact_duplicates: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    unresolved_references: list[dict[str, Any]] = field(default_factory=list)


def cleanup_expired_staging(ttl_seconds: int = PREVIEW_TTL_SECONDS) -> None:
    """Remove expired private staged archive files."""
    if not os.path.isdir(STAGING_DIR):
        return
    cutoff = time.time() - ttl_seconds
    for name in os.listdir(STAGING_DIR):
        path = os.path.join(STAGING_DIR, name)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            pass


def compute_file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            buf = fh.read(STREAM_READ_CHUNK_BYTES)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def _validate_member_path(name: str) -> str:
    if not name or "\x00" in name or "\\" in name:
        raise ArchiveValidationError(f"Unsafe archive entry path: {name!r}")
    if name.startswith("/") or (len(name) >= 2 and name[1] == ":"):
        raise ArchiveValidationError(f"Absolute path not allowed in archive: {name!r}")
    parts = name.split("/")
    if any(p in ("..", ".", "") for p in parts):
        raise ArchiveValidationError(f"Path traversal or empty segment not allowed: {name!r}")
    norm = posixpath.normpath(name)
    if norm != name or norm.startswith(".."):
        raise ArchiveValidationError(f"Non-canonical archive path rejected: {name!r}")
    return norm


def _is_symlink_zipinfo(info: zipfile.ZipInfo) -> bool:
    unix_mode = (info.external_attr >> 16) & 0o170000
    return unix_mode == 0o120000


def _read_zip_member_bounded(
    zf: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    limits: ArchiveLimits,
    total_uncompressed_so_far: int,
) -> tuple[bytes, str, int]:
    """
    Stream a ZIP member with hard per-file, total-uncompressed, and
    compression-ratio enforcement so zip bombs stop immediately.
    """
    actual_bytes = 0
    sha256 = hashlib.sha256()
    chunks: list[bytes] = []
    comp_size = max(info.compress_size, 1)

    with zf.open(info, "r") as stream:
        while True:
            buf = stream.read(STREAM_READ_CHUNK_BYTES)
            if not buf:
                break
            actual_bytes += len(buf)
            total_uncompressed_so_far += len(buf)

            if actual_bytes > limits.max_single_file_bytes:
                raise ArchiveLimitError(
                    f"Entry '{info.filename}' exceeds max single-file limit "
                    f"({limits.max_single_file_bytes} bytes)."
                )
            if total_uncompressed_so_far > limits.max_uncompressed_bytes:
                raise ArchiveLimitError(
                    f"Archive exceeds max uncompressed size limit "
                    f"({limits.max_uncompressed_bytes} bytes)."
                )
            if info.compress_size == 0 and actual_bytes > 0:
                raise ArchiveLimitError(
                    f"Entry '{info.filename}' has zero compressed size with non-empty payload."
                )
            if (actual_bytes / comp_size) > limits.max_compression_ratio:
                raise ArchiveLimitError(
                    f"Entry '{info.filename}' exceeds max compression ratio "
                    f"({limits.max_compression_ratio}x)."
                )

            sha256.update(buf)
            chunks.append(buf)

    return b"".join(chunks), sha256.hexdigest(), total_uncompressed_so_far


def _verify_zip_media_bounded(
    zf: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    limits: ArchiveLimits,
    total_uncompressed_so_far: int,
) -> tuple[int, str, int]:
    """Stream a media entry without buffering its full bytes in memory."""
    actual_bytes = 0
    sha256 = hashlib.sha256()
    comp_size = max(info.compress_size, 1)

    with zf.open(info, "r") as stream:
        while True:
            buf = stream.read(STREAM_READ_CHUNK_BYTES)
            if not buf:
                break
            actual_bytes += len(buf)
            total_uncompressed_so_far += len(buf)

            if actual_bytes > limits.max_single_file_bytes:
                raise ArchiveLimitError(
                    f"Media '{info.filename}' exceeds max single-file limit "
                    f"({limits.max_single_file_bytes} bytes)."
                )
            if total_uncompressed_so_far > limits.max_uncompressed_bytes:
                raise ArchiveLimitError(
                    f"Archive exceeds max uncompressed size limit "
                    f"({limits.max_uncompressed_bytes} bytes)."
                )
            if info.compress_size == 0 and actual_bytes > 0:
                raise ArchiveLimitError(
                    f"Media '{info.filename}' has zero compressed size with non-empty payload."
                )
            if (actual_bytes / comp_size) > limits.max_compression_ratio:
                raise ArchiveLimitError(
                    f"Media '{info.filename}' exceeds max compression ratio "
                    f"({limits.max_compression_ratio}x)."
                )
            sha256.update(buf)

    return actual_bytes, sha256.hexdigest(), total_uncompressed_so_far


def _parse_and_validate_scalar(value: Any, field, model_label: str, pk: Any) -> Any:
    if value is None:
        if not field.null and not field.primary_key:
            raise ArchiveValidationError(
                f"{model_label}:{pk}.{field.name} is null for non-nullable field."
            )
        return None

    ftype = field.get_internal_type()
    if ftype == "DecimalField":
        if isinstance(value, bool):
            raise ArchiveValidationError(f"{model_label}:{pk}.{field.name} invalid Decimal.")
        try:
            d = decimal.Decimal(str(value))
        except (decimal.InvalidOperation, ValueError, TypeError) as exc:
            raise ArchiveValidationError(
                f"{model_label}:{pk}.{field.name} invalid Decimal {value!r}."
            ) from exc
        if not d.is_finite():
            raise ArchiveValidationError(f"{model_label}:{pk}.{field.name} non-finite Decimal.")
        return d

    if ftype == "DateTimeField":
        if not isinstance(value, str):
            raise ArchiveValidationError(f"{model_label}:{pk}.{field.name} expected ISO datetime string.")
        try:
            dt = datetime.datetime.fromisoformat(value)
        except ValueError as exc:
            raise ArchiveValidationError(
                f"{model_label}:{pk}.{field.name} invalid ISO datetime {value!r}."
            ) from exc
        if settings.USE_TZ and timezone.is_naive(dt):
            raise ArchiveValidationError(
                f"{model_label}:{pk}.{field.name} must be timezone-aware."
            )
        return dt

    if ftype == "DateField":
        if not isinstance(value, str):
            raise ArchiveValidationError(f"{model_label}:{pk}.{field.name} expected ISO date string.")
        try:
            return datetime.date.fromisoformat(value)
        except ValueError as exc:
            raise ArchiveValidationError(
                f"{model_label}:{pk}.{field.name} invalid ISO date {value!r}."
            ) from exc

    if ftype == "TimeField":
        if not isinstance(value, str):
            raise ArchiveValidationError(f"{model_label}:{pk}.{field.name} expected ISO time string.")
        try:
            return datetime.time.fromisoformat(value)
        except ValueError as exc:
            raise ArchiveValidationError(
                f"{model_label}:{pk}.{field.name} invalid ISO time {value!r}."
            ) from exc

    if ftype == "UUIDField":
        try:
            return uuid.UUID(str(value))
        except (ValueError, TypeError) as exc:
            raise ArchiveValidationError(
                f"{model_label}:{pk}.{field.name} invalid UUID {value!r}."
            ) from exc

    if ftype in ("IntegerField", "BigAutoField", "PositiveIntegerField", "PositiveBigIntegerField"):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ArchiveValidationError(
                f"{model_label}:{pk}.{field.name} expected integer, got {type(value).__name__}."
            )
        if ftype.startswith("Positive") and value < 0:
            raise ArchiveValidationError(
                f"{model_label}:{pk}.{field.name} negative value {value} for positive integer field."
            )
        return value

    if ftype == "BooleanField":
        if not isinstance(value, bool):
            raise ArchiveValidationError(f"{model_label}:{pk}.{field.name} expected bool.")
        return value

    if ftype == "FloatField":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ArchiveValidationError(f"{model_label}:{pk}.{field.name} expected float.")
        return float(value)

    return value


def _values_equal(archive_val: Any, db_val: Any, field) -> bool:
    if archive_val is None and db_val is None:
        return True
    if archive_val is None or db_val is None:
        return False
    ftype = field.get_internal_type()
    if ftype == "DecimalField":
        return decimal.Decimal(str(archive_val)) == decimal.Decimal(str(db_val))
    if ftype == "DateTimeField":
        if isinstance(archive_val, datetime.datetime) and isinstance(db_val, datetime.datetime):
            return archive_val == db_val
    if ftype == "UUIDField":
        return str(archive_val) == str(db_val)
    return archive_val == db_val


def validate_and_preview_archive(
    zip_path: str,
    *,
    limits: ArchiveLimits | None = None,
) -> dict[str, Any]:
    """
    Validate a Trackme portable ZIP archive and compute a read-only additive
    import preview without writing any business data.
    """
    limits = limits or ArchiveLimits()

    if not os.path.isfile(zip_path):
        raise ArchiveValidationError("Archive file does not exist.")

    compressed_size = os.path.getsize(zip_path)
    if compressed_size > limits.max_compressed_bytes:
        raise ArchiveLimitError(
            f"Compressed archive size ({compressed_size} bytes) exceeds limit "
            f"({limits.max_compressed_bytes} bytes)."
        )

    archive_sha256 = compute_file_sha256(zip_path)

    included_models, _, _ = inventory_models()
    allowed_models = {m._meta.label: m for m in included_models}

    try:
        zf_ctx = zipfile.ZipFile(zip_path, "r")
    except zipfile.BadZipFile as exc:
        raise ArchiveValidationError(f"Invalid ZIP archive: {exc}") from exc

    total_uncompressed = 0
    seen_paths: set[str] = set()
    info_by_path: dict[str, zipfile.ZipInfo] = {}

    with zf_ctx as zf:
        infolist = zf.infolist()
        if len(infolist) > limits.max_entry_count:
            raise ArchiveLimitError(
                f"Archive entry count ({len(infolist)}) exceeds limit ({limits.max_entry_count})."
            )

        declared_uncompressed_sum = 0
        for info in infolist:
            if info.is_dir():
                raise ArchiveValidationError(f"Directory entries are not allowed: {info.filename!r}")
            if _is_symlink_zipinfo(info):
                raise ArchiveValidationError(f"Symlink entry rejected: {info.filename!r}")
            norm_path = _validate_member_path(info.filename)
            if norm_path in seen_paths:
                raise ArchiveValidationError(f"Duplicate archive entry rejected: {norm_path!r}")
            seen_paths.add(norm_path)
            info_by_path[norm_path] = info

            if info.file_size > limits.max_single_file_bytes:
                raise ArchiveLimitError(
                    f"Entry '{norm_path}' declared size ({info.file_size}) exceeds limit "
                    f"({limits.max_single_file_bytes})."
                )
            declared_uncompressed_sum += info.file_size
            if declared_uncompressed_sum > limits.max_uncompressed_bytes:
                raise ArchiveLimitError(
                    f"Archive declared uncompressed size ({declared_uncompressed_sum}) exceeds limit "
                    f"({limits.max_uncompressed_bytes})."
                )
            if info.compress_size == 0 and info.file_size > 0:
                raise ArchiveLimitError(f"Suspicious zero-compressed-size entry: {norm_path!r}")
            if info.compress_size > 0 and (info.file_size / info.compress_size) > limits.max_compression_ratio:
                raise ArchiveLimitError(
                    f"Entry '{norm_path}' declared compression ratio exceeds limit "
                    f"({limits.max_compression_ratio}x)."
                )

            if not (
                norm_path == "manifest.json"
                or norm_path.startswith("data/")
                or norm_path.startswith("media/")
            ):
                raise ArchiveValidationError(f"Unexpected archive entry path: {norm_path!r}")

            if norm_path.startswith("media/"):
                _, ext = posixpath.splitext(norm_path.lower())
                if ext in UNSAFE_MEDIA_EXTENSIONS:
                    raise ArchiveValidationError(f"Unsafe media file extension rejected: {norm_path!r}")

        if "manifest.json" not in info_by_path:
            raise ArchiveValidationError("Missing required manifest.json.")

        manifest_bytes, _, total_uncompressed = _read_zip_member_bounded(
            zf, info_by_path["manifest.json"], limits, total_uncompressed
        )
        try:
            manifest = json.loads(manifest_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArchiveValidationError(f"Invalid manifest.json: {exc}") from exc

        if not isinstance(manifest, dict):
            raise ArchiveValidationError("manifest.json root must be an object.")
        if manifest.get("format") != ARCHIVE_FORMAT or manifest.get("format_version") != ARCHIVE_FORMAT_VERSION:
            raise ArchiveValidationError(
                f"Unsupported archive format/version: {manifest.get('format')!r} / {manifest.get('format_version')!r}"
            )
        if not manifest.get("consistency", {}).get("consistent"):
            raise ArchiveValidationError("Archive is not marked consistent.")

        manifest_models = manifest.get("models")
        if not isinstance(manifest_models, dict):
            raise ArchiveValidationError("manifest.json 'models' must be an object.")

        # Validate model allowlist & verify every data chunk
        for model_label in manifest_models:
            if model_label not in allowed_models:
                raise ArchiveValidationError(
                    f"Disallowed or unsupported model in archive: {model_label!r}"
                )

        # Verify media entries match manifest and checksums
        media_entries = manifest.get("media", {}).get("files", [])
        expected_media_paths: set[str] = set()
        for m_item in media_entries:
            m_path = m_item.get("archive_path", "")
            if m_path not in info_by_path:
                raise ArchiveValidationError(f"Missing media entry declared in manifest: {m_path!r}")
            expected_media_paths.add(m_path)
            actual_size, actual_sha, total_uncompressed = _verify_zip_media_bounded(
                zf, info_by_path[m_path], limits, total_uncompressed
            )
            if actual_size != m_item.get("size") or actual_sha != m_item.get("sha256"):
                raise ArchiveValidationError(f"Media checksum or size mismatch for {m_path!r}.")

        actual_media_paths = {p for p in seen_paths if p.startswith("media/")}
        if actual_media_paths != expected_media_paths:
            extra = sorted(actual_media_paths - expected_media_paths)
            raise ArchiveValidationError(f"Unreferenced media files in archive: {extra[:5]}")

        # Parse & validate all chunks, collecting records for relationship & identity preview
        parsed_records: dict[str, list[dict[str, Any]]] = {}
        all_archive_refs: set[str] = set()
        expected_chunk_paths: set[str] = set()
        total_parsed_records = 0

        for model_label, model_meta in manifest_models.items():
            model_cls = allowed_models[model_label]
            excluded_field_set = set(EXCLUDED_FIELDS.get(model_label, {}).keys())
            concrete_map = {
                f.name: f
                for f in model_cls._meta.concrete_fields
                if f.name not in excluded_field_set
            }
            m2m_map = {
                f.name: f
                for f in model_cls._meta.many_to_many
                if f.name not in excluded_field_set
            }

            model_rows: list[dict[str, Any]] = []
            for chunk_meta in model_meta.get("chunks", []):
                c_path = chunk_meta.get("path", "")
                if c_path not in info_by_path:
                    raise ArchiveValidationError(f"Missing chunk file declared in manifest: {c_path!r}")
                expected_chunk_paths.add(c_path)

                chunk_bytes, chunk_sha, total_uncompressed = _read_zip_member_bounded(
                    zf, info_by_path[c_path], limits, total_uncompressed
                )
                if chunk_sha != chunk_meta.get("sha256"):
                    raise ArchiveValidationError(f"Checksum mismatch for chunk {c_path!r}.")

                lines = chunk_bytes.decode("utf-8").splitlines()
                if len(lines) != chunk_meta.get("record_count"):
                    raise ArchiveValidationError(
                        f"Record count mismatch in {c_path!r}: expected {chunk_meta.get('record_count')}, got {len(lines)}."
                    )

                for line in lines:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ArchiveValidationError(f"Invalid JSONL in {c_path!r}: {exc}") from exc

                    if rec.get("model") != model_label or "ref" not in rec or "pk" not in rec:
                        raise ArchiveValidationError(f"Malformed record envelope in {c_path!r}.")
                    fields_dict = rec.get("fields")
                    m2m_dict = rec.get("m2m", {})
                    if not isinstance(fields_dict, dict) or not isinstance(m2m_dict, dict):
                        raise ArchiveValidationError(f"Malformed fields/m2m in {rec['ref']}.")

                    for fname in fields_dict:
                        if fname not in concrete_map:
                            raise ArchiveValidationError(
                                f"Disallowed or unknown field {model_label}.{fname} in {rec['ref']}."
                            )
                    for mname in m2m_dict:
                        if mname not in m2m_map:
                            raise ArchiveValidationError(
                                f"Disallowed or unknown M2M {model_label}.{mname} in {rec['ref']}."
                            )

                    typed_fields: dict[str, Any] = {}
                    for fname, fval in fields_dict.items():
                        field = concrete_map[fname]
                        if field.is_relation:
                            if fval is None:
                                if not field.null:
                                    raise ArchiveValidationError(
                                        f"Required FK {model_label}.{fname} is null in {rec['ref']}."
                                    )
                                typed_fields[fname] = None
                            else:
                                if not isinstance(fval, dict) or fval.get("target_model") != field.related_model._meta.label:
                                    raise ArchiveValidationError(
                                        f"Invalid FK reference target for {model_label}.{fname} in {rec['ref']}."
                                    )
                                typed_fields[fname] = fval
                        elif field.get_internal_type() == "FileField":
                            if fval is not None:
                                if not isinstance(fval, dict) or fval.get("archive_path") not in expected_media_paths:
                                    raise ArchiveValidationError(
                                        f"Missing media reference for {model_label}.{fname} in {rec['ref']}."
                                    )
                            typed_fields[fname] = fval
                        else:
                            typed_fields[fname] = _parse_and_validate_scalar(
                                fval, field, model_label, rec["pk"]
                            )

                    all_archive_refs.add(rec["ref"])
                    model_rows.append(
                        {
                            "ref": rec["ref"],
                            "pk": rec["pk"],
                            "fields": typed_fields,
                            "m2m": m2m_dict,
                        }
                    )
                    total_parsed_records += 1

            parsed_records[model_label] = model_rows

        actual_chunk_paths = {p for p in seen_paths if p.startswith("data/")}
        if actual_chunk_paths != expected_chunk_paths:
            extra_chunks = sorted(actual_chunk_paths - expected_chunk_paths)
            raise ArchiveValidationError(f"Undeclared data chunks in archive: {extra_chunks[:5]}")

        if total_parsed_records != manifest.get("totals", {}).get("record_count"):
            raise ArchiveValidationError("Manifest total record_count mismatch.")

    # Compute read-only dry-run preview (creates, exact_duplicates, conflicts, unresolved_references)
    bucket = PreviewBucket()
    resolved_ref_to_dest_pk: dict[str, Any] = {}
    per_model_summary: dict[str, dict[str, int]] = {}

    # Multi-pass identity resolution so parent records resolve before children when possible.
    unresolved_queue: list[tuple[str, dict[str, Any]]] = []
    for model_label, rows in parsed_records.items():
        for row in rows:
            unresolved_queue.append((model_label, row))

    max_passes = 3
    for _pass in range(max_passes):
        next_queue: list[tuple[str, dict[str, Any]]] = []
        progress = False
        for model_label, row in unresolved_queue:
            model_cls = allowed_models[model_label]
            concrete_map = {f.name: f for f in model_cls._meta.concrete_fields}
            rules = STABLE_IDENTITY_RULES.get(model_label, [])

            # Check broken FKs (not in archive at all)
            broken_fk = False
            for fname, fval in row["fields"].items():
                field = concrete_map[fname]
                if field.is_relation and fval is not None:
                    if field.related_model._meta.label != "contenttypes.ContentType":
                        target_ref = fval.get("ref")
                        if target_ref not in all_archive_refs:
                            broken_fk = True
                            break

            if broken_fk:
                next_queue.append((model_label, row))
                continue

            # Try matching destination record by stable identity rules
            matched_obj = None
            rule_blocked_by_unresolved_parent = False

            for rule in rules:
                lookup_kwargs: dict[str, Any] = {}
                can_evaluate_rule = True
                for attr in rule:
                    val = row["fields"].get(attr)
                    field = concrete_map[attr]
                    if val is None:
                        can_evaluate_rule = False
                        break
                    if field.is_relation:
                        parent_ref = val.get("ref")
                        if parent_ref in resolved_ref_to_dest_pk:
                            lookup_kwargs[field.attname] = resolved_ref_to_dest_pk[parent_ref]
                        else:
                            can_evaluate_rule = False
                            rule_blocked_by_unresolved_parent = True
                            break
                    else:
                        if model_label == "accounts.CustomUser" and attr == "email":
                            lookup_kwargs["email__iexact"] = val
                        else:
                            lookup_kwargs[attr] = val
                if can_evaluate_rule and lookup_kwargs:
                    matched_obj = model_cls._default_manager.filter(**lookup_kwargs).first()
                    if matched_obj is not None:
                        break

            if matched_obj is not None:
                resolved_ref_to_dest_pk[row["ref"]] = matched_obj.pk
                progress = True

                # Compare fields to classify exact_duplicate vs conflict
                differing_fields: list[str] = []
                pk_name = model_cls._meta.pk.name
                for fname, fval in row["fields"].items():
                    if fname == pk_name:
                        continue
                    field = concrete_map[fname]
                    if field.is_relation:
                        if fval is None:
                            if getattr(matched_obj, field.attname) is not None:
                                differing_fields.append(fname)
                        elif field.related_model._meta.label != "contenttypes.ContentType":
                            expected_dest_pk = resolved_ref_to_dest_pk.get(fval.get("ref"))
                            if expected_dest_pk is not None and getattr(matched_obj, field.attname) != expected_dest_pk:
                                differing_fields.append(fname)
                    elif field.get_internal_type() == "FileField":
                        arch_name = fval.get("storage_name") if isinstance(fval, dict) else None
                        db_name = str(getattr(matched_obj, fname) or "") or None
                        if arch_name != db_name:
                            differing_fields.append(fname)
                    else:
                        if not _values_equal(fval, getattr(matched_obj, fname), field):
                            differing_fields.append(fname)

                summary = per_model_summary.setdefault(
                    model_label, {"creates": 0, "exact_duplicates": 0, "conflicts": 0, "unresolved_references": 0}
                )
                if differing_fields:
                    bucket.conflicts.append(
                        {
                            "ref": row["ref"],
                            "model": model_label,
                            "destination_pk": matched_obj.pk,
                            "differing_fields": differing_fields,
                        }
                    )
                    summary["conflicts"] += 1
                else:
                    bucket.exact_duplicates.append(
                        {
                            "ref": row["ref"],
                            "model": model_label,
                            "destination_pk": matched_obj.pk,
                        }
                    )
                    summary["exact_duplicates"] += 1
            else:
                if rule_blocked_by_unresolved_parent and _pass < max_passes - 1:
                    next_queue.append((model_label, row))
                else:
                    next_queue.append((model_label, row))

        unresolved_queue = next_queue
        if not progress:
            break

    # Remaining records: either creates or unresolved_references (if any FK is broken)
    for model_label, row in unresolved_queue:
        model_cls = allowed_models[model_label]
        concrete_map = {f.name: f for f in model_cls._meta.concrete_fields}
        missing_refs: list[dict[str, Any]] = []
        for fname, fval in row["fields"].items():
            field = concrete_map[fname]
            if field.is_relation and fval is not None:
                if field.related_model._meta.label != "contenttypes.ContentType":
                    target_ref = fval.get("ref")
                    if target_ref not in all_archive_refs:
                        missing_refs.append({"field": fname, "target_ref": target_ref})

        summary = per_model_summary.setdefault(
            model_label, {"creates": 0, "exact_duplicates": 0, "conflicts": 0, "unresolved_references": 0}
        )
        if missing_refs:
            bucket.unresolved_references.append(
                {
                    "ref": row["ref"],
                    "model": model_label,
                    "missing_references": missing_refs,
                }
            )
            summary["unresolved_references"] += 1
        else:
            bucket.creates.append({"ref": row["ref"], "model": model_label})
            summary["creates"] += 1

    return {
        "valid": len(bucket.unresolved_references) == 0,
        "ready_for_additive_import": len(bucket.unresolved_references) == 0 and len(bucket.conflicts) == 0,
        "archive_sha256": archive_sha256,
        "format": manifest["format"],
        "format_version": manifest["format_version"],
        "source_identity": manifest.get("source_identity"),
        "totals": {
            "records": total_parsed_records,
            "creates": len(bucket.creates),
            "exact_duplicates": len(bucket.exact_duplicates),
            "conflicts": len(bucket.conflicts),
            "unresolved_references": len(bucket.unresolved_references),
        },
        "per_model": per_model_summary,
        "creates": bucket.creates,
        "exact_duplicates": bucket.exact_duplicates,
        "conflicts": bucket.conflicts,
        "unresolved_references": bucket.unresolved_references,
    }


def bind_preview_to_user(user_id: int, preview: dict[str, Any], staged_path: str) -> str:
    """
    Bind a validated dry-run preview to the requesting user and archive SHA-256
    in private cache with an expiration TTL.
    """
    preview_id = uuid.uuid4().hex
    cache_key = f"trackme_import_preview:{preview_id}"
    payload = {
        "preview_id": preview_id,
        "user_id": user_id,
        "archive_sha256": preview["archive_sha256"],
        "staged_path": staged_path,
        "created_at": timezone.now().isoformat(),
        "expires_in_seconds": PREVIEW_TTL_SECONDS,
        "preview": preview,
    }
    cache.set(cache_key, payload, timeout=PREVIEW_TTL_SECONDS)
    return preview_id


def get_bound_preview(preview_id: str, user_id: int, expected_sha256: str | None = None) -> dict[str, Any] | None:
    """Retrieve a preview only if it belongs to `user_id` and matches `expected_sha256`."""
    payload = cache.get(f"trackme_import_preview:{preview_id}")
    if not payload:
        return None
    if payload.get("user_id") != user_id:
        return None
    if expected_sha256 is not None and payload.get("archive_sha256") != expected_sha256:
        return None
    return payload


def _topological_model_order(model_labels: list[str], allowed_models: dict[str, Any]) -> list[str]:
    """Order models so parent FK targets precede dependents (ignoring self-FKs)."""
    label_set = set(model_labels)
    deps: dict[str, set[str]] = {lbl: set() for lbl in model_labels}
    for lbl in model_labels:
        m = allowed_models[lbl]
        for f in m._meta.concrete_fields:
            if f.is_relation:
                target = f.related_model._meta.label
                if target in label_set and target != lbl:
                    deps[lbl].add(target)

    ordered: list[str] = []
    remaining = set(model_labels)
    while remaining:
        ready = sorted(lbl for lbl in remaining if not (deps[lbl] & remaining))
        if not ready:
            # Break multi-model cycle deterministically
            ready = [sorted(remaining)[0]]
        for lbl in ready:
            ordered.append(lbl)
            remaining.remove(lbl)
    return ordered


def _build_content_type_reverse_map() -> dict[str, int]:
    from django.contrib.contenttypes.models import ContentType

    return {
        f"{ct.app_label}.{ct.model}": ct.pk
        for ct in ContentType.objects.all()
    }


def _stage_and_place_media(
    zf: zipfile.ZipFile,
    media_entries: list[dict[str, Any]],
    staging_media_dir: str,
    limits: ArchiveLimits,
    placed_media_paths: list[str],
) -> dict[str, str]:
    """
    Stage archive media privately, verify checksums, and place into default_storage
    without ever overwriting existing files. Returns {archive_path: dest_storage_name}.
    """
    import shutil

    os.makedirs(staging_media_dir, exist_ok=True)
    archive_to_dest_storage: dict[str, str] = {}
    total_uncompressed = 0

    for item in media_entries:
        arch_path = item["archive_path"]
        desired_storage_name = item["storage_name"]
        expected_sha = item["sha256"]
        info = zf.getinfo(arch_path)

        staged_file = os.path.join(staging_media_dir, f"{uuid.uuid4().hex}.bin")
        sha256 = hashlib.sha256()
        actual_bytes = 0
        comp_size = max(info.compress_size, 1)

        with zf.open(info, "r") as src, open(staged_file, "wb") as dst:
            while True:
                buf = src.read(STREAM_READ_CHUNK_BYTES)
                if not buf:
                    break
                actual_bytes += len(buf)
                total_uncompressed += len(buf)
                if actual_bytes > limits.max_single_file_bytes or total_uncompressed > limits.max_uncompressed_bytes:
                    raise ArchiveLimitError("Media exceeds size limits during staging.")
                if (actual_bytes / comp_size) > limits.max_compression_ratio:
                    raise ArchiveLimitError("Media exceeds compression ratio limit during staging.")
                sha256.update(buf)
                dst.write(buf)

        if sha256.hexdigest() != expected_sha:
            raise ArchiveValidationError(f"Media checksum mismatch during staging: {arch_path}")

        # Never overwrite existing media in default_storage
        dest_name = desired_storage_name
        if default_storage.exists(dest_name):
            existing_sha = hashlib.sha256()
            with default_storage.open(dest_name, "rb") as existing_fh:
                while True:
                    buf = existing_fh.read(STREAM_READ_CHUNK_BYTES)
                    if not buf:
                        break
                    existing_sha.update(buf)
            if existing_sha.hexdigest() == expected_sha:
                archive_to_dest_storage[arch_path] = dest_name
                continue
            stem, ext = posixpath.splitext(desired_storage_name)
            dest_name = f"{stem}_{expected_sha[:8]}{ext}"
            if default_storage.exists(dest_name):
                dest_name = default_storage.get_available_name(dest_name)

        with open(staged_file, "rb") as fh:
            saved_name = default_storage.save(dest_name, File(fh))
        placed_media_paths.append(saved_name)
        archive_to_dest_storage[arch_path] = saved_name

    shutil.rmtree(staging_media_dir, ignore_errors=True)
    return archive_to_dest_storage


def execute_additive_import(
    zip_path: str,
    *,
    expected_sha256: str,
    limits: ArchiveLimits | None = None,
    post_media_hook=None,
) -> dict[str, Any]:
    """
    Revalidate the archive digest and destination conflicts, then additively
    import new records, remapped FKs/M2Ms, and media without triggering save/signal
    side effects or overwriting existing records/media.
    """
    import shutil

    limits = limits or ArchiveLimits()
    preview = validate_and_preview_archive(zip_path, limits=limits)

    if preview["archive_sha256"] != expected_sha256:
        raise ArchiveValidationError("Archive digest mismatch at execution time.")
    if not preview["valid"]:
        raise ImportConflictError(
            f"Archive has {preview['totals']['unresolved_references']} unresolved references."
        )
    if preview["totals"]["conflicts"] > 0:
        raise ImportConflictError(
            f"Destination conflicts detected ({preview['totals']['conflicts']} records); aborting without changes."
        )

    included_models, _, _ = inventory_models()
    allowed_models = {m._meta.label: m for m in included_models}
    ct_reverse_map = _build_content_type_reverse_map()

    source_ref_to_dest_pk: dict[str, Any] = {}
    for dup in preview["exact_duplicates"]:
        source_ref_to_dest_pk[dup["ref"]] = dup["destination_pk"]

    create_refs = {c["ref"] for c in preview["creates"]}

    staging_media_dir = os.path.join(STAGING_DIR, f"media_{uuid.uuid4().hex}")
    placed_media_paths: list[str] = []

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            media_entries = manifest.get("media", {}).get("files", [])
            model_labels = _topological_model_order(
                list(manifest["models"].keys()), allowed_models
            )

            # Parse records to create in topological order
            records_by_model: dict[str, list[dict[str, Any]]] = {}
            for label in model_labels:
                model_cls = allowed_models[label]
                concrete_map = {f.name: f for f in model_cls._meta.concrete_fields}
                rows: list[dict[str, Any]] = []
                for chunk_meta in manifest["models"][label].get("chunks", []):
                    for line in zf.read(chunk_meta["path"]).decode("utf-8").splitlines():
                        rec = json.loads(line)
                        if rec["ref"] in create_refs:
                            parsed_fields: dict[str, Any] = {}
                            for fname, fval in rec["fields"].items():
                                field = concrete_map[fname]
                                if field.is_relation or field.get_internal_type() == "FileField":
                                    parsed_fields[fname] = fval
                                else:
                                    parsed_fields[fname] = _parse_and_validate_scalar(
                                        fval, field, label, rec["pk"]
                                    )
                            rows.append(
                                {
                                    "ref": rec["ref"],
                                    "pk": rec["pk"],
                                    "fields": parsed_fields,
                                    "m2m": rec.get("m2m", {}),
                                }
                            )
                if rows:
                    records_by_model[label] = rows

            with transaction.atomic():
                archive_to_dest_storage = _stage_and_place_media(
                    zf, media_entries, staging_media_dir, limits, placed_media_paths
                )

                if post_media_hook is not None:
                    post_media_hook()

                deferred_fk_updates: list[tuple[Any, Any, str, str]] = []
                pending_rows: list[tuple[str, dict[str, Any]]] = [
                    (lbl, row)
                    for lbl in model_labels
                    for row in records_by_model.get(lbl, [])
                ]

                created_counts: dict[str, int] = {}
                remapped_pks: dict[str, dict[str, Any]] = {}

                # Multi-pass insert: allocate fresh destination PKs via bulk_create
                # (bypasses custom save() overrides and post_save signals).
                while pending_rows:
                    next_pending: list[tuple[str, dict[str, Any]]] = []
                    progressed = False

                    for label, row in pending_rows:
                        model_cls = allowed_models[label]
                        pk_name = model_cls._meta.pk.name
                        concrete_map = {f.name: f for f in model_cls._meta.concrete_fields}

                        init_kwargs: dict[str, Any] = {}
                        row_deferred_fks: list[tuple[str, str]] = []
                        blocked_on_required_fk = False

                        for fname, fval in row["fields"].items():
                            if fname == pk_name:
                                continue
                            field = concrete_map[fname]
                            if field.is_relation:
                                if fval is None:
                                    init_kwargs[field.attname] = None
                                elif field.related_model._meta.label == "contenttypes.ContentType":
                                    init_kwargs[field.attname] = ct_reverse_map.get(
                                        fval.get("natural_key")
                                    )
                                else:
                                    target_ref = fval.get("ref")
                                    if target_ref in source_ref_to_dest_pk:
                                        init_kwargs[field.attname] = source_ref_to_dest_pk[target_ref]
                                    elif field.null:
                                        init_kwargs[field.attname] = None
                                        row_deferred_fks.append((field.attname, target_ref))
                                    else:
                                        blocked_on_required_fk = True
                                        break
                            elif field.get_internal_type() == "FileField":
                                if fval is None:
                                    init_kwargs[fname] = ""
                                else:
                                    init_kwargs[fname] = archive_to_dest_storage[fval["archive_path"]]
                            else:
                                init_kwargs[fname] = fval

                        if blocked_on_required_fk:
                            next_pending.append((label, row))
                            continue

                        instance = model_cls(**init_kwargs)
                        model_cls._default_manager.bulk_create([instance])
                        dest_pk = instance.pk
                        source_ref_to_dest_pk[row["ref"]] = dest_pk
                        remapped_pks[row["ref"]] = {"source_pk": row["pk"], "destination_pk": dest_pk}
                        created_counts[label] = created_counts.get(label, 0) + 1
                        progressed = True

                        for attname, target_ref in row_deferred_fks:
                            deferred_fk_updates.append((model_cls, dest_pk, attname, target_ref))

                    if not progressed and next_pending:
                        raise ImportConflictError(
                            f"Unable to resolve cyclic non-nullable FK dependencies for {len(next_pending)} records."
                        )
                    pending_rows = next_pending

                # Apply deferred nullable/cyclic FKs via QuerySet.update (no signals/save)
                for model_cls, dest_pk, attname, target_ref in deferred_fk_updates:
                    model_cls._default_manager.filter(pk=dest_pk).update(
                        **{attname: source_ref_to_dest_pk[target_ref]}
                    )

                # Restore M2M links directly via through table bulk_create
                m2m_restored = 0
                for label in model_labels:
                    model_cls = allowed_models[label]
                    m2m_map = {f.name: f for f in model_cls._meta.many_to_many}
                    for row in records_by_model.get(label, []):
                        src_dest_pk = source_ref_to_dest_pk[row["ref"]]
                        for mname, targets in row["m2m"].items():
                            if not targets or mname not in m2m_map:
                                continue
                            m2m_field = m2m_map[mname]
                            through = m2m_field.remote_field.through
                            from_col = m2m_field.m2m_column_name()
                            to_col = m2m_field.m2m_reverse_name()
                            from_att = next(
                                tf.attname for tf in through._meta.concrete_fields if tf.column == from_col
                            )
                            to_att = next(
                                tf.attname for tf in through._meta.concrete_fields if tf.column == to_col
                            )
                            join_objs = [
                                through(
                                    **{
                                        from_att: src_dest_pk,
                                        to_att: source_ref_to_dest_pk[t["ref"]],
                                    }
                                )
                                for t in targets
                                if t["ref"] in source_ref_to_dest_pk
                            ]
                            if join_objs:
                                through._default_manager.bulk_create(join_objs, ignore_conflicts=True)
                                m2m_restored += len(join_objs)

        return {
            "status": "completed",
            "archive_sha256": expected_sha256,
            "created_total": sum(created_counts.values()),
            "created_by_model": created_counts,
            "skipped_exact_duplicates": preview["totals"]["exact_duplicates"],
            "m2m_links_restored": m2m_restored,
            "media_placed": len(placed_media_paths),
            "remapped_pks": remapped_pks,
        }
    except Exception:
        # transaction.atomic() rolled back DB writes; explicitly clean up placed & staged media.
        for media_name in placed_media_paths:
            try:
                if default_storage.exists(media_name):
                    default_storage.delete(media_name)
            except Exception:
                pass
        shutil.rmtree(staging_media_dir, ignore_errors=True)
        raise

