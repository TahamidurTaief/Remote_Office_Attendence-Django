import logging
import uuid
from django.http import JsonResponse
from django.views.csrf import csrf_failure as default_csrf_failure

logger = logging.getLogger(__name__)


def is_attendance_or_api_request(request):
    """
    Determines if request originated from attendance flows, offline sync, HTMX, or API.
    """
    path = request.path or ''
    if path.startswith('/attendance/') or path.startswith('/api/') or path.startswith('/staff/'):
        return True

    # HTMX or AJAX requests
    if request.headers.get('HX-Request') == 'true' or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return True

    # JSON content or accept headers
    accept = request.headers.get('Accept', '')
    if 'application/json' in accept or 'application/json' in (request.content_type or ''):
        return True

    return False


def csrf_failure_view(request, reason=""):
    """
    Custom CSRF failure handler.
    - Generates a unique request ID.
    - Logs the actual rejection reason WITHOUT logging any cookies, tokens, or request bodies.
    - Returns a JSON response with status 403, stable code 'CSRF_FAILURE', reason, and request_id
      for all attendance, API, HTMX, and AJAX requests.
    - Falls back to standard Django HTML response for traditional browser navigation.
    """
    request_id = request.headers.get('X-Request-ID') or str(uuid.uuid4())

    # Log sanitized rejection reason — NEVER log tokens, cookies, or secrets
    logger.warning(
        "CSRF verification rejected [request_id=%s] path=%s method=%s reason=%s",
        request_id,
        request.path,
        request.method,
        reason
    )

    if is_attendance_or_api_request(request):
        response = JsonResponse({
            'success': False,
            'error': 'CSRF verification failed.',
            'code': 'CSRF_FAILURE',
            'reason': reason,
            'request_id': request_id,
        }, status=403)
        response['Cache-Control'] = 'no-cache, no-store, must-revalidate, private'
        response['Pragma'] = 'no-cache'
        return response

    return default_csrf_failure(request, reason=reason)
