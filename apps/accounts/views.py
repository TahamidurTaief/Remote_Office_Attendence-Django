import hashlib
import json
import random
import secrets
import logging
from datetime import timedelta
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib import messages
from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.utils import timezone
from django.http import JsonResponse
from django.db.models import Q
from django.contrib.sessions.models import Session
from apps.accounts.models import UserSession, TrustedDevice, CustomUser, PasswordResetOTP

logger = logging.getLogger(__name__)


def get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def get_device_id(request):
    device_id = request.POST.get('device_id') or request.headers.get('X-Device-Id')
    if not device_id:
        ua = request.META.get('HTTP_USER_AGENT', '')
        ip = get_client_ip(request)
        device_id = hashlib.sha256(f"{ua}-{ip}".encode('utf-8')).hexdigest()[:32]
    return device_id


class CustomLoginView(View):
    def get(self, request):
        if request.user.is_authenticated:
            return self.redirect_based_on_role(request.user)

        device_notice = request.GET.get('device_notice')
        notice_message = None
        if device_notice == 'logged_in_elsewhere':
            notice_message = 'Your session was ended because your account logged in from another device.'
        elif device_notice == 'idle_timeout':
            notice_message = 'Your session expired due to 30 minutes of inactivity.'

        context = {
            'device_notice': device_notice,
            'notice_message': notice_message
        }
        return render(request, 'accounts/login.html', context)

    def post(self, request):
        email = request.POST.get('email')
        password = request.POST.get('password')
        remember_device = request.POST.get('remember_device') == 'true' or request.POST.get('remember_device') == 'on'

        ip = get_client_ip(request)
        cache_key = f"login_attempts_{ip}"
        attempts = cache.get(cache_key, 0)

        if attempts >= 5:
            messages.error(request, 'Too many login attempts. Please try again in 5 minutes.')
            return render(request, 'accounts/login.html')

        user = authenticate(request, email=email, password=password)

        if user is not None:
            if user.is_active:
                now = timezone.now()
                device_id = get_device_id(request)

                # 1. Single Device Login Enforcement: Invalidate prior active sessions
                old_sessions = UserSession.objects.filter(user=user, is_active=True)
                for old_sess in old_sessions:
                    old_sess.is_active = False
                    old_sess.logout_time = now
                    old_sess.save(update_fields=['is_active', 'logout_time'])

                    if old_sess.session_key:
                        Session.objects.filter(session_key=old_sess.session_key).delete()

                # 2. Perform standard Django login
                login(request, user)
                cache.delete(cache_key)

                # 3. Create new active UserSession record
                UserSession.objects.create(
                    user=user,
                    device_id=device_id,
                    session_key=request.session.session_key,
                    browser=request.META.get('HTTP_USER_AGENT', ''),
                    ip=ip,
                    login_time=now,
                    last_activity=now,
                    is_active=True
                )

                # 4. Process Remember Device / TrustedDevice
                if remember_device:
                    device_hash = hashlib.sha256(f"{user.id}-{device_id}".encode('utf-8')).hexdigest()
                    expire_at = now + timedelta(days=30)
                    TrustedDevice.objects.update_or_create(
                        user=user,
                        device_hash=device_hash,
                        defaults={
                            'device_name': request.META.get('HTTP_USER_AGENT', '')[:250],
                            'expire_at': expire_at
                        }
                    )

                return self.redirect_based_on_role(user)
            else:
                messages.error(request, 'Your account is disabled.')
        else:
            cache.set(cache_key, attempts + 1, timeout=300)
            messages.error(request, 'Invalid email or password.')

        return render(request, 'accounts/login.html')

    def redirect_based_on_role(self, user):
        if user.role == 'admin':
            return redirect('/admin-panel/dashboard/')
        elif user.role in ['staff', 'manager']:
            return redirect('/staff/home/')
        return redirect('/')


class CustomLogoutView(View):
    def get(self, request):
        return self._logout_user(request)

    def post(self, request):
        return self._logout_user(request)

    def _logout_user(self, request):
        if request.user.is_authenticated:
            session_key = request.session.session_key
            if session_key:
                UserSession.objects.filter(user=request.user, session_key=session_key).update(
                    is_active=False,
                    logout_time=timezone.now()
                )
        logout(request)
        return redirect('/login/')


class ChangePasswordView(LoginRequiredMixin, View):
    def get(self, request):
        return render(request, 'accounts/change_password.html')

    def post(self, request):
        old_password = request.POST.get('old_password')
        new_password1 = request.POST.get('new_password1')
        new_password2 = request.POST.get('new_password2')

        errors = []

        if not request.user.check_password(old_password):
            errors.append("Current password is incorrect.")

        if not new_password1:
            errors.append("New password cannot be empty.")

        if new_password1 != new_password2:
            errors.append("New password and confirm password do not match.")

        if errors:
            for err in errors:
                messages.error(request, err)
            if request.headers.get('HX-Request') == 'true':
                return render(request, 'accounts/change_password.html')
            return render(request, 'accounts/change_password.html')

        # Any password allowed (no complexity rule)
        user = request.user
        user.set_password(new_password1)
        user.save()

        # Update auth hash to keep current session active
        update_session_auth_hash(request, user)

        # Logout ALL OTHER devices (keep current session active)
        now = timezone.now()
        current_session_key = request.session.session_key
        other_sessions = UserSession.objects.filter(user=user, is_active=True).exclude(session_key=current_session_key)

        for sess in other_sessions:
            sess.is_active = False
            sess.logout_time = now
            sess.save(update_fields=['is_active', 'logout_time'])

            if sess.session_key:
                Session.objects.filter(session_key=sess.session_key).delete()

        messages.success(request, 'Your password was successfully updated! Other device sessions have been logged out.')

        if request.headers.get('HX-Request') == 'true':
            return render(request, 'accounts/change_password.html')
        return render(request, 'accounts/change_password.html')


class ForgotPasswordView(View):
    """
    Forgot Password multi-step HTMX view container.
    """
    def get(self, request):
        return render(request, 'accounts/forgot_password.html')


class ForgotPasswordRequestView(View):
    """
    Step 1: User enters email or phone. Server generates 6-digit OTP code.
    """
    def post(self, request):
        identifier = (request.POST.get('identifier') or '').strip()

        if not identifier:
            return render(request, 'accounts/partials/forgot_step1.html', {
                'error': 'Please enter your email address or phone number.'
            })

        user = CustomUser.objects.filter(
            Q(email__iexact=identifier) | Q(phone__iexact=identifier)
        ).first()

        if not user:
            # Generic message to avoid email enumeration
            return render(request, 'accounts/partials/forgot_step2.html', {
                'identifier': identifier,
                'reset_token': 'dummy_token',
                'debug_otp': '123456'
            })

        # Generate 6-digit OTP
        otp_code = f"{random.randint(100000, 999999)}"
        reset_token = secrets.token_urlsafe(32)
        expires_at = timezone.now() + timedelta(minutes=10)

        PasswordResetOTP.objects.create(
            user=user,
            otp_code=otp_code,
            reset_token=reset_token,
            expires_at=expires_at
        )

        logger.info(f"[ForgotPassword] Generated OTP {otp_code} for user {user.email or user.phone}")

        return render(request, 'accounts/partials/forgot_step2.html', {
            'identifier': identifier,
            'reset_token': reset_token,
            'debug_otp': otp_code
        })


class ForgotPasswordVerifyView(View):
    """
    Step 2: Verify OTP code.
    """
    def post(self, request):
        reset_token = request.POST.get('reset_token')
        otp_code = (request.POST.get('otp_code') or '').strip()

        otp_obj = PasswordResetOTP.objects.filter(
            reset_token=reset_token,
            otp_code=otp_code
        ).first()

        if not otp_obj or not otp_obj.is_valid():
            return render(request, 'accounts/partials/forgot_step2.html', {
                'reset_token': reset_token,
                'error': 'Invalid or expired OTP code. Please try again.'
            })

        otp_obj.is_used = True
        otp_obj.save(update_fields=['is_used'])

        return render(request, 'accounts/partials/forgot_step3.html', {
            'reset_token': reset_token
        })


class ForgotPasswordResetView(View):
    """
    Step 3: Reset Password & Logout All Devices.
    """
    def post(self, request):
        reset_token = request.POST.get('reset_token')
        new_password = request.POST.get('new_password')
        confirm_password = request.POST.get('confirm_password')

        if not new_password or not confirm_password:
            return render(request, 'accounts/partials/forgot_step3.html', {
                'reset_token': reset_token,
                'error': 'Password fields cannot be empty.'
            })

        if new_password != confirm_password:
            return render(request, 'accounts/partials/forgot_step3.html', {
                'reset_token': reset_token,
                'error': 'Passwords do not match.'
            })

        otp_obj = PasswordResetOTP.objects.filter(reset_token=reset_token).first()
        if not otp_obj:
            return render(request, 'accounts/partials/forgot_step3.html', {
                'reset_token': reset_token,
                'error': 'Invalid reset session. Please request a new OTP.'
            })

        # Set user new password (any password allowed)
        user = otp_obj.user
        user.set_password(new_password)
        user.save()

        # Logout ALL active device sessions for this user
        now = timezone.now()
        active_sessions = UserSession.objects.filter(user=user, is_active=True)
        for sess in active_sessions:
            sess.is_active = False
            sess.logout_time = now
            sess.save(update_fields=['is_active', 'logout_time'])

            if sess.session_key:
                Session.objects.filter(session_key=sess.session_key).delete()

        return render(request, 'accounts/partials/forgot_step4.html')


class SyncApiView(View):
    """
    POST /api/sync/
    Bulk sync endpoint stub for offline sync_queue items.
    """
    def post(self, request):
        if not request.user.is_authenticated:
            return JsonResponse({'valid': False, 'reason': 'unauthenticated'}, status=401)

        try:
            body = json.loads(request.body.decode('utf-8'))
            items = body.get('items', [])
            results = []

            for item in items:
                results.append({
                    'uuid': item.get('uuid'),
                    'module': item.get('module'),
                    'action': item.get('action'),
                    'status': 'success',
                    'synced_at': timezone.now().isoformat()
                })

            return JsonResponse({'status': 'success', 'results': results})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)


class SessionValidateView(View):
    """
    POST /api/session/validate/
    Endpoint used by SyncEngine on reconnect to re-validate offline cached session token.
    Force logout ONLY after server explicitly confirms session invalidation (HTTP 401).
    """
    def post(self, request):
        if not request.user.is_authenticated:
            return JsonResponse({
                'valid': False,
                'reason': 'logged_in_elsewhere',
                'message': 'Logged in from another device or session expired.'
            }, status=401)

        session_key = request.session.session_key
        active_sess = UserSession.objects.filter(
            user=request.user,
            session_key=session_key,
            is_active=True
        ).first()

        if not active_sess:
            return JsonResponse({
                'valid': False,
                'reason': 'logged_in_elsewhere',
                'message': 'Session invalidated due to login on another device.'
            }, status=401)

        return JsonResponse({
            'valid': True,
            'user': {
                'id': request.user.id,
                'email': request.user.email,
                'role': request.user.role
            },
            'session_key': session_key
        })


class UserSessionsView(LoginRequiredMixin, View):
    """
    Renders active sessions for current user (used by <c-session-list>).
    """
    def get(self, request):
        sessions = UserSession.objects.filter(user=request.user).order_by('-login_time')[:10]
        current_session_key = request.session.session_key
        return render(request, 'cotton/session-list.html', {
            'sessions': sessions,
            'current_session_key': current_session_key
        })


def index_view(request):
    return render(request, 'index.html')
