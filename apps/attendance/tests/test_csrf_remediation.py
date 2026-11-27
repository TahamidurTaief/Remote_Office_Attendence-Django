import tempfile
import json
import uuid
import io
from PIL import Image
from django.test import TestCase, Client, override_settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.middleware.csrf import get_token, rotate_token
from apps.employees.models import EmployeeProfile
from apps.branches.models import Branch
from apps.attendance.models import Attendance, AttendancePolicy

User = get_user_model()


def create_test_image():
    file = io.BytesIO()
    image = Image.new('RGB', (100, 100), color='blue')
    image.save(file, 'jpeg')
    file.seek(0)
    return SimpleUploadedFile('test_photo.jpg', file.read(), content_type='image/jpeg')


class CSRFRemediationTestSuite(TestCase):
    """
    Rigorously tests CSRF hardening, token refresh endpoint,
    custom CSRF failure response, token rotation recovery,
    origin verification, and idempotency across attendance paths.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._temp_media = tempfile.TemporaryDirectory()
        cls._media_override = override_settings(MEDIA_ROOT=cls._temp_media.name)
        cls._media_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._media_override.disable()
        cls._temp_media.cleanup()
        super().tearDownClass()

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.user = User.objects.create_user(
            email='staff_test@example.com',
            password='TestPassword123!'
        )
        self.branch = Branch.objects.create(
            name='Test Branch',
            address='Dhaka, Bangladesh',
            latitude=23.8103,
            longitude=90.4125
        )
        self.employee = EmployeeProfile.objects.create(
            user=self.user,
            employee_id='EMP-TEST-001',
            full_name='Test Employee',
            phone='01700000001',
            branch=self.branch,
            joined_date='2026-01-01',
            is_active=True
        )
        # Global policy allowing attendance
        self.policy = AttendancePolicy.objects.create(
            branch=self.branch,
            photo_required=False,
            gps_required='required',
            allow_outside_geofence=True
        )

    def test_settings_and_httponly_cookie(self):
        """Verify settings preserve HttpOnly and use custom failure view."""
        self.assertTrue(settings.CSRF_COOKIE_HTTPONLY, "CSRF_COOKIE_HTTPONLY must remain True")
        self.assertEqual(settings.CSRF_FAILURE_VIEW, 'apps.attendance.csrf.csrf_failure_view')
        self.assertTrue(settings.USE_X_FORWARDED_HOST, "USE_X_FORWARDED_HOST must be True for reverse proxies")

        # Initial request sets HttpOnly CSRF cookie
        response = self.client.get(reverse('accounts:login'))
        csrf_cookie = response.cookies.get(settings.CSRF_COOKIE_NAME)
        self.assertIsNotNone(csrf_cookie, "CSRF cookie must be issued")
        self.assertTrue(csrf_cookie['httponly'], "CSRF cookie must have HttpOnly flag set")

    def test_missing_csrf_token_returns_custom_json(self):
        """Missing CSRF token on attendance endpoint must return 403 JSON with stable error code and request_id."""
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('attendance:check_in'),
            {'latitude': 23.8103, 'longitude': 90.4125, 'accuracy': 10},
            HTTP_ACCEPT='application/json'
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response['Content-Type'], 'application/json')
        data = response.json()
        self.assertFalse(data['success'])
        self.assertEqual(data['code'], 'CSRF_FAILURE')
        self.assertIn('request_id', data)
        self.assertTrue(len(data['request_id']) > 0)
        self.assertIn('CSRF verification failed', data['error'])

    def test_untrusted_origin_rejected_with_csrf_failure(self):
        """Requests from untrusted origins must be rejected with 403 CSRF_FAILURE."""
        self.client.force_login(self.user)
        # Get a valid token first
        token_res = self.client.get(reverse('attendance:csrf_token'))
        valid_token = token_res.json()['csrf_token']

        response = self.client.post(
            reverse('attendance:check_in'),
            {'latitude': 23.8103, 'longitude': 90.4125},
            HTTP_X_CSRFTOKEN=valid_token,
            HTTP_ORIGIN='https://attacker-domain.evil.com',
            HTTP_ACCEPT='application/json'
        )
        self.assertEqual(response.status_code, 403)
        data = response.json()
        self.assertEqual(data['code'], 'CSRF_FAILURE')

    def test_trusted_origin_accepted(self):
        """Requests from trusted origin trackme.signtechlimited.com must pass CSRF validation."""
        self.client.force_login(self.user)
        token_res = self.client.get(reverse('attendance:csrf_token'))
        valid_token = token_res.json()['csrf_token']

        response = self.client.post(
            reverse('attendance:check_in'),
            {
                'latitude': 23.8103,
                'longitude': 90.4125,
                'accuracy': 15,
                'address': 'Head Office',
                'csrfmiddlewaretoken': valid_token
            },
            HTTP_X_CSRFTOKEN=valid_token,
            HTTP_ORIGIN='https://trackme.signtechlimited.com',
            HTTP_REFERER='https://trackme.signtechlimited.com/staff/check-in/',
            HTTP_ACCEPT='application/json'
        )
        # Should not be rejected by CSRF
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get('success'))

    def test_token_refresh_endpoint_authenticated(self):
        """Authenticated token refresh endpoint returns fresh masked token with no-cache headers."""
        self.client.force_login(self.user)
        response = self.client.get(reverse('attendance:csrf_token'))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertTrue(isinstance(data['csrf_token'], str))
        self.assertEqual(len(data['csrf_token']), 64, "Masked CSRF token must be 64 characters")
        self.assertIn('no-store', response['Cache-Control'])
        self.assertIn('no-cache', response['Cache-Control'])

    def test_token_refresh_endpoint_unauthenticated(self):
        """Unauthenticated requests to token refresh endpoint return 401."""
        response = self.client.get(reverse('attendance:csrf_token'))
        self.assertEqual(response.status_code, 401)
        data = response.json()
        self.assertFalse(data['success'])
        self.assertEqual(data['code'], 'UNAUTHENTICATED')

    def test_token_rotation_and_recovery(self):
        """
        Page opened before later login/rotation:
        1. Submit with old token fails with 403 CSRF_FAILURE.
        2. Refresh token from endpoint.
        3. Re-submit with new token succeeds without duplicate.
        """
        # Step 1: Initial token before login
        initial_res = self.client.get(reverse('accounts:login'))
        old_token = initial_res.context.get('csrf_token') or self.client.cookies[settings.CSRF_COOKIE_NAME].value

        # Step 2: Login rotates CSRF secret
        self.client.post(reverse('accounts:login'), {
            'email': 'staff_test@example.com',
            'password': 'TestPassword123!',
            'csrfmiddlewaretoken': old_token
        })

        # Step 3: Attempt submission with old token
        sync_uuid = str(uuid.uuid4())
        failed_res = self.client.post(
            reverse('attendance:check_in'),
            {
                'latitude': 23.8103,
                'longitude': 90.4125,
                'accuracy': 10,
                'sync_uuid': sync_uuid,
                'csrfmiddlewaretoken': old_token
            },
            HTTP_X_CSRFTOKEN=old_token,
            HTTP_ACCEPT='application/json'
        )
        self.assertEqual(failed_res.status_code, 403)
        self.assertEqual(failed_res.json()['code'], 'CSRF_FAILURE')

        # Step 4: Refresh token
        refresh_res = self.client.get(reverse('attendance:csrf_token'))
        self.assertEqual(refresh_res.status_code, 200)
        fresh_token = refresh_res.json()['csrf_token']
        self.assertNotEqual(fresh_token, old_token)

        # Step 5: Resubmit with fresh token (both header and form body)
        retry_res = self.client.post(
            reverse('attendance:check_in'),
            {
                'latitude': 23.8103,
                'longitude': 90.4125,
                'accuracy': 10,
                'sync_uuid': sync_uuid,
                'csrfmiddlewaretoken': fresh_token
            },
            HTTP_X_CSRFTOKEN=fresh_token,
            HTTP_ACCEPT='application/json'
        )
        self.assertEqual(retry_res.status_code, 200)
        self.assertTrue(retry_res.json()['success'])

        # Verify no duplicate check-ins created
        attendances = Attendance.objects.filter(employee=self.employee, sync_uuid=sync_uuid)
        self.assertEqual(attendances.count(), 1)

    def test_stale_post_body_token_takes_precedence_over_header(self):
        """
        Verify Django behavior:
        If POST body has stale csrfmiddlewaretoken, Django rejects EVEN IF HTTP_X_CSRFTOKEN is fresh.
        Replacing POST body token with fresh token succeeds.
        """
        self.client.force_login(self.user)
        token_res = self.client.get(reverse('attendance:csrf_token'))
        fresh_token = token_res.json()['csrf_token']
        stale_token = 'invalid_stale_token_value_xyz'

        # Send fresh token in header, but stale in body
        response = self.client.post(
            reverse('attendance:check_in'),
            {
                'latitude': 23.8103,
                'longitude': 90.4125,
                'csrfmiddlewaretoken': stale_token  # Stale form token
            },
            HTTP_X_CSRFTOKEN=fresh_token,          # Fresh header token
            HTTP_ACCEPT='application/json'
        )
        self.assertEqual(response.status_code, 403, "Django must reject because body token is stale")

        # Now replace POST body token with fresh token
        response_success = self.client.post(
            reverse('attendance:check_in'),
            {
                'latitude': 23.8103,
                'longitude': 90.4125,
                'accuracy': 10,
                'csrfmiddlewaretoken': fresh_token
            },
            HTTP_X_CSRFTOKEN=fresh_token,
            HTTP_ACCEPT='application/json'
        )
        self.assertEqual(response_success.status_code, 200)
        self.assertTrue(response_success.json()['success'])

    def test_photo_retention_and_idempotency_on_retry(self):
        """Photo and sync_uuid are preserved and do not produce duplicate records upon retry."""
        self.client.force_login(self.user)
        token_res = self.client.get(reverse('attendance:csrf_token'))
        fresh_token = token_res.json()['csrf_token']
        photo = create_test_image()
        sync_uuid = str(uuid.uuid4())

        # First attempt with stale token
        failed_res = self.client.post(
            reverse('attendance:check_in'),
            {
                'latitude': 23.8103,
                'longitude': 90.4125,
                'accuracy': 12,
                'sync_uuid': sync_uuid,
                'photo': photo,
                'csrfmiddlewaretoken': 'stale_token'
            },
            HTTP_ACCEPT='application/json'
        )
        self.assertEqual(failed_res.status_code, 403)
        self.assertFalse(Attendance.objects.filter(sync_uuid=sync_uuid).exists())

        # Retry with fresh token and original photo & sync_uuid
        photo.seek(0)
        retry_res = self.client.post(
            reverse('attendance:check_in'),
            {
                'latitude': 23.8103,
                'longitude': 90.4125,
                'accuracy': 12,
                'sync_uuid': sync_uuid,
                'photo': photo,
                'csrfmiddlewaretoken': fresh_token
            },
            HTTP_X_CSRFTOKEN=fresh_token,
            HTTP_ACCEPT='application/json'
        )
        self.assertEqual(retry_res.status_code, 200)
        self.assertTrue(retry_res.json()['success'])

        att = Attendance.objects.get(sync_uuid=sync_uuid)
        self.assertIsNotNone(att.photo)

        # Idempotent second call with same sync_uuid does not duplicate
        photo.seek(0)
        dup_res = self.client.post(
            reverse('attendance:check_in'),
            {
                'latitude': 23.8103,
                'longitude': 90.4125,
                'sync_uuid': sync_uuid,
                'photo': photo,
                'csrfmiddlewaretoken': fresh_token
            },
            HTTP_X_CSRFTOKEN=fresh_token,
            HTTP_ACCEPT='application/json'
        )
        self.assertEqual(dup_res.status_code, 200)
        self.assertEqual(Attendance.objects.filter(sync_uuid=sync_uuid).count(), 1)

    def test_offline_bulk_sync_csrf_enforcement(self):
        """Bulk sync endpoint enforces CSRF checks and accepts fresh token."""
        self.client.force_login(self.user)
        sync_uuid = str(uuid.uuid4())
        payload = {
            'items': [{
                'uuid': sync_uuid,
                'action': 'check_in',
                'payload': {
                    'latitude': 23.8103,
                    'longitude': 90.4125,
                    'accuracy': 10
                }
            }]
        }

        # Without CSRF token -> 403
        no_csrf_res = self.client.post(
            reverse('attendance:bulk_sync'),
            json.dumps(payload),
            content_type='application/json',
            HTTP_ACCEPT='application/json'
        )
        self.assertEqual(no_csrf_res.status_code, 403)
        self.assertEqual(no_csrf_res.json()['code'], 'CSRF_FAILURE')

        # With fresh CSRF token -> 200
        token_res = self.client.get(reverse('attendance:csrf_token'))
        fresh_token = token_res.json()['csrf_token']

        with_csrf_res = self.client.post(
            reverse('attendance:bulk_sync'),
            json.dumps(payload),
            content_type='application/json',
            HTTP_X_CSRFTOKEN=fresh_token,
            HTTP_ACCEPT='application/json'
        )
        self.assertEqual(with_csrf_res.status_code, 200)
        self.assertEqual(with_csrf_res.json()['synced'], 1)
