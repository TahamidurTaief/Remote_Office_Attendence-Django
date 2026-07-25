from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from django.utils import timezone
from apps.accounts.views import get_client_ip
from apps.accounts.models import UserLoginActivity, UserSession
from apps.accounts.utils import parse_user_agent
from apps.notifications.models import log_audit, AuditLog

User = get_user_model()


class AuditFixesTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(
            email='audit_test@example.com',
            password='Password123!',
            role='admin'
        )

    def test_get_client_ip_precedence(self):
        # 1. X-Forwarded-For chain
        req1 = self.factory.get('/', HTTP_X_FORWARDED_FOR='203.0.113.195, 70.41.3.18, 150.172.238.178', REMOTE_ADDR='127.0.0.1')
        self.assertEqual(get_client_ip(req1), '203.0.113.195')

        # 2. X-Real-IP
        req2 = self.factory.get('/', HTTP_X_REAL_IP='198.51.100.22', REMOTE_ADDR='127.0.0.1')
        self.assertEqual(get_client_ip(req2), '198.51.100.22')

        # 3. Direct REMOTE_ADDR fallback
        req3 = self.factory.get('/', REMOTE_ADDR='192.0.2.1')
        self.assertEqual(get_client_ip(req3), '192.0.2.1')

    def test_user_login_activity_identifier(self):
        act = UserLoginActivity.objects.create(
            user=self.user,
            identifier_entered='audit_test@example.com',
            ip_address='192.0.2.1',
            user_agent='Mozilla/5.0',
            status='success'
        )
        self.assertEqual(act.identifier_entered, 'audit_test@example.com')
        self.assertEqual(act.ip_address, '192.0.2.1')

    def test_log_audit_actor_and_ip(self):
        req = self.factory.get('/', REMOTE_ADDR='198.51.100.5')
        req.user = self.user

        log = log_audit(req, 'test_action', target=self.user, summary='Testing audit log creation')
        self.assertEqual(log.actor, self.user)
        self.assertEqual(log.ip_address, '198.51.100.5')
        self.assertEqual(log.summary, 'Testing audit log creation')
        self.assertEqual(log.target_type, 'CustomUser')

    def test_user_agent_parser(self):
        chrome_win = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        safari_ios = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1"

        self.assertEqual(parse_user_agent(chrome_win), "Chrome on Windows")
        self.assertEqual(parse_user_agent(safari_ios), "Safari on iOS")

        sess = UserSession.objects.create(
            user=self.user,
            device_id='dev1234567890',
            browser=chrome_win,
            ip='192.0.2.1'
        )
        self.assertEqual(sess.device_display_name, "Chrome on Windows")

    def test_user_session_logout_time(self):
        now = timezone.now()
        sess = UserSession.objects.create(
            user=self.user,
            device_id='dev9876543210',
            browser='Mozilla/5.0',
            ip='192.0.2.1',
            is_active=True
        )
        self.assertIsNone(sess.logout_time)

        sess.is_active = False
        sess.logout_time = now
        sess.save()

        sess.refresh_from_db()
        self.assertFalse(sess.is_active)
        self.assertIsNotNone(sess.logout_time)

    def test_security_settings_navigation_link(self):
        self.client.force_login(self.user)
        resp = self.client.get('/admin-panel/dashboard/')
        self.assertIn(resp.status_code, [200, 302])
        if resp.status_code == 200:
            self.assertContains(resp, '/account/security/')

    def test_mfa_setup_cta_button_and_htmx_gate(self):
        self.client.force_login(self.user)
        # 1. Page load when MFA is disabled shows 'Set Up MFA' button and NOT step 0 form
        resp = self.client.get('/account/security/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Set Up MFA')
        self.assertNotContains(resp, 'Verify Your Identity')

        # 2. Clicking CTA (GET mfa_wizard_gate via htmx) loads Step 0 identity verification form
        gate_resp = self.client.get('/account/security/mfa/wizard/gate/', HTTP_HX_REQUEST='true')
        self.assertEqual(gate_resp.status_code, 200)
        self.assertContains(gate_resp, 'Verify Your Identity')
        self.assertContains(gate_resp, 'Current Password')

    def test_trusted_device_removal(self):
        from apps.accounts.models import TrustedDevice
        self.client.force_login(self.user)
        device = TrustedDevice.objects.create(
            user=self.user,
            device_hash='hash123456789',
            device_name='My Laptop',
            expire_at=timezone.now() + timezone.timedelta(days=30)
        )
        url = f'/account/security/trusted-device/{device.pk}/remove/'
        resp = self.client.post(url, HTTP_HX_REQUEST='true')
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(TrustedDevice.objects.filter(pk=device.pk).exists())
        self.assertContains(resp, 'No trusted devices')

    def test_workspace_password_feature_d(self):
        from apps.accounts.models import SecurityPolicy, UserSecurityProfile
        from apps.notifications.models import AuditLog

        # 1. Test model methods
        sec_prof, _ = UserSecurityProfile.objects.get_or_create(user=self.user)
        sec_prof.set_workspace_password('MySecretWS123')
        self.assertTrue(sec_prof.check_workspace_password('MySecretWS123'))
        self.assertFalse(sec_prof.check_workspace_password('WrongSecret'))

        # 2. Test Setup view validation
        self.client.force_login(self.user)
        policy, _ = SecurityPolicy.objects.get_or_create(role=self.user.role)
        policy.unlock_method = 'workspace_password'
        policy.save()

        setup_url = '/account/security/workspace-password/setup/'

        # Wrong current password
        resp = self.client.post(setup_url, {'password': 'wrongpassword', 'ws_password': 'NewPassword1', 'confirm_ws_password': 'NewPassword1'})
        self.assertContains(resp, 'Incorrect current password')

        # Short workspace password
        resp = self.client.post(setup_url, {'password': 'Password123!', 'ws_password': '123', 'confirm_ws_password': '123'})
        self.assertContains(resp, 'at least 6 characters')

        # Mismatched confirmation
        resp = self.client.post(setup_url, {'password': 'Password123!', 'ws_password': 'NewPassword1', 'confirm_ws_password': 'NewPassword2'})
        self.assertContains(resp, 'do not match')

        # Valid setup
        resp = self.client.post(setup_url, {'password': 'Password123!', 'ws_password': 'NewPassword1', 'confirm_ws_password': 'NewPassword1'})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'successfully')

        sec_prof.refresh_from_db()
        self.assertTrue(sec_prof.check_workspace_password('NewPassword1'))
        self.assertTrue(AuditLog.objects.filter(action='workspace_password_setup_success', actor=self.user).exists())

        # 3. Test WorkspaceUnlockView via workspace_password
        unlock_url = '/security/workspace-lock/unlock/'

        # Failed unlock
        fail_resp = self.client.post(unlock_url, {'password': 'WrongPassword'})
        self.assertEqual(fail_resp.status_code, 400)

        # Successful unlock
        success_resp = self.client.post(unlock_url, {'password': 'NewPassword1'})
        self.assertEqual(success_resp.status_code, 200)
        self.assertTrue(success_resp.json().get('valid'))
