import time
from django.test import TestCase, Client
from django.contrib.auth import get_user_model, authenticate
from django.urls import reverse
from apps.accounts.models import LoginProtection, CustomUser
from apps.accounts.login_protection import (
    check_3layer_lock,
    record_failed_attempt,
    record_successful_login
)

User = get_user_model()

class AuthenticationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.password = 'password123'

        self.user_both = User.objects.create_user(
            email='user_both@example.com',
            phone='+8801700000001',
            password=self.password,
            role='staff'
        )

        self.user_email = User.objects.create_user(
            email='user_email@example.com',
            password=self.password,
            role='staff'
        )

        self.user_phone = User.objects.create_user(
            phone='+8801700000003',
            password=self.password,
            role='staff'
        )

    def test_authenticate_by_email(self):
        user = authenticate(username='user_both@example.com', password=self.password)
        self.assertIsNotNone(user)
        self.assertEqual(user, self.user_both)

        user = authenticate(username='user_email@example.com', password=self.password)
        self.assertIsNotNone(user)
        self.assertEqual(user, self.user_email)

    def test_authenticate_by_phone(self):
        user = authenticate(username='+8801700000001', password=self.password)
        self.assertIsNotNone(user)
        self.assertEqual(user, self.user_both)

        user = authenticate(username='+8801700000003', password=self.password)
        self.assertIsNotNone(user)
        self.assertEqual(user, self.user_phone)

    def test_authenticate_invalid_credentials(self):
        user = authenticate(username='user_both@example.com', password='wrongpassword')
        self.assertIsNone(user)

        user = authenticate(username='nonexistent@example.com', password=self.password)
        self.assertIsNone(user)

    def test_login_view_email(self):
        response = self.client.post(reverse('accounts:login'), {
            'email': 'user_both@example.com',
            'password': self.password
        })
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, '/staff/home/')

    def test_login_view_phone(self):
        response = self.client.post(reverse('accounts:login'), {
            'email': '+8801700000001',
            'password': self.password
        })
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, '/staff/home/')

    def test_login_rate_limiting(self):
        LoginProtection.objects.all().delete()

        # Post 5 invalid logins
        for _ in range(5):
            response = self.client.post(reverse('accounts:login'), {
                'email': 'user_both@example.com',
                'password': 'wrongpassword'
            })
            self.assertEqual(response.status_code, 200)

        # 6th post should be locked immediately
        response = self.client.post(reverse('accounts:login'), {
            'email': 'user_both@example.com',
            'password': self.password
        })
        self.assertEqual(response.status_code, 200)

        from django.contrib.messages import get_messages
        messages_list = list(get_messages(response.wsgi_request))
        self.assertTrue(any('locked' in str(m).lower() or 'too many' in str(m).lower() for m in messages_list))

        LoginProtection.objects.all().delete()


class ProgressiveLoginProtectionTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.email = 'security_test@example.com'
        self.user = User.objects.create_user(
            email=self.email,
            password='password123',
            role='staff'
        )

    def test_3layer_progressive_lock_flow(self):
        ip = '127.0.0.1'
        device = 'test_device_uuid_123'

        # Attempt 1 & 2: normal
        record_failed_attempt(user=self.user, email=self.email, ip=ip, device_id=device)
        record_failed_attempt(user=self.user, email=self.email, ip=ip, device_id=device)
        is_locked, _, prot = check_3layer_lock(user=self.user, email=self.email, ip=ip, device_id=device)
        self.assertFalse(is_locked)
        self.assertFalse(prot.captcha_required if prot else False)

        # Attempt 3: Captcha required
        prot = record_failed_attempt(user=self.user, email=self.email, ip=ip, device_id=device)
        self.assertTrue(prot.captcha_required)
        is_locked, _, _ = check_3layer_lock(user=self.user, email=self.email, ip=ip, device_id=device)
        self.assertFalse(is_locked)

        # Attempt 4 & 5: Lock Level 1 (1 min)
        record_failed_attempt(user=self.user, email=self.email, ip=ip, device_id=device)
        prot = record_failed_attempt(user=self.user, email=self.email, ip=ip, device_id=device)
        self.assertEqual(prot.current_lock_level, 1)

        is_locked, remaining_secs, _ = check_3layer_lock(user=self.user, email=self.email, ip=ip, device_id=device)
        self.assertTrue(is_locked)
        self.assertGreater(remaining_secs, 0)

    def test_unknown_username_rate_limiting(self):
        # Unknown email gets exact same rate limit protection
        unknown_email = 'nonexistent_test_account@example.com'
        ip = '192.168.1.50'
        device = 'dev_unknown'

        for _ in range(5):
            prot = record_failed_attempt(email=unknown_email, ip=ip, device_id=device)

        is_locked, _, _ = check_3layer_lock(email=unknown_email, ip=ip, device_id=device)
        self.assertTrue(is_locked)

    def test_successful_login_resets_protection(self):
        ip = '127.0.0.1'
        device = 'dev_reset_test'

        record_failed_attempt(user=self.user, email=self.email, ip=ip, device_id=device)
        record_failed_attempt(user=self.user, email=self.email, ip=ip, device_id=device)

        record_successful_login(user=self.user, email=self.email, ip=ip, device_id=device)

        is_locked, _, _ = check_3layer_lock(user=self.user, email=self.email, ip=ip, device_id=device)
        prot = get_or_create_protection(user=self.user, email=self.email, ip=ip, device_id=device)
        self.assertFalse(is_locked)
        self.assertEqual(prot.failed_attempts, 0)


from apps.accounts.models import WorkspaceLockEvent, UserSession

class WorkspaceLockTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.password = 'password123'
        self.user = User.objects.create_user(
            email='lock_test@example.com',
            password=self.password,
            role='staff'
        )

    def test_workspace_lock_and_unlock_flow(self):
        self.client.post(reverse('accounts:login'), {'email': 'lock_test@example.com', 'password': self.password})

        # Lock workspace
        resp = self.client.post(reverse('accounts:workspace_lock'), {'reason': 'idle'})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(WorkspaceLockEvent.objects.filter(user=self.user, lock_reason='idle').exists())

        # Unlock with wrong password -> 400
        resp_err = self.client.post(reverse('accounts:workspace_unlock'), {'password': 'wrongpassword'})
        self.assertEqual(resp_err.status_code, 400)

        # Unlock with correct password -> 200
        resp_ok = self.client.post(reverse('accounts:workspace_unlock'), {'password': self.password})
        self.assertEqual(resp_ok.status_code, 200)
        self.assertTrue(resp_ok.json().get('valid'))

    def test_security_heartbeat_session_invalidation(self):
        self.client.post(reverse('accounts:login'), {'email': 'lock_test@example.com', 'password': self.password})

        # Active heartbeat
        resp = self.client.get(reverse('accounts:security_heartbeat'))
        self.assertEqual(resp.status_code, 200)

        # Invalidate session (e.g. force logout)
        UserSession.objects.filter(user=self.user).update(is_active=False)

        # Heartbeat returns 401
        resp_inv = self.client.get(reverse('accounts:security_heartbeat'))
        self.assertEqual(resp_inv.status_code, 401)


import pyotp
from apps.accounts.models import UserSecurityProfile

class MFATests(TestCase):
    def setUp(self):
        self.client = Client()
        self.password = 'password123'
        self.user = User.objects.create_user(
            email='mfa_test@example.com',
            password=self.password,
            role='staff'
        )

    def test_mfa_setup_and_verify_flow(self):
        self.client.post(reverse('accounts:login'), {'email': 'mfa_test@example.com', 'password': self.password})

        # GET setup page -> QR code generated
        resp = self.client.get(reverse('accounts:mfa_setup'))
        self.assertEqual(resp.status_code, 200)

        sec_prof = UserSecurityProfile.objects.get(user=self.user)
        self.assertFalse(sec_prof.mfa_enabled)
        self.assertTrue(len(sec_prof.mfa_secret) > 0)

        # POST setup with valid TOTP code
        totp = pyotp.TOTP(sec_prof.mfa_secret)
        code = totp.now()

        resp_setup = self.client.post(reverse('accounts:mfa_setup'), {'totp_code': code})
        self.assertEqual(resp_setup.status_code, 200)

        sec_prof.refresh_from_db()
        self.assertTrue(sec_prof.mfa_enabled)
        self.assertEqual(len(sec_prof.backup_codes), 8)

    def test_mfa_login_interception_and_backup_code(self):
        sec_prof = UserSecurityProfile.objects.create(user=self.user, mfa_enabled=True)
        sec_prof.generate_new_secret()
        raw_backup_codes = sec_prof.generate_backup_codes()
        sec_prof.save()

        # Login with password -> intercepted by MFA step
        resp = self.client.post(reverse('accounts:login'), {'email': 'mfa_test@example.com', 'password': self.password})
        self.assertEqual(resp.status_code, 200)

        # Verify with valid backup code
        backup_code = raw_backup_codes[0]
        resp_mfa = self.client.post(reverse('accounts:mfa_login_verify'), {'mfa_code': backup_code})
        self.assertEqual(resp_mfa.status_code, 302)

        # Verify backup code was used and invalidated
        sec_prof.refresh_from_db()
        self.assertEqual(len(sec_prof.backup_codes), 7)


