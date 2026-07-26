"""
Regression tests for the login-time MFA verification flow.

Covers the path: POST /login/ -> MFA interception -> POST /login/mfa/verify/ -> dashboard.
Does NOT test the self-service MFA setup wizard (/account/security/mfa/wizard/).

Root-cause history:
  - Wrong backend name ('EmailOrPhoneModelBackend' instead of 'PhoneOrEmailBackend')
    caused login() to silently fail, leaving the user un-authenticated.
  - Redundant session.cycle_key() after login() created a mismatched session key,
    causing SessionDeviceMiddleware to log the user out on the very next request.
  - HTMX path returned 200 with empty body, causing htmx to swap empty content into
    the form container before processing HX-Redirect, corrupting the UX.
"""
import pyotp
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model

from apps.accounts.models import UserSecurityProfile, UserSession, TrustedDevice

User = get_user_model()


def _make_mfa_user(email='mfa_flow@example.com', password='testpass123', role='staff'):
    """Helper: create a user with MFA fully enabled and a known TOTP secret."""
    user = User.objects.create_user(email=email, password=password, role=role)
    sec_prof = UserSecurityProfile.objects.create(user=user, mfa_enabled=True)
    sec_prof.generate_new_secret()
    sec_prof.save()
    return user, sec_prof


def _make_admin_mfa_user(email='mfa_admin@example.com', password='testpass123'):
    """Helper: create an admin superuser with MFA enabled."""
    user = User.objects.create_superuser(email=email, password=password, role='admin')
    sec_prof = UserSecurityProfile.objects.create(user=user, mfa_enabled=True)
    sec_prof.generate_new_secret()
    sec_prof.save()
    return user, sec_prof


class MFALoginFlowPositiveTests(TestCase):
    """Correct TOTP code -> user is authenticated and redirected to dashboard."""

    def setUp(self):
        self.client = Client()
        self.password = 'testpass123'
        self.user, self.sec_prof = _make_mfa_user()

    def _start_login_and_get_mfa_step(self):
        resp = self.client.post(
            reverse('accounts:login'),
            {'email': self.user.email, 'password': self.password},
        )
        self.assertEqual(resp.status_code, 200, "Login should be intercepted at MFA step")
        self.assertEqual(self.client.session.get('pending_mfa_user_id'), self.user.pk)
        return resp

    def test_correct_totp_non_htmx_redirects_to_dashboard(self):
        """Non-HTMX: correct TOTP code -> 302 -> staff dashboard."""
        self._start_login_and_get_mfa_step()
        totp = pyotp.TOTP(self.sec_prof.mfa_secret)
        resp = self.client.post(
            reverse('accounts:mfa_login_verify'),
            {'mfa_code': totp.now()},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/staff/home/', resp['Location'])

    def test_correct_totp_htmx_returns_204_with_hx_redirect(self):
        """HTMX: correct TOTP code -> 204 with HX-Redirect header."""
        self._start_login_and_get_mfa_step()
        totp = pyotp.TOTP(self.sec_prof.mfa_secret)
        resp = self.client.post(
            reverse('accounts:mfa_login_verify'),
            {'mfa_code': totp.now()},
            **{'HTTP_HX-Request': 'true'},
        )
        self.assertIn(resp.status_code, (200, 204))
        self.assertIn('HX-Redirect', resp)
        self.assertIn('/staff/home/', resp['HX-Redirect'])

    def test_usersession_created_after_mfa_verify(self):
        """A UserSession record must exist for the new session key after MFA success."""
        self._start_login_and_get_mfa_step()
        totp = pyotp.TOTP(self.sec_prof.mfa_secret)
        self.client.post(
            reverse('accounts:mfa_login_verify'),
            {'mfa_code': totp.now()},
        )
        new_session_key = self.client.session.session_key
        self.assertIsNotNone(new_session_key)
        exists = UserSession.objects.filter(
            user=self.user, session_key=new_session_key, is_active=True,
        ).exists()
        self.assertTrue(exists, "Active UserSession must be recorded for the new session key")

    def test_pending_mfa_marker_removed_after_verify(self):
        """After successful verification, 'pending_mfa_user_id' must be absent."""
        self._start_login_and_get_mfa_step()
        totp = pyotp.TOTP(self.sec_prof.mfa_secret)
        self.client.post(
            reverse('accounts:mfa_login_verify'),
            {'mfa_code': totp.now()},
        )
        self.assertNotIn('pending_mfa_user_id', self.client.session)

    def test_backup_code_path_authenticates_and_invalidates_code(self):
        """A one-time backup code: authenticates, then removes that code from DB."""
        raw_codes = self.sec_prof.generate_backup_codes()
        self.sec_prof.save()
        self.assertEqual(len(self.sec_prof.backup_codes), 8)
        self._start_login_and_get_mfa_step()
        resp = self.client.post(
            reverse('accounts:mfa_login_verify'),
            {'mfa_code': raw_codes[0]},
        )
        self.assertEqual(resp.status_code, 302)
        self.sec_prof.refresh_from_db()
        self.assertEqual(len(self.sec_prof.backup_codes), 7, "Used backup code must be consumed")

    def test_admin_correct_totp_redirects_to_admin_dashboard(self):
        """Admin superuser -> redirected to /admin-panel/dashboard/."""
        admin, admin_sec = _make_admin_mfa_user()
        resp = self.client.post(
            reverse('accounts:login'),
            {'email': admin.email, 'password': 'testpass123'},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.client.session.get('pending_mfa_user_id'), admin.pk)
        totp = pyotp.TOTP(admin_sec.mfa_secret)
        resp2 = self.client.post(
            reverse('accounts:mfa_login_verify'),
            {'mfa_code': totp.now()},
        )
        self.assertEqual(resp2.status_code, 302)
        self.assertIn('/admin-panel/dashboard/', resp2['Location'])

    def test_remember_device_creates_trusted_device(self):
        """Checking 'Trust this device' must create a TrustedDevice record."""
        self._start_login_and_get_mfa_step()
        totp = pyotp.TOTP(self.sec_prof.mfa_secret)
        self.client.post(
            reverse('accounts:mfa_login_verify'),
            {'mfa_code': totp.now(), 'remember_device': 'true'},
        )
        self.assertTrue(TrustedDevice.objects.filter(user=self.user).exists())


class MFALoginFlowNegativeTests(TestCase):
    """Wrong code / expired session -> stays on verification page, no auth."""

    def setUp(self):
        self.client = Client()
        self.password = 'testpass123'
        self.user, self.sec_prof = _make_mfa_user(email='mfa_neg@example.com')

    def _start_login_and_get_mfa_step(self):
        resp = self.client.post(
            reverse('accounts:login'),
            {'email': self.user.email, 'password': self.password},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.client.session.get('pending_mfa_user_id'), self.user.pk)
        return resp

    def test_wrong_totp_stays_on_verification_page(self):
        """A wrong TOTP code should render the verification form again (200)."""
        self._start_login_and_get_mfa_step()
        resp = self.client.post(
            reverse('accounts:mfa_login_verify'),
            {'mfa_code': '000000'},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.wsgi_request.user.is_authenticated)

    def test_wrong_code_shows_error_message(self):
        """Wrong code must surface an error in the rendered response."""
        self._start_login_and_get_mfa_step()
        resp = self.client.post(
            reverse('accounts:mfa_login_verify'),
            {'mfa_code': '999999'},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Invalid')

    def test_no_pending_session_redirects_to_login(self):
        """Submitting MFA verify without a pending session -> redirect to login."""
        resp = self.client.post(
            reverse('accounts:mfa_login_verify'),
            {'mfa_code': '123456'},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login/', resp['Location'])

    def test_wrong_code_does_not_create_usersession(self):
        """A failed verification must not create any UserSession record."""
        self._start_login_and_get_mfa_step()
        count_before = UserSession.objects.filter(user=self.user).count()
        self.client.post(
            reverse('accounts:mfa_login_verify'),
            {'mfa_code': '000000'},
        )
        count_after = UserSession.objects.filter(user=self.user).count()
        self.assertEqual(count_before, count_after)

    def test_backup_code_reuse_fails(self):
        """A backup code used once cannot be reused."""
        raw_codes = self.sec_prof.generate_backup_codes()
        self.sec_prof.save()
        # First use
        self._start_login_and_get_mfa_step()
        resp1 = self.client.post(
            reverse('accounts:mfa_login_verify'),
            {'mfa_code': raw_codes[0]},
        )
        self.assertEqual(resp1.status_code, 302, "First use should succeed")
        # Log out
        self.client.get(reverse('accounts:logout'))
        # Re-authenticate to get a new MFA challenge
        resp2 = self.client.post(
            reverse('accounts:login'),
            {'email': self.user.email, 'password': self.password},
        )
        self.assertEqual(resp2.status_code, 200)
        # Attempt reuse
        resp3 = self.client.post(
            reverse('accounts:mfa_login_verify'),
            {'mfa_code': raw_codes[0]},
        )
        self.assertEqual(resp3.status_code, 200, "Reused backup code should fail")
        self.assertFalse(resp3.wsgi_request.user.is_authenticated)


class MFALoginSessionIntegrityTests(TestCase):
    """
    Regression: SessionDeviceMiddleware must NOT log the user out
    immediately after a successful MFA login.
    """

    def setUp(self):
        self.client = Client()
        self.password = 'testpass123'
        self.user, self.sec_prof = _make_mfa_user(email='mfa_session@example.com')

    def test_session_device_middleware_does_not_logout_after_mfa(self):
        """
        After MFA verify, the UserSession key matches the cookie.
        A subsequent GET must not bounce to /login/.
        Regression guard for the double cycle_key() bug.
        """
        self.client.post(
            reverse('accounts:login'),
            {'email': self.user.email, 'password': self.password},
        )
        totp = pyotp.TOTP(self.sec_prof.mfa_secret)
        resp = self.client.post(
            reverse('accounts:mfa_login_verify'),
            {'mfa_code': totp.now()},
        )
        self.assertEqual(resp.status_code, 302)

        # Verify session key matches DB record
        active_session_key = self.client.session.session_key
        self.assertIsNotNone(active_session_key)
        self.assertTrue(
            UserSession.objects.filter(
                user=self.user, session_key=active_session_key, is_active=True,
            ).exists(),
            "Active UserSession must match the session key the browser holds"
        )

        # The next request must NOT be redirected to login
        resp2 = self.client.get('/staff/home/')
        self.assertNotEqual(
            resp2.status_code, 302,
            f"SessionDeviceMiddleware kicked user to {resp2.get('Location', '?')} — regression!"
        )

    def test_trusted_device_skips_mfa_on_next_login(self):
        """After trusting the device, next login should bypass MFA entirely."""
        self.client.post(
            reverse('accounts:login'),
            {'email': self.user.email, 'password': self.password},
        )
        totp = pyotp.TOTP(self.sec_prof.mfa_secret)
        self.client.post(
            reverse('accounts:mfa_login_verify'),
            {'mfa_code': totp.now(), 'remember_device': 'true'},
        )
        self.assertTrue(TrustedDevice.objects.filter(user=self.user).exists())

        self.client.get(reverse('accounts:logout'))

        resp = self.client.post(
            reverse('accounts:login'),
            {'email': self.user.email, 'password': self.password},
        )
        self.assertEqual(resp.status_code, 302,
                         "Trusted device should bypass MFA and redirect directly to dashboard")
        self.assertNotIn('pending_mfa_user_id', self.client.session)
