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
from apps.accounts.models import UserSession, TrustedDevice, CustomUser, PasswordResetOTP, UserLoginActivity
from apps.notifications.models import log_audit, AuditLog

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

        ip = get_client_ip(request)
        attempts = cache.get(f"login_attempts_{ip}", 0)
        show_captcha = attempts >= 3

        n1, n2 = random.randint(1, 9), random.randint(1, 9)
        request.session['captcha_ans'] = str(n1 + n2)

        context = {
            'device_notice': device_notice,
            'notice_message': notice_message,
            'show_captcha': show_captcha,
            'captcha_num1': n1,
            'captcha_num2': n2,
        }
        return render(request, 'accounts/login.html', context)

    def post(self, request):
        email = (request.POST.get('email') or '').strip()
        password = request.POST.get('password')
        remember_device = request.POST.get('remember_device') in ['true', 'on']
        captcha_ans_entered = (request.POST.get('captcha_ans') or '').strip()

        ip = get_client_ip(request)
        ua = request.META.get('HTTP_USER_AGENT', '')
        cache_key = f"login_attempts_{ip}"
        attempts = cache.get(cache_key, 0)

        user_obj = CustomUser.objects.filter(Q(email__iexact=email) | Q(phone__iexact=email)).first()

        # 1. IP Rate Limiting (5 fails = 5min IP block)
        if attempts >= 5:
            messages.error(request, 'Too many login attempts. Please try again in 5 minutes.')
            return render(request, 'accounts/login.html', {'email_entered': email, 'show_captcha': True})

        # 2. Check account lock & 30-min auto-unlock
        if user_obj and user_obj.locked_until:
            if timezone.now() > user_obj.locked_until:
                user_obj.locked_until = None
                user_obj.failed_login_count = 0
                user_obj.save(update_fields=['locked_until', 'failed_login_count'])
            else:
                UserLoginActivity.objects.create(
                    user=user_obj,
                    identifier_entered=email,
                    ip_address=ip,
                    user_agent=ua,
                    status='locked'
                )
                time_left = max(1, int((user_obj.locked_until - timezone.now()).total_seconds() // 60))
                messages.error(request, f"Account is locked due to 5 failed attempts. Please try again in {time_left} minutes.")
                return render(request, 'accounts/login.html', {'email_entered': email, 'show_captcha': True})

        # 3. Check 3-fail Captcha verification
        requires_captcha = (user_obj and user_obj.failed_login_count >= 3) or (attempts >= 3)
        expected_ans = request.session.get('captcha_ans')
        if requires_captcha and expected_ans and captcha_ans_entered != str(expected_ans):
            cache.set(cache_key, attempts + 1, timeout=300)
            messages.error(request, 'Incorrect Security Verification answer.')
            n1, n2 = random.randint(1, 9), random.randint(1, 9)
            request.session['captcha_ans'] = str(n1 + n2)
            return render(request, 'accounts/login.html', {
                'email_entered': email,
                'show_captcha': True,
                'captcha_num1': n1,
                'captcha_num2': n2
            })

        # 4. Authenticate User
        user = authenticate(request, username=email, password=password)

        if user is not None:
            # Check employee profile active status
            emp_prof = getattr(user, 'employee_profile', None)
            if not user.is_active or (emp_prof and not emp_prof.is_active):
                log_audit(user, 'account_disabled_block', summary="Login blocked: Account deactivated/suspended", ip=ip)
                messages.error(request, 'Your account has been deactivated or suspended. Please contact administrator.')
                return render(request, 'accounts/login.html', {'email_entered': email})

            now = timezone.now()
            device_id = get_device_id(request)

            user.failed_login_count = 0
            user.locked_until = None
            user.save(update_fields=['failed_login_count', 'locked_until'])

            UserLoginActivity.objects.create(
                user=user,
                identifier_entered=email,
                ip_address=ip,
                user_agent=ua,
                status='success'
            )

            # Single Device Login Enforcement: Invalidate prior active sessions
            old_sessions = UserSession.objects.filter(user=user, is_active=True)
            for old_sess in old_sessions:
                old_sess.is_active = False
                old_sess.logout_time = now
                old_sess.save(update_fields=['is_active', 'logout_time'])

                if old_sess.session_key:
                    Session.objects.filter(session_key=old_sess.session_key).delete()

            # Perform standard Django login
            login(request, user)

            # 1. Regenerate session key to prevent session fixation attacks
            request.session.cycle_key()

            cache.delete(cache_key)
            log_audit(user, 'user_login', summary=f"User logged in from {ip}", ip=ip)

            # 2. Check New Device Login Alert
            device_hash = hashlib.sha256(f"{user.id}-{device_id}".encode('utf-8')).hexdigest()
            user_has_trusted = TrustedDevice.objects.filter(user=user).exists()
            is_device_trusted = TrustedDevice.objects.filter(user=user, device_hash=device_hash).exists()

            if not user_has_trusted:
                # First-ever login: automatically trust device
                TrustedDevice.objects.create(
                    user=user,
                    device_hash=device_hash,
                    device_name=ua[:250],
                    expire_at=now + timedelta(days=30)
                )
            elif not is_device_trusted:
                # Unrecognized new device login
                log_audit(user, 'new_device_login', summary=f"Unrecognized new device login from IP {ip}", ip=ip)
                if user.email:
                    from django.core.mail import send_mail
                    send_mail(
                        subject="Security Alert: New Device Login Detected",
                        message=f"Hi {user.email},\n\nA new device login was detected for your FieldTrack account.\nIP Address: {ip}\nBrowser: {ua[:100]}\nTime: {now.strftime('%d/%m/%Y %g:%i %A')}\n\nIf this was not you, please change your password immediately.",
                        from_email="noreply@fieldtrack.com",
                        recipient_list=[user.email],
                        fail_silently=True
                    )

            UserSession.objects.create(
                user=user,
                device_id=device_id,
                session_key=request.session.session_key,
                browser=ua,
                ip=ip,
                login_time=now,
                last_activity=now,
                is_active=True
            )

            if remember_device:
                expire_at = now + timedelta(days=30)
                TrustedDevice.objects.update_or_create(
                    user=user,
                    device_hash=device_hash,
                    defaults={
                        'device_name': ua[:250],
                        'expire_at': expire_at
                    }
                )

            return self.redirect_based_on_role(user)
        else:
            cache.set(cache_key, attempts + 1, timeout=300)
            if user_obj:
                user_obj.failed_login_count += 1
                if user_obj.failed_login_count >= 5:
                    user_obj.locked_until = timezone.now() + timedelta(minutes=30)
                    user_obj.save(update_fields=['failed_login_count', 'locked_until'])
                    UserLoginActivity.objects.create(
                        user=user_obj,
                        identifier_entered=email,
                        ip_address=ip,
                        user_agent=ua,
                        status='locked'
                    )
                    messages.error(request, 'Account locked for 30 minutes due to 5 failed login attempts.')
                else:
                    user_obj.save(update_fields=['failed_login_count'])
                    UserLoginActivity.objects.create(
                        user=user_obj,
                        identifier_entered=email,
                        ip_address=ip,
                        user_agent=ua,
                        status='failed'
                    )
                    messages.error(request, f'Invalid email or password. Attempt {user_obj.failed_login_count} of 5.')
            else:
                UserLoginActivity.objects.create(
                    user=None,
                    identifier_entered=email,
                    ip_address=ip,
                    user_agent=ua,
                    status='failed'
                )
                messages.error(request, 'Invalid email or password.')

        n1, n2 = random.randint(1, 9), random.randint(1, 9)
        request.session['captcha_ans'] = str(n1 + n2)

        show_captcha_now = (user_obj and user_obj.failed_login_count >= 3) or (attempts >= 2)
        return render(request, 'accounts/login.html', {
            'email_entered': email,
            'show_captcha': show_captcha_now,
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

        log_audit(request.user, 'admin_unlock_user', target=target_user, summary=f"Admin unlocked user {target_user.email or target_user.phone}", ip=get_client_ip(request))

        if request.headers.get('HX-Request') == 'true':
            return render(request, 'cotton/badge.html', {'slot': 'Unlocked', 'variant': 'success'})

        messages.success(request, f"User unlocked successfully.")
        return redirect('/admin-panel/roles/')


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
