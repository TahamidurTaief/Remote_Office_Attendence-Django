from copy import deepcopy
from uuid import UUID

from django.forms.models import model_to_dict
from django.utils import timezone


SENSITIVE_KEYS = {
    "password",
    "password_hash",
    "mfa_secret",
    "token",
    "api_key",
    "private_key",
    "session_secret",
    "backup_codes",
    "workspace_password_hash",
    "pin_hash",
}

MASKED_KEYS = {
    "bank_account",
    "national_id",
    "nid",
}

EXCLUDED_MODEL_FIELDS = {"created_at", "updated_at"}


def mask_value(value):
    if value in (None, ""):
        return value
    text = str(value)
    if len(text) <= 4:
        return "*" * len(text)
    return f"{text[:2]}{'*' * max(len(text) - 4, 1)}{text[-2:]}"


def sanitize_value(key, value):
    lowered = str(key).lower()
    if lowered in SENSITIVE_KEYS or any(part in lowered for part in ("password", "secret", "token", "key")):
        return "[REDACTED]"
    if lowered in MASKED_KEYS or any(part in lowered for part in ("bank", "nid", "national_id")):
        return mask_value(value)
    if isinstance(value, dict):
        return {sub_key: sanitize_value(sub_key, sub_val) for sub_key, sub_val in value.items()}
    if isinstance(value, list):
        return [sanitize_value(key, item) for item in value]
    return value


def normalize_value(value):
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (set, tuple)):
        return [normalize_value(item) for item in value]
    if isinstance(value, list):
        return [normalize_value(item) for item in value]
    if isinstance(value, dict):
        return {key: normalize_value(val) for key, val in value.items()}
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "pk"):
        return value.pk
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def serialize_instance(instance):
    data = model_to_dict(instance)
    data["pk"] = instance.pk
    for field in instance._meta.fields:
        if field.name in data or field.name in EXCLUDED_MODEL_FIELDS:
            continue
        data[field.name] = getattr(instance, field.name, None)
    cleaned = {}
    for key, value in data.items():
        if key in EXCLUDED_MODEL_FIELDS:
            continue
        cleaned[key] = sanitize_value(key, normalize_value(value))
    return cleaned


def diff_snapshots(before, after):
    before = before or {}
    after = after or {}
    changed = {}
    for key in sorted(set(before.keys()) | set(after.keys())):
        if before.get(key) != after.get(key):
            changed[key] = {
                "before": deepcopy(before.get(key)),
                "after": deepcopy(after.get(key)),
            }
    return changed


def get_request_ip(request):
    if not request:
        return ""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def get_request_device(request):
    if not request or not getattr(request, "user", None) or not request.user.is_authenticated:
        return ""
    session_key = getattr(request.session, "session_key", "")
    from apps.accounts.models import UserSession

    sess = UserSession.objects.filter(user=request.user, session_key=session_key, is_active=True).first()
    if not sess:
        return request.META.get("HTTP_USER_AGENT", "")[:255]
    return sess.device_display_name[:255]


def to_expiry_timestamp(seconds):
    return (timezone.now() + timezone.timedelta(seconds=seconds)).isoformat()
