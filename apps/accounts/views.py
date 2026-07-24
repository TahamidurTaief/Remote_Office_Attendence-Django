import hashlib
import time
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
from apps.accounts.models import UserSession, TrustedDevice, CustomUser, PasswordResetOTP, UserLoginActivity, LoginProtection
from apps.notifications.models import log_audit, AuditLog
from apps.accounts.login_protection import (
    check_3layer_lock,
    record_failed_attempt,
    record_successful_login,
    get_or_create_protection
)

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
        device_notice = False
        notice_message = ""
        remember_token = request.COOKIES.get('remember_device_token')
        if remember_token:
            device_notice = True
            notice_message = "Your device is recognized."

        ip = get_client_ip(request)
        device_id = get_device_id(request)
        is_locked, remaining_secs, prot = check_3layer_lock(ip=ip, device_id=device_id)

        n1, n2 = random.randint(1, 9), random.randint(1, 9)
        request.session['captcha_ans'] = str(n1 + n2)

        context = {
            'device_notice': device_notice,
            'notice_message': notice_message,
            'show_captcha': (prot and prot.captcha_required) if prot else False,
            'is_locked': is_locked,
            'remaining_secs': remaining_secs,
            'lock_level': prot.current_lock_level if prot else 0,
            'captcha_num1': n1,
            'captcha_num2': n2,
        }
        return render(request, 'accounts/login.html', context)

    def post(self, request):
        start_time = time.time()
        email = (request.POST.get('email') or '').strip()
        password = request.POST.get('password') or ''
        remember_device = request.POST.get('remember_device') in ['true', 'on']
        captcha_ans_entered = (request.POST.get('captcha_ans') or '').strip()

        ip = get_client_ip(request)
        ua = request.META.get('HTTP_USER_AGENT', '')
        device_id = get_device_id(request)

        user_obj = CustomUser.objects.filter(Q(email__iexact=email) | Q(phone__iexact=email)).first()

        def pad_response():
            elapsed = time.time() - start_time
            if elapsed < 0.35:
                time.sleep(0.35 - elapsed)

        # 1. 3-Layer Lock Check (User, Email, IP, Device)
        is_locked, remaining_secs, prot = check_3layer_lock(user=user_obj, email=email, ip=ip, device_id=device_id)
        if is_locked:
            UserLoginActivity.objects.create(
                user=user_obj,
                identifier_entered=email,
                ip_address=ip,
                user_agent=ua,
                status='locked'
            )
            mins = remaining_secs // 60
            secs = remaining_secs % 60
            messages.error(request, f"Account temporarily locked (Level {prot.current_lock_level}). Please wait {mins}m {secs}s.")
            pad_response()
            return render(request, 'accounts/login.html', {
                'email_entered': email,
                'is_locked': True,
                'remaining_secs': remaining_secs,
                'lock_level': prot.current_lock_level if prot else 1,
                'show_captcha': True
            })

        # 2. Captcha Validation Check (Attempt >= 3)
        prot_rec = get_or_create_protection(user=user_obj, email=email, ip=ip, device_id=device_id)
        expected_ans = request.session.get('captcha_ans')

        if prot_rec.captcha_required and expected_ans:
            if captcha_ans_entered != str(expected_ans):
                new_prot = record_failed_attempt(user=user_obj, email=email, ip=ip, device_id=device_id)
                messages.error(request, 'Incorrect Security Verification answer.')
                n1, n2 = random.randint(1, 9), random.randint(1, 9)
                request.session['captcha_ans'] = str(n1 + n2)
                pad_response()
                return render(request, 'accounts/login.html', {
                    'email_entered': email,
                    'show_captcha': True,
                    'captcha_num1': n1,
                    'captcha_num2': n2
                })

        # 3. Authenticate User (Applies to existing AND unknown usernames equally)
        user = authenticate(request, username=email, password=password)

        if user is None:
            new_prot = record_failed_attempt(user=user_obj, email=email, ip=ip, device_id=device_id)
            UserLoginActivity.objects.create(
                user=user_obj,
                identifier_entered=email,
                ip_address=ip,
                user_agent=ua,
                status='failed'
            )

            n1, n2 = random.randint(1, 9), random.randint(1, 9)
            request.session['captcha_ans'] = str(n1 + n2)

            if new_prot.failed_attempts >= 5:
                rem = new_prot.remaining_lock_seconds()
                messages.error(request, f"Too many failed login attempts. Account locked for {rem // 60 or 1} minute(s).")
            elif new_prot.failed_attempts >= 3:
                messages.error(request, "Invalid credentials. Two attempts remaining before temporary lock.")
            else:
                messages.error(request, "Invalid email or password.")

            pad_response()
            return render(request, 'accounts/login.html', {
                'email_entered': email,
                'show_captcha': new_prot.captcha_required,
                'captcha_num1': n1,
                'captcha_num2': n2
            })

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
            log_audit(request.user, 'user_logout', summary="User logged out", ip=get_client_ip(request))
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
            return render(request, 'accounts/change_password.html')

        user = request.user
        user.set_password(new_password1)
        user.save()

        update_session_auth_hash(request, user)
        log_audit(user, 'password_change', summary="User updated account password", ip=get_client_ip(request))

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
        return render(request, 'accounts/change_password.html')


class ForgotPasswordView(View):
    def get(self, request):
        return render(request, 'accounts/forgot_password.html')


class ForgotPasswordRequestView(View):
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
            return render(request, 'accounts/partials/forgot_step2.html', {
                'identifier': identifier,
                'reset_token': 'dummy_token',
                'debug_otp': '123456'
            })

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

        user = otp_obj.user
        user.set_password(new_password)
        user.save()
        log_audit(user, 'password_reset', summary="Password reset via OTP", ip=get_client_ip(request))

        now = timezone.now()
        active_sessions = UserSession.objects.filter(user=user, is_active=True)
        for sess in active_sessions:
            sess.is_active = False
            sess.logout_time = now
            sess.save(update_fields=['is_active', 'logout_time'])

            if sess.session_key:
                Session.objects.filter(session_key=sess.session_key).delete()

        return render(request, 'accounts/partials/forgot_step4.html')


class AdminForceLogoutUserView(LoginRequiredMixin, View):
    """
    POST /admin-panel/users/<int:pk>/force-logout/
    Admin force-logout action. Kills all active sessions for targeted user.
    """
    def post(self, request, pk):
        if request.user.role != 'admin':
            return JsonResponse({'error': 'Permission denied'}, status=403)

        target_user = CustomUser.objects.filter(pk=pk).first()
        if not target_user:
            return JsonResponse({'error': 'User not found'}, status=404)

        now = timezone.now()
        active_sessions = UserSession.objects.filter(user=target_user, is_active=True)
        for sess in active_sessions:
            sess.is_active = False
            sess.logout_time = now
            sess.save(update_fields=['is_active', 'logout_time'])

            if sess.session_key:
                Session.objects.filter(session_key=sess.session_key).delete()

        log_audit(request.user, 'admin_force_logout', target=target_user, summary=f"Admin force logged out user {target_user.email or target_user.phone}", ip=get_client_ip(request))

        if request.headers.get('HX-Request') == 'true':
            return render(request, 'cotton/badge.html', {'slot': 'Logged Out', 'variant': 'secondary'})

        messages.success(request, f"User logged out from all devices.")
        return redirect('/admin-panel/roles/')


class AdminUnlockUserView(LoginRequiredMixin, View):
    """
    POST /admin-panel/users/<int:pk>/unlock/
    Admin manual unlock action. Resets failed attempts and clear locked_until.
    """
    def post(self, request, pk):
        if request.user.role != 'admin':
            return JsonResponse({'error': 'Permission denied'}, status=403)

        target_user = CustomUser.objects.filter(pk=pk).first()
        if not target_user:
            return JsonResponse({'error': 'User not found'}, status=404)

        target_user.failed_login_count = 0
        target_user.locked_until = None
        target_user.save(update_fields=['failed_login_count', 'locked_until'])

        if target_user.email:
            prots = LoginProtection.objects.filter(Q(user=target_user) | Q(email__iexact=target_user.email))
            for p in prots:
                p.reset_lock()

        log_audit(request.user, 'admin_unlock_user', target=target_user, summary=f"Admin unlocked user {target_user.email or target_user.phone}", ip=get_client_ip(request))

        if request.headers.get('HX-Request') == 'true':
            return render(request, 'cotton/badge.html', {'slot': 'Unlocked', 'variant': 'success'})

        messages.success(request, f"User unlocked successfully.")
        return redirect('/admin-panel/roles/')


class LoginLockStatusView(View):
    """
    GET /login/lock-status/
    HTMX live server-time polling endpoint for locked login screen countdown.
    """
    def get(self, request):
        email = (request.GET.get('email') or '').strip()
        ip = get_client_ip(request)
        device_id = get_device_id(request)

        is_locked, remaining_secs, prot = check_3layer_lock(email=email, ip=ip, device_id=device_id)

        if not is_locked:
            return render(request, 'accounts/partials/lock_status_unlocked.html')

        mins = remaining_secs // 60
        secs = remaining_secs % 60

        context = {
            'is_locked': True,
            'remaining_secs': remaining_secs,
            'minutes': mins,
            'seconds': secs,
            'email': email,
            'lock_level': prot.current_lock_level if prot else 1
        }
        return render(request, 'accounts/partials/lock_status_countdown.html', context)


class AdminLoginActivityView(LoginRequiredMixin, View):
    """
    GET /admin-panel/login-activity/
    Renders login activity logs for admin.
    """
    def get(self, request):
        if request.user.role != 'admin':
            return redirect('/')

        status_filter = request.GET.get('status')
        activities = UserLoginActivity.objects.select_related('user').order_by('-timestamp')

        if status_filter:
            activities = activities.filter(status=status_filter)

        return render(request, 'admin_panel/login_activity.html', {
            'activities': activities[:100],
            'status_filter': status_filter
        })


class SyncApiView(View):
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
    def get(self, request):
        sessions = UserSession.objects.filter(user=request.user).order_by('-login_time')[:10]
        current_session_key = request.session.session_key
        return render(request, 'cotton/session-list.html', {
            'sessions': sessions,
            'current_session_key': current_session_key
        })


def index_view(request):
    return render(request, 'index.html')
