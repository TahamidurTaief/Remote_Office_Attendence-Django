from django.utils import timezone

from .constants import AUDIT_UNLOCK_SESSION_KEY, AUDIT_UNLOCK_TTL_SECONDS


def grant_audit_unlock(request, seconds=AUDIT_UNLOCK_TTL_SECONDS):
    request.session[AUDIT_UNLOCK_SESSION_KEY] = (timezone.now() + timezone.timedelta(seconds=seconds)).isoformat()
    request.session.modified = True


def has_audit_unlock(request):
    expires_at = request.session.get(AUDIT_UNLOCK_SESSION_KEY)
    if not expires_at:
        return False
    try:
        parsed = timezone.datetime.fromisoformat(expires_at)
    except ValueError:
        request.session.pop(AUDIT_UNLOCK_SESSION_KEY, None)
        return False
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    if parsed <= timezone.now():
        request.session.pop(AUDIT_UNLOCK_SESSION_KEY, None)
        return False
    return True

