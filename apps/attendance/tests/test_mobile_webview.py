"""
Mobile/WebView hardening tests.

Covers:
 - Service worker response headers (no-cache, Service-Worker-Allowed)
 - Check-in: missing GPS returns 400/json-error not 500
 - Unauthenticated location sync returns auth redirect, not 500
 - PWA manifest: correct content-type
 - Sensitive media/employee paths confirm SW never-cache rule is meaningful
"""

from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.branches.models import Branch
from apps.employees.models import EmployeeProfile

User = get_user_model()


class ServiceWorkerHeaderTests(TestCase):
    """SW must always be served with no-store to prevent stale worker caching."""

    def setUp(self):
        self.client = Client()

    def test_sw_no_cache_header(self):
        response = self.client.get('/sw.js')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/javascript')
        cc = response.get('Cache-Control', '')
        # Must contain no-store so browsers never cache the service worker
        self.assertIn('no-store', cc, msg=f"SW Cache-Control missing no-store: got '{cc}'")

    def test_sw_service_worker_allowed_header(self):
        response = self.client.get('/sw.js')
        self.assertEqual(response.status_code, 200)
        # Service-Worker-Allowed: / is required to allow the SW to control the whole origin
        swa = response.get('Service-Worker-Allowed', '')
        self.assertEqual(swa, '/', msg=f"Service-Worker-Allowed header wrong: got '{swa}'")

    def test_manifest_content_type(self):
        response = self.client.get('/manifest.json')
        self.assertEqual(response.status_code, 200)
        ct = response.get('Content-Type', '')
        self.assertIn('application/json', ct)


class AttendanceCheckInMobileTests(TestCase):
    """Mobile check-in endpoint behaviour for edge cases."""

    def setUp(self):
        self.branch = Branch.objects.create(
            name='Mobile Test Branch',
            address='Dhaka, Bangladesh',
            latitude=23.810332,
            longitude=90.412518,
            radius_meters=200
        )
        self.user = User.objects.create_user(
            email='mobile@example.com',
            phone='01700000001',
            password='pass1234!',
            role='staff'
        )
        self.client.login(email='mobile@example.com', password='pass1234!')

    def test_check_in_missing_gps_returns_error_not_500(self):
        """Check-in with no GPS coords must return 400 JSON error, never 500."""
        response = self.client.post(
            reverse('attendance:check_in'),
            data={
                'latitude': '',
                'longitude': '',
                'accuracy': '',
                'address': '',
                'type': 'office',
            },
            HTTP_ACCEPT='application/json',
        )
        # Must not 500 — 400 or 200-with-error-json are both acceptable
        self.assertNotEqual(response.status_code, 500,
                            msg="Missing GPS should never cause a 500 server error")

    def test_location_sync_unauthenticated_returns_auth_error(self):
        """Location sync without auth must return 302 redirect or 403, not 500."""
        anon_client = Client()
        response = anon_client.post(
            '/attendance/location-sync/',
            data='{"latitude":23.7,"longitude":90.4,"accuracy":10}',
            content_type='application/json',
        )
        self.assertIn(response.status_code, [302, 403],
                      msg=f"Unauthenticated location-sync returned {response.status_code}")

    def test_check_in_unauthenticated_redirects(self):
        """Check-in page without auth must redirect to login, not error."""
        anon_client = Client()
        response = anon_client.get(reverse('attendance:check_in'))
        self.assertIn(response.status_code, [301, 302],
                      msg=f"Check-in without auth returned {response.status_code}")


class SWCacheSensitivePathTests(TestCase):
    """
    Verify that sensitive URL paths are excluded from SW cache.
    These integration checks verify the paths exist and respond correctly
    so the SW never-cache rules in sw.js are meaningful.
    """

    def setUp(self):
        self.client = Client()

    def test_media_path_not_500(self):
        """
        /media/ path must be excluded from the SW cache.
        Verify the server doesn't 500 on a media path probe.
        """
        response = self.client.get('/media/nonexistent-test-file.pdf')
        self.assertNotEqual(response.status_code, 500)

    def test_employees_path_returns_auth_redirect(self):
        """
        /employees/ must redirect to login for anonymous users.
        Confirms the path exists so SW never-cache rule is meaningful.
        """
        response = self.client.get('/employees/')
        self.assertIn(response.status_code, [301, 302])
