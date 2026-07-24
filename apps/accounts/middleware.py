import hashlib
from datetime import timedelta
from django.utils import timezone
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.http import JsonResponse
from apps.accounts.models import UserSession


class SessionDeviceMiddleware:
    """
    Middleware enforcing Single Device Login and 30-minute idle session auto-expiration.
    """

    IDLE_TIMEOUT_MINUTES = 30

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            session_key = request.session.session_key
            if session_key:
                # Find matching active session in user_session table
                user_session = UserSession.objects.filter(
                    user=request.user,
                    session_key=session_key,
                    is_active=True
                ).first()

                if not user_session:
                    # Session was invalidated (e.g. logged in on another device)
                    logout(request)
                    if self._is_api_or_htmx(request):
                        return JsonResponse(
                            {'valid': False, 'reason': 'logged_in_elsewhere', 'message': 'Logged in from another device.'},
                            status=401
                        )
                    return redirect('/login/?device_notice=logged_in_elsewhere')

                # Check idle timeout (30 minutes)
                now = timezone.now()
                idle_threshold = timedelta(minutes=self.IDLE_TIMEOUT_MINUTES)
                if user_session.last_activity and (now - user_session.last_activity) > idle_threshold:
                    user_session.is_active = False
                    user_session.logout_time = now
                    user_session.save(update_fields=['is_active', 'logout_time'])
                    logout(request)

                    if self._is_api_or_htmx(request):
                        return JsonResponse(
                            {'valid': False, 'reason': 'idle_timeout', 'message': 'Session expired due to inactivity.'},
                            status=401
                        )
                    return redirect('/login/?device_notice=idle_timeout')

                # Update last_activity timestamp (throttled to avoid DB thrashing)
                if not user_session.last_activity or (now - user_session.last_activity) > timedelta(seconds=60):
                    user_session.last_activity = now
                    user_session.save(update_fields=['last_activity'])

        response = self.get_response(request)
        return response

    def _is_api_or_htmx(self, request):
        return (
            request.path.startswith('/api/') or
            request.headers.get('HX-Request') == 'true' or
            request.headers.get('Accept') == 'application/json'
        )
