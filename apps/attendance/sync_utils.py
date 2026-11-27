import datetime
from django.utils import timezone

class ClientTimeValidationError(ValueError):
    """Raised when a supplied client_event_time is malformed, in the future, or outside the offline window."""


def validate_client_time(client_time_str, max_age_seconds=259200):
    """
    Validates an optional ISO8601 client_event_time string:
    - Absent / blank (None or empty string) -> returns None (live server-time fallback).
    - Supplied but malformed, in the future (dt > now), or older than max_age_seconds (default 72h)
      -> raises ClientTimeValidationError.
    """
    if client_time_str is None:
        return None
    if isinstance(client_time_str, str) and not client_time_str.strip():
        return None
    if not isinstance(client_time_str, str):
        raise ClientTimeValidationError('Invalid client_event_time format.')

    raw = client_time_str.strip()
    if raw.endswith('Z'):
        raw = raw[:-1] + '+00:00'

    try:
        dt = datetime.datetime.fromisoformat(raw)
    except ValueError:
        raise ClientTimeValidationError('Invalid client_event_time format.')

    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())

    now = timezone.now()
    if dt > now:
        raise ClientTimeValidationError('client_event_time cannot be in the future.')

    if (now - dt).total_seconds() > max_age_seconds:
        raise ClientTimeValidationError(
            'client_event_time is older than the allowed 72-hour offline sync window.'
        )

    return dt


def parse_and_validate_client_time(client_time_str, max_age_seconds=259200):
    """
    Backwards-compatible wrapper returning None when client_time_str is absent or invalid.
    """
    try:
        return validate_client_time(client_time_str, max_age_seconds=max_age_seconds)
    except ClientTimeValidationError:
        return None
