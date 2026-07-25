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
from django.utils.decorators import method_decorator
from apps.accounts.decorators import require_reauth
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

        # 4. Check Disabled/Suspended Status & Employee Master Lifecycle Status
        emp_prof = getattr(user, 'employee_profile', None)
        emp_master = getattr(user, 'employee_master', None)
        if not user.is_active or (emp_prof and not emp_prof.is_active) or (emp_master and not emp_master.is_login_allowed()):
            log_audit(user, 'account_disabled_block', summary="Login blocked: Account deactivated, suspended, or invalid employee status", ip=ip)
            messages.error(request, 'Your account has been deactivated or suspended. Please contact administrator.')
            pad_response()
            return render(request, 'accounts/login.html', {'email_entered': email})

        # 4.5 Check MFA Requirement (Skip if device is already trusted)
        sec_prof = getattr(user, 'security_profile', None)
        if sec_prof and sec_prof.mfa_enabled:
            device_hash = hashlib.sha256(f"{user.id}-{device_id}".encode('utf-8')).hexdigest()
            is_device_trusted = TrustedDevice.objects.filter(user=user, device_hash=device_hash, expire_at__gt=timezone.now()).exists()

            if not is_device_trusted:
                request.session['pending_mfa_user_id'] = user.id
                pad_response()
                if request.headers.get('HX-Request') == 'true':
                    return render(request, 'accounts/partials/login_mfa_step.html')
                return render(request, 'accounts/login.html', {'show_mfa_step': True})

        # 5. Successful Authentication -> Full Reset & Session Start
        record_successful_login(user=user, email=email, ip=ip, device_id=device_id)

        now = timezone.now()
        UserLoginActivity.objects.create(
            user=user,
            identifier_entered=email,
            ip_address=ip,
            user_agent=ua,
            status='success'
        )

        old_sessions = UserSession.objects.filter(user=user, is_active=True)
        for old_sess in old_sessions:
            old_sess.is_active = False
            old_sess.logout_time = now
            old_sess.save(update_fields=['is_active', 'logout_time'])

            if old_sess.session_key:
                Session.objects.filter(session_key=old_sess.session_key).delete()

        login(request, user)
        request.session.cycle_key()
        log_audit(user, 'user_login', summary=f"User logged in from {ip}", ip=ip)

        # Check New Device Alert
        device_hash = hashlib.sha256(f"{user.id}-{device_id}".encode('utf-8')).hexdigest()
        user_has_trusted = TrustedDevice.objects.filter(user=user).exists()
        is_device_trusted = TrustedDevice.objects.filter(user=user, device_hash=device_hash).exists()

        if not user_has_trusted:
            TrustedDevice.objects.create(
                user=user,
                device_hash=device_hash,
                device_name=ua[:250],
                expire_at=now + timedelta(days=30)
            )
        elif not is_device_trusted:
            log_audit(user, 'new_device_login', summary=f"Unrecognized new device login from IP {ip}", ip=ip)
            if user.email:
                try:
                    from django.core.mail import send_mail
                    send_mail(
                        subject="Security Alert: New Device Login Detected",
                        message=f"Hi {user.email},\n\nA new device login was detected for your FieldTrack account.\nIP Address: {ip}\nBrowser: {ua[:100]}\nTime: {now.strftime('%d/%m/%Y %I:%M %p')}\n\nIf this was not you, please change your password immediately.",
                        from_email="noreply@fieldtrack.com",
                        recipient_list=[user.email],
                        fail_silently=True
                    )
                except Exception as mail_err:
                    logger.warning(f"Failed sending new device alert email to {user.email}: {mail_err}")

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

        pad_response()
        return self.redirect_based_on_role(user)

    def redirect_based_on_role(self, user):
        from apps.accounts.engine import PermissionEngine
        if user.is_superuser or PermissionEngine.evaluate(user, 'accounts.view').allowed:
            return redirect('/admin-panel/dashboard/')
        return redirect('/staff/home/')


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
            if request.headers.get('HX-Request') == 'true':
                return render(request, 'accounts/partials/change_password_form.html')
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
        if request.headers.get('HX-Request') == 'true':
            return render(request, 'accounts/partials/change_password_form.html')
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


@method_decorator(require_reauth, name='dispatch')
class AdminForceLogoutUserView(LoginRequiredMixin, View):
    """
    POST /admin-panel/users/<int:pk>/force-logout/
    Admin force-logout action. Kills all active sessions for targeted user.
    """
    def post(self, request, pk):
        from apps.accounts.engine import PermissionEngine
        if not (request.user.is_superuser or PermissionEngine.evaluate(request.user, 'accounts.edit').allowed):
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


@method_decorator(require_reauth, name='dispatch')
class AdminUnlockUserView(LoginRequiredMixin, View):
    """
    POST /admin-panel/users/<int:pk>/unlock/
    Admin manual unlock action. Resets failed attempts and clear locked_until.
    """
    def post(self, request, pk):
        from apps.accounts.engine import PermissionEngine
        if not (request.user.is_superuser or PermissionEngine.evaluate(request.user, 'accounts.edit').allowed):
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
        from apps.accounts.engine import PermissionEngine
        if not (request.user.is_superuser or PermissionEngine.evaluate(request.user, 'notifications.view').allowed):
            return redirect('/')

        status_filter = request.GET.get('status')
        activities = UserLoginActivity.objects.select_related('user').order_by('-timestamp')

        if status_filter:
            activities = activities.filter(status=status_filter)

        context = {
            'activities': activities[:100],
            'status_filter': status_filter
        }
        if request.headers.get('HX-Request') == 'true':
            return render(request, 'admin_panel/partials/login_activity_partial.html', context)
        return render(request, 'admin_panel/login_activity.html', context)

    def post(self, request):
        from apps.accounts.engine import PermissionEngine
        if not (request.user.is_superuser or PermissionEngine.evaluate(request.user, 'notifications.edit').allowed):
            return JsonResponse({'status': 'error', 'message': 'Forbidden'}, status=403)

        ids = request.POST.getlist('ids') or request.POST.get('ids', '').split(',')
        ids = [i for i in ids if str(i).isdigit()]
        if ids:
            deleted_count, _ = UserLoginActivity.objects.filter(id__in=ids).delete()
            log_audit(request.user, 'bulk_login_activity_delete', summary=f"Bulk deleted {deleted_count} UserLoginActivity entries", ip=get_client_ip(request))
            messages.success(request, f"Successfully deleted {deleted_count} login activity records.")

        activities = UserLoginActivity.objects.select_related('user').order_by('-timestamp')[:100]
        if request.headers.get('HX-Request') == 'true':
            return render(request, 'admin_panel/login_activity.html', {'activities': activities})
        return redirect('accounts:admin_login_activity')


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
        ctx = {
            'sessions': sessions,
            'current_session_key': current_session_key
        }
        if request.headers.get('HX-Request'):
            return render(request, 'cotton/session-list.html', ctx)
        return render(request, 'accounts/user_sessions.html', ctx)

    def post(self, request):
        ids = request.POST.getlist('ids') or request.POST.get('ids', '').split(',')
        ids = [i for i in ids if str(i).isdigit()]
        if ids:
            sessions = UserSession.objects.filter(user=request.user, id__in=ids)
            now = timezone.now()
            count = 0
            for s in sessions:
                if s.is_active:
                    s.is_active = False
                    s.logout_time = now
                    s.save(update_fields=['is_active', 'logout_time'])
                    if s.session_key:
                        Session.objects.filter(session_key=s.session_key).delete()
                else:
                    s.delete()
                count += 1

            log_audit(request.user, 'bulk_session_delete', summary=f"Bulk processed {count} session records", ip=get_client_ip(request))
            messages.success(request, f"Successfully processed {count} session records.")

        sessions = UserSession.objects.filter(user=request.user).order_by('-login_time')[:10]
        current_session_key = request.session.session_key
        return render(request, 'cotton/session-list.html', {
            'sessions': sessions,
            'current_session_key': current_session_key
        })


def index_view(request):
    return render(request, 'index.html')


from apps.accounts.models import WorkspaceLockEvent

class WorkspaceLockView(LoginRequiredMixin, View):
    """
    POST /security/workspace-lock/lock/
    Records a workspace lock event.
    """
    def post(self, request):
        reason = request.POST.get('reason', 'idle')
        session_key = request.session.session_key
        curr_session = UserSession.objects.filter(user=request.user, session_key=session_key, is_active=True).first()

        evt = WorkspaceLockEvent.objects.create(
            user=request.user,
            session=curr_session,
            lock_reason=reason,
            locked_at=timezone.now()
        )
        log_audit(request.user, 'workspace_locked', target=evt, summary=f"Workspace locked ({reason})", ip=get_client_ip(request))
        return JsonResponse({'status': 'locked', 'event_id': evt.id})


class WorkspaceUnlockView(LoginRequiredMixin, View):
    """
    POST /security/workspace-lock/unlock/
    Authenticates user password, PIN, or MFA code to unlock workspace overlay based on role SecurityPolicy.
    """
    def post(self, request):
        credential = request.POST.get('password') or request.POST.get('credential') or ''
        sec_prof = getattr(request.user, 'security_profile', None)
        policy = SecurityPolicy.objects.filter(role=request.user.role).first()
        unlock_method = policy.unlock_method if policy else 'password'

        is_valid = False
        method_used = 'password'

        if unlock_method == 'pin' and sec_prof and sec_prof.pin_hash:
            is_valid = sec_prof.check_pin(credential)
            method_used = 'pin'

        if not is_valid and unlock_method == 'mfa' and sec_prof:
            is_valid = sec_prof.verify_totp(credential) or sec_prof.verify_backup_code(credential)
            method_used = 'mfa'

        if not is_valid:
            is_valid = request.user.check_password(credential)

        if not is_valid:
            log_audit(request.user, 'workspace_unlock_failed', summary="Failed workspace unlock attempt", ip=get_client_ip(request))
            return JsonResponse({'valid': False, 'message': 'Incorrect password or credential. Please try again.'}, status=400)

        evt = WorkspaceLockEvent.objects.filter(user=request.user, unlocked_at__isnull=True).order_by('-locked_at').first()
        if evt:
            evt.unlocked_at = timezone.now()
            evt.unlock_method = method_used
            evt.save(update_fields=['unlocked_at', 'unlock_method'])

        log_audit(request.user, 'workspace_unlocked', summary=f"Workspace unlocked successfully via {method_used}", ip=get_client_ip(request))
        return JsonResponse({'valid': True, 'message': 'Unlocked'})


class SecurityHeartbeatView(View):
    """
    GET /security/heartbeat/
    Periodic client ping (every 30s). Validates server session & forces instant logout if session invalidated.
    """
    def get(self, request):
        if not request.user.is_authenticated:
            return JsonResponse({'valid': False, 'reason': 'unauthenticated'}, status=401)

        session_key = request.session.session_key
        active_sess = UserSession.objects.filter(
            user=request.user,
            session_key=session_key,
            is_active=True
        ).first()

        if not active_sess or not request.user.is_active:
            return JsonResponse({'valid': False, 'reason': 'session_invalidated'}, status=401)

        return JsonResponse({'valid': True, 'timestamp': timezone.now().isoformat()})


import io
import base64
import qrcode
from apps.accounts.models import UserSecurityProfile

def generate_qr_code_base64(totp_uri):
    img = qrcode.make(totp_uri)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return base64.b64encode(buf.getvalue()).decode('utf-8')


class MFASetupView(LoginRequiredMixin, View):
    """
    GET/POST /account/mfa/setup/
    TOTP MFA setup wizard with QR code and code verification.
    """
    def get(self, request):
        sec_prof, _ = UserSecurityProfile.objects.get_or_create(user=request.user)
        if sec_prof.mfa_enabled:
            return render(request, 'accounts/mfa_setup.html')

        secret_key = sec_prof.generate_new_secret()
        sec_prof.save()

        totp_uri = sec_prof.get_totp_uri()
        qr_code_base64 = generate_qr_code_base64(totp_uri)

        return render(request, 'accounts/mfa_setup.html', {
            'secret_key': secret_key,
            'qr_code_base64': qr_code_base64
        })

    def post(self, request):
        sec_prof, _ = UserSecurityProfile.objects.get_or_create(user=request.user)
        totp_code = request.POST.get('totp_code', '').strip()

        if sec_prof.verify_totp(totp_code):
            sec_prof.mfa_enabled = True
            sec_prof.mfa_enabled_at = timezone.now()
            sec_prof.save()
            raw_backup_codes = sec_prof.generate_backup_codes()
            log_audit(request.user, 'mfa_enabled', summary="User enabled TOTP Multi-Factor Authentication", ip=get_client_ip(request))
            return render(request, 'accounts/mfa_backup_codes.html', {
                'backup_codes': raw_backup_codes
            })

        messages.error(request, "Invalid 6-digit verification code. Please check your authenticator app.")
        totp_uri = sec_prof.get_totp_uri()
        qr_code_base64 = generate_qr_code_base64(totp_uri)
        return render(request, 'accounts/mfa_setup.html', {
            'secret_key': sec_prof.mfa_secret,
            'qr_code_base64': qr_code_base64
        })


class MFADisableView(LoginRequiredMixin, View):
    """
    POST /account/mfa/disable/
    Disables MFA for current user.
    """
    def post(self, request):
        sec_prof = getattr(request.user, 'security_profile', None)
        if sec_prof:
            sec_prof.mfa_enabled = False
            sec_prof.mfa_secret = ''
            sec_prof.backup_codes = []
            sec_prof.save()
            log_audit(request.user, 'mfa_disabled', summary="User disabled TOTP Multi-Factor Authentication", ip=get_client_ip(request))
            messages.success(request, "Two-Factor Authentication disabled successfully.")
        return redirect('accounts:mfa_setup')


class LoginMFAVerifyView(View):
    """
    POST /login/mfa/verify/
    Verifies TOTP or backup code during login flow.
    """
    def post(self, request):
        user_id = request.session.get('pending_mfa_user_id')
        if not user_id:
            if request.headers.get('HX-Request') == 'true':
                response = render(request, 'accounts/partials/login_mfa_step.html', {'mfa_error': 'Session expired. Please log in again.'})
                response['HX-Redirect'] = '/login/'
                return response
            return redirect('accounts:login')

        user = CustomUser.objects.filter(pk=user_id).first()
        if not user:
            return redirect('accounts:login')

        code = request.POST.get('mfa_code', '').strip()
        remember_device = request.POST.get('remember_device') == 'true'
        sec_prof = getattr(user, 'security_profile', None)

        is_valid = False
        used_backup = False

        if sec_prof and sec_prof.mfa_enabled:
            if sec_prof.verify_totp(code):
                is_valid = True
            elif sec_prof.verify_backup_code(code):
                is_valid = True
                used_backup = True

        ip = get_client_ip(request)
        ua = request.META.get('HTTP_USER_AGENT', '')
        device_id = get_device_id(request)

        if is_valid:
            del request.session['pending_mfa_user_id']

            if used_backup:
                log_audit(user, 'backup_code_used', summary="MFA verified via one-time backup code", ip=ip)
            else:
                log_audit(user, 'mfa_verify_success', summary="MFA TOTP code verified successfully", ip=ip)

            # Record login activity & clear old sessions
            now = timezone.now()
            UserLoginActivity.objects.create(
                user=user,
                identifier_entered=user.email,
                ip_address=ip,
                user_agent=ua,
                status='success'
            )

            old_sessions = UserSession.objects.filter(user=user, is_active=True)
            for old_sess in old_sessions:
                old_sess.is_active = False
                old_sess.logout_time = now
                old_sess.save(update_fields=['is_active', 'logout_time'])

            if not hasattr(user, 'backend') or not user.backend:
                user.backend = 'apps.accounts.backends.EmailOrPhoneModelBackend'
            login(request, user)
            request.session.cycle_key()

            # Record TrustedDevice if requested
            if remember_device:
                device_hash = hashlib.sha256(f"{user.id}-{device_id}".encode('utf-8')).hexdigest()
                TrustedDevice.objects.update_or_create(
                    user=user,
                    device_hash=device_hash,
                    defaults={
                        'device_name': ua[:250],
                        'expire_at': now + timedelta(days=30)
                    }
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

            target_url = '/admin-panel/dashboard/' if user.role == 'admin' else '/staff/home/'
            if request.headers.get('HX-Request') == 'true':
                response = render(request, 'accounts/partials/login_mfa_step.html')
                response['HX-Redirect'] = target_url
                return response
            return redirect(target_url)

        log_audit(user, 'mfa_verify_fail', summary="Failed MFA verification attempt", ip=ip)
        return render(request, 'accounts/partials/login_mfa_step.html', {
            'mfa_error': 'Invalid verification code. Please check your authenticator app or backup codes.'
        })


@method_decorator(require_reauth, name='dispatch')
class AdminDisableUserMFAView(LoginRequiredMixin, View):
    """
    POST /admin-panel/users/<int:pk>/mfa/disable/
    Admin action to disable a user's MFA if they lost their device & backup codes.
    """
    def post(self, request, pk):
        from apps.accounts.engine import PermissionEngine
        if not (request.user.is_superuser or PermissionEngine.evaluate(request.user, 'accounts.edit').allowed):
            return JsonResponse({'status': 'error', 'message': 'Forbidden'}, status=403)

        target_user = CustomUser.objects.filter(pk=pk).first()
        if not target_user:
            return JsonResponse({'status': 'error', 'message': 'User not found'}, status=404)

        sec_prof = getattr(target_user, 'security_profile', None)
        if sec_prof:
            sec_prof.mfa_enabled = False
            sec_prof.mfa_secret = ''
            sec_prof.backup_codes = []
            sec_prof.save()
            log_audit(request.user, 'mfa_disabled_by_admin', target=target_user, summary=f"Admin disabled MFA for user {target_user.email}", ip=get_client_ip(request))
            messages.success(request, f"MFA disabled for user {target_user.email}.")

        return redirect('accounts:admin_login_activity')


from apps.accounts.models import SecurityPolicy

@method_decorator(require_reauth, name='dispatch')
class AdminSecurityPolicyListView(LoginRequiredMixin, View):
    """
    GET/POST /admin-panel/security/policies/
    Manages per-role SecurityPolicy configurations.
    """
    def get(self, request):
        from apps.accounts.engine import PermissionEngine
        if not (request.user.is_superuser or PermissionEngine.evaluate(request.user, 'accounts.view').allowed):
            return redirect('/')

        roles = ['admin', 'manager', 'staff']
        for r in roles:
            SecurityPolicy.objects.get_or_create(role=r)

        policies = SecurityPolicy.objects.filter(role__in=roles).order_by('role')
        return render(request, 'admin_panel/security_policies.html', {'policies': policies})

    def post(self, request):
        from apps.accounts.engine import PermissionEngine
        if not (request.user.is_superuser or PermissionEngine.evaluate(request.user, 'accounts.edit').allowed):
            return JsonResponse({'status': 'error', 'message': 'Forbidden'}, status=403)

        role = request.POST.get('role')
        policy = SecurityPolicy.objects.filter(role=role).first()
        if not policy:
            return JsonResponse({'status': 'error', 'message': 'Policy not found'}, status=404)

        policy.mfa_required = request.POST.get('mfa_required') == 'true'
        policy.unlock_method = request.POST.get('unlock_method', 'password')
        try:
            policy.reauth_interval_hours = int(request.POST.get('reauth_interval_hours', 4))
        except (ValueError, TypeError):
            policy.reauth_interval_hours = 4

        try:
            policy.trusted_device_days = int(request.POST.get('trusted_device_days', 30))
        except (ValueError, TypeError):
            policy.trusted_device_days = 30

        policy.save()
        log_audit(request.user, 'security_policy_changed', target=policy, summary=f"Updated SecurityPolicy for role '{role}'", ip=get_client_ip(request))
        messages.success(request, f"Security Policy for '{role}' updated successfully.")

        if request.headers.get('HX-Request') == 'true':
            return JsonResponse({'status': 'success'})
        return redirect('accounts:admin_security_policies')


class SecurityReauthView(LoginRequiredMixin, View):
    """
    POST /security/reauth/
    Verifies user credential (password or TOTP) for sensitive operation re-authentication.
    """
    def post(self, request):
        credential = request.POST.get('reauth_credential', '').strip()
        target_url = request.POST.get('target_url') or request.session.get('pending_reauth_target') or '/'
        sec_prof = getattr(request.user, 'security_profile', None)

        is_valid = request.user.check_password(credential)
        if not is_valid and sec_prof:
            is_valid = sec_prof.verify_totp(credential) or sec_prof.verify_backup_code(credential)

        if is_valid:
            session_key = request.session.session_key
            user_sess = UserSession.objects.filter(user=request.user, session_key=session_key, is_active=True).first()
            if user_sess:
                user_sess.last_reauth_at = timezone.now()
                user_sess.save(update_fields=['last_reauth_at'])

            log_audit(request.user, 'sensitive_action_reauth_success', summary=f"Re-authenticated for sensitive action at {target_url}", ip=get_client_ip(request))

            if request.headers.get('HX-Request') == 'true':
                response = render(request, 'accounts/partials/reauth_modal.html')
                response['HX-Redirect'] = target_url
                return response
            return redirect(target_url)

        log_audit(request.user, 'sensitive_action_reauth_fail', summary="Failed sensitive action re-authentication", ip=get_client_ip(request))
        return render(request, 'accounts/partials/reauth_modal.html', {
            'reauth_error': 'Authentication failed. Please check your password or 6-digit code.',
            'target_url': target_url
        })



# ─────────────────────────────────────────────────────────────────────────────
# Security Settings Page + MFA Wizard  (Phase 1 – Step 9 UX refactor)
# ─────────────────────────────────────────────────────────────────────────────
import re

def _get_security_policy(user):
    """Return the SecurityPolicy for this user's role, or None."""
    return SecurityPolicy.objects.filter(role=user.role).first()


def _gate_passed(request):
    """True if the user already verified their password in this session."""
    return request.session.get('mfa_wizard_gate_passed', False)


def _require_gate(request):
    """Redirect to the security page (wizard will re-open at gate step) if gate not passed."""
    return redirect('accounts:security_settings')


class SecuritySettingsView(LoginRequiredMixin, View):
    """
    GET /account/security/
    Self-service security hub: MFA status, trusted devices, backup-code count.
    """
    def get(self, request):
        sec_prof, _ = UserSecurityProfile.objects.get_or_create(user=request.user)
        policy = _get_security_policy(request.user)
        mfa_required = policy.mfa_required if policy else False

        trusted_devices = TrustedDevice.objects.filter(
            user=request.user,
            expire_at__gt=timezone.now()
        ).order_by('-created_at')

        backup_code_count = len(sec_prof.backup_codes) if sec_prof.backup_codes else 0

        # Get active device sessions
        sessions = UserSession.objects.filter(user=request.user).order_by('-login_time')[:10]
        current_session_key = request.session.session_key

        # Clear stale gate flag on plain page load (not htmx)
        if not request.headers.get('HX-Request'):
            request.session.pop('mfa_wizard_gate_passed', None)
            request.session.pop('mfa_wizard_secret', None)
            request.session.pop('mfa_wizard_raw_codes', None)

        return render(request, 'accounts/security_settings.html', {
            'sec_prof': sec_prof,
            'mfa_required': mfa_required,
            'policy': policy,
            'trusted_devices': trusted_devices,
            'backup_code_count': backup_code_count,
            'sessions': sessions,
            'current_session_key': current_session_key,
        })


# ── Wizard Step 0: Password Gate ─────────────────────────────────────────────

class MFAWizardGateView(LoginRequiredMixin, View):
    """
    POST /account/security/mfa/wizard/gate/
    Verify password (+ PIN if set). Sets session flag on success.
    """
    def post(self, request):
        password = request.POST.get('password', '').strip()
        pin = request.POST.get('pin', '').strip()

        sec_prof, _ = UserSecurityProfile.objects.get_or_create(user=request.user)

        # Always verify password
        if not request.user.check_password(password):
            return render(request, 'accounts/partials/mfa_wizard/step0_gate.html', {
                'error': 'Incorrect password. Please try again.',
                'has_pin': bool(sec_prof.pin_hash),
            })

        # If PIN is set, also require it
        if sec_prof.pin_hash:
            if not pin or not sec_prof.check_pin(pin):
                return render(request, 'accounts/partials/mfa_wizard/step0_gate.html', {
                    'error': 'Incorrect PIN. Please try again.',
                    'has_pin': True,
                })

        # Gate passed — generate a fresh secret for setup
        request.session['mfa_wizard_gate_passed'] = True
        secret = sec_prof.generate_new_secret()
        sec_prof.save(update_fields=['mfa_secret'])
        request.session['mfa_wizard_secret'] = secret

        log_audit(request.user, 'mfa_wizard_gate_passed', summary='Password gate verified for MFA wizard', ip=get_client_ip(request))

        # Return Step 1 QR partial
        totp_uri = sec_prof.get_totp_uri()
        qr_b64 = generate_qr_code_base64(totp_uri)
        return render(request, 'accounts/partials/mfa_wizard/step1_qr.html', {
            'secret_key': secret,
            'qr_code_base64': qr_b64,
        })


# ── Wizard Step 1: QR Code (GET reload) ──────────────────────────────────────

class MFAWizardQRView(LoginRequiredMixin, View):
    """
    GET /account/security/mfa/wizard/qr/
    Returns Step 1 partial (gate must already be passed).
    """
    def get(self, request):
        if not _gate_passed(request):
            return render(request, 'accounts/partials/mfa_wizard/step0_gate.html', {
                'error': 'Session expired. Please re-verify your password.',
                'has_pin': bool(getattr(getattr(request.user, 'security_profile', None), 'pin_hash', None)),
            })
        sec_prof, _ = UserSecurityProfile.objects.get_or_create(user=request.user)
        totp_uri = sec_prof.get_totp_uri()
        qr_b64 = generate_qr_code_base64(totp_uri)
        return render(request, 'accounts/partials/mfa_wizard/step1_qr.html', {
            'secret_key': sec_prof.mfa_secret,
            'qr_code_base64': qr_b64,
        })


# ── Wizard Step 2: Verify TOTP ────────────────────────────────────────────────

class MFAWizardVerifyView(LoginRequiredMixin, View):
    """
    POST /account/security/mfa/wizard/verify/
    Verify the 6-digit TOTP code against the pending secret.
    """
    def post(self, request):
        if not _gate_passed(request):
            return _require_gate(request)

        code = request.POST.get('totp_code', '').strip()
        sec_prof, _ = UserSecurityProfile.objects.get_or_create(user=request.user)

        if not sec_prof.verify_totp(code):
            totp_uri = sec_prof.get_totp_uri()
            qr_b64 = generate_qr_code_base64(totp_uri)
            return render(request, 'accounts/partials/mfa_wizard/step2_verify.html', {
                'error': 'Invalid code. Check your authenticator app and try again.',
                'secret_key': sec_prof.mfa_secret,
                'qr_code_base64': qr_b64,
            })

        # Code valid — generate backup codes and store raw in session (shown once)
        raw_codes = sec_prof.generate_backup_codes()
        request.session['mfa_wizard_raw_codes'] = raw_codes

        return render(request, 'accounts/partials/mfa_wizard/step3_codes.html', {
            'backup_codes': raw_codes,
        })


# ── Wizard Step 3: Backup Codes Acknowledgment ───────────────────────────────

class MFAWizardCompleteView(LoginRequiredMixin, View):
    """
    POST /account/security/mfa/wizard/complete/
    Checkbox ack → commit mfa_enabled=True, audit, clear session.
    """
    def post(self, request):
        if not _gate_passed(request):
            return _require_gate(request)

        if request.POST.get('acknowledged') != 'true':
            raw_codes = request.session.get('mfa_wizard_raw_codes', [])
            return render(request, 'accounts/partials/mfa_wizard/step3_codes.html', {
                'backup_codes': raw_codes,
                'error': 'Please confirm you have saved your backup codes.',
            })

        sec_prof, _ = UserSecurityProfile.objects.get_or_create(user=request.user)
        sec_prof.mfa_enabled = True
        sec_prof.mfa_enabled_at = timezone.now()
        sec_prof.save(update_fields=['mfa_enabled', 'mfa_enabled_at'])

        # Clear wizard session state
        for k in ('mfa_wizard_gate_passed', 'mfa_wizard_secret', 'mfa_wizard_raw_codes'):
            request.session.pop(k, None)

        log_audit(request.user, 'mfa_enabled', summary='User enabled TOTP Multi-Factor Authentication via wizard', ip=get_client_ip(request))

        response = render(request, 'accounts/partials/mfa_wizard/complete.html')
        response['HX-Redirect'] = '/account/security/'
        return response


# ── Disable MFA Flow ──────────────────────────────────────────────────────────

class MFADisableWizardView(LoginRequiredMixin, View):
    """
    POST /account/security/mfa/disable/
    Requires password + current TOTP or backup code.
    Blocked if role has mfa_required=True.
    """
    def post(self, request):
        policy = _get_security_policy(request.user)
        if policy and policy.mfa_required:
            return JsonResponse({
                'status': 'error',
                'message': 'MFA is required by your role security policy and cannot be disabled.'
            }, status=403)

        password = request.POST.get('password', '').strip()
        mfa_code = request.POST.get('mfa_code', '').strip()

        if not password or not mfa_code:
            return render(request, 'accounts/partials/mfa_wizard/disable_form.html', {
                'error': 'Both password and current MFA code are required.',
            })

        if not request.user.check_password(password):
            return render(request, 'accounts/partials/mfa_wizard/disable_form.html', {
                'error': 'Incorrect password.',
            })

        sec_prof = getattr(request.user, 'security_profile', None)
        if not sec_prof:
            return render(request, 'accounts/partials/mfa_wizard/disable_form.html', {
                'error': 'No security profile found.',
            })

        code_valid = sec_prof.verify_totp(mfa_code) or sec_prof.verify_backup_code(mfa_code)
        if not code_valid:
            return render(request, 'accounts/partials/mfa_wizard/disable_form.html', {
                'error': 'Invalid MFA code. Enter your current 6-digit TOTP or a backup code.',
            })

        sec_prof.mfa_enabled = False
        sec_prof.mfa_secret = ''
        sec_prof.backup_codes = []
        sec_prof.save(update_fields=['mfa_enabled', 'mfa_secret', 'backup_codes'])

        log_audit(request.user, 'mfa_disabled', summary='User disabled TOTP Multi-Factor Authentication', ip=get_client_ip(request))

        response = render(request, 'accounts/partials/mfa_wizard/disable_form.html', {'success': True})
        response['HX-Redirect'] = '/account/security/'
        return response


# ── Trusted Device Management ─────────────────────────────────────────────────

class TrustedDeviceRemoveView(LoginRequiredMixin, View):
    """
    POST /account/security/trusted-device/<int:pk>/remove/
    htmx: removes one device, returns updated list partial.
    """
    def post(self, request, pk):
        device = TrustedDevice.objects.filter(pk=pk, user=request.user).first()
        if device:
            device_name = device.device_name or device.device_hash[:8]
            device.delete()
            log_audit(request.user, 'trusted_device_removed', summary=f'Trusted device removed: {device_name}', ip=get_client_ip(request))

        trusted_devices = TrustedDevice.objects.filter(
            user=request.user,
            expire_at__gt=timezone.now()
        ).order_by('-created_at')
        return render(request, 'accounts/partials/trusted_device_list.html', {
            'trusted_devices': trusted_devices,
        })


# ── Backup Codes Regenerate ───────────────────────────────────────────────────

class BackupCodesRegenerateView(LoginRequiredMixin, View):
    """
    POST /account/security/backup-codes/regenerate/
    Re-generates backup codes. Requires password + TOTP/backup code.
    Returns new codes in a modal partial.
    """
    def post(self, request):
        password = request.POST.get('password', '').strip()
        mfa_code = request.POST.get('mfa_code', '').strip()

        if not request.user.check_password(password):
            return render(request, 'accounts/partials/mfa_wizard/regen_codes.html', {
                'error': 'Incorrect password.',
            })

        sec_prof = getattr(request.user, 'security_profile', None)
        if not sec_prof or not sec_prof.mfa_enabled:
            return render(request, 'accounts/partials/mfa_wizard/regen_codes.html', {
                'error': 'MFA is not enabled on this account.',
            })

        code_valid = sec_prof.verify_totp(mfa_code) or sec_prof.verify_backup_code(mfa_code)
        if not code_valid:
            return render(request, 'accounts/partials/mfa_wizard/regen_codes.html', {
                'error': 'Invalid MFA code.',
            })

        raw_codes = sec_prof.generate_backup_codes()
        log_audit(request.user, 'backup_codes_regenerated', summary='User regenerated backup/recovery codes', ip=get_client_ip(request))

        return render(request, 'accounts/partials/mfa_wizard/regen_codes.html', {
            'backup_codes': raw_codes,
            'success': True,
        })


class SetupPINView(LoginRequiredMixin, View):
    """
    POST /account/security/pin/setup/
    Saves or changes the user's unlock PIN after password confirmation.
    """
    def post(self, request):
        policy = SecurityPolicy.objects.filter(role=request.user.role).first()
        if not policy or policy.unlock_method != 'pin':
            from django.http import HttpResponseForbidden
            return HttpResponseForbidden("PIN unlock is not permitted by your role's security policy.")

        password = request.POST.get('password', '').strip()
        pin = request.POST.get('pin', '').strip()
        confirm_pin = request.POST.get('confirm_pin', '').strip()
        sec_prof, _ = UserSecurityProfile.objects.get_or_create(user=request.user)

        # Re-use password check pattern
        if not request.user.check_password(password):
            return render(request, 'accounts/partials/pin_setup_form.html', {
                'error': 'Incorrect password. Please try again.',
                'pin_exists': bool(sec_prof.pin_hash)
            })

        # Validate PIN: must be numeric and exactly 4 digits
        import re
        if not re.match(r'^\d{4}$', pin):
            return render(request, 'accounts/partials/pin_setup_form.html', {
                'error': 'PIN must be exactly 4 digits and numeric.',
                'pin_exists': bool(sec_prof.pin_hash)
            })

        if pin != confirm_pin:
            return render(request, 'accounts/partials/pin_setup_form.html', {
                'error': 'PIN and confirm PIN do not match.',
                'pin_exists': bool(sec_prof.pin_hash)
            })

        sec_prof.set_pin(pin)
        log_audit(
            actor=request.user,
            action='pin_setup_success',
            target=sec_prof,
            summary="User successfully set/changed security PIN",
            ip=get_client_ip(request)
        )

        return render(request, 'accounts/partials/pin_setup_form.html', {
            'success': True,
            'pin_exists': True
        })

