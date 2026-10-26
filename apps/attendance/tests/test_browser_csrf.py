import os
os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"

import io
import json
import uuid
import tempfile
from urllib.parse import urlparse
from PIL import Image

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings
import django.contrib.auth
from django.contrib.auth import get_user_model
from django.urls import reverse
from playwright.sync_api import sync_playwright

from apps.employees.models import EmployeeProfile
from apps.branches.models import Branch
from apps.attendance.models import Attendance, AttendancePolicy, AttendanceLocation

User = get_user_model()


class CSRFBrowserIntegrationTests(StaticLiveServerTestCase):
    """
    Automated browser test suite using real headless Chrome & Playwright:
    1. Masked token provider without document.cookie access (HttpOnly preserved).
    2. Main form check-in with photo Blob & distinct punch UUID preserved across token rotation.
    3. Attendance control widget check-out with CSRF token rotation recovery.
    4. HTMX request configuration hook (htmx:configRequest) and error recovery.
    5. Offline reconnect bulk sync preserving photo Blobs and punch UUIDs.
    6. Offline sync missing-photo policy enforcement (remains pending in IndexedDB).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._temp_media = tempfile.TemporaryDirectory()
        cls._media_override = override_settings(
            MEDIA_ROOT=cls._temp_media.name,
            ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1']
        )
        cls._media_override.enable()

        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(
            headless=True,
            executable_path='/usr/bin/google-chrome',
            args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-gpu']
        )

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
            cls.playwright.stop()
        except Exception:
            pass
        cls._media_override.disable()
        cls._temp_media.cleanup()
        super().tearDownClass()

    def setUp(self):
        self.context = self.browser.new_context()
        self.page = self.context.new_page()

        self.user = User.objects.create_user(
            email='browser_staff@example.com',
            password='StaffPassword123!'
        )
        self.branch = Branch.objects.create(
            name='Browser Test Branch',
            address='123 Tech Way',
            latitude=23.8103,
            longitude=90.4125
        )
        self.employee = EmployeeProfile.objects.create(
            user=self.user,
            employee_id='EMP-BROWSER-001',
            full_name='Browser Test Staff',
            phone='01799999999',
            branch=self.branch,
            joined_date='2026-01-01',
            is_active=True
        )
        self.policy, _ = AttendancePolicy.objects.update_or_create(
            branch=self.branch,
            defaults={
                'photo_required': False,
                'gps_required': 'optional',
                'allow_outside_geofence': True
            }
        )

    def tearDown(self):
        try:
            self.page.close()
            self.context.close()
        except Exception:
            pass
        super().tearDown()

    def login_in_browser(self):
        """Logs into FieldTrack in browser session via authenticated session cookie."""
        from django.utils import timezone
        from apps.accounts.models import UserSession

        session = SessionStore()
        session[django.contrib.auth.SESSION_KEY] = str(self.user.pk)
        session[django.contrib.auth.BACKEND_SESSION_KEY] = 'django.contrib.auth.backends.ModelBackend'
        session[django.contrib.auth.HASH_SESSION_KEY] = self.user.get_session_auth_hash()
        session.save()

        UserSession.objects.filter(user=self.user).delete()
        UserSession.objects.create(
            user=self.user,
            session_key=session.session_key,
            device_id='browser_test_device',
            is_active=True,
            last_activity=timezone.now()
        )
        self.user.active_session_key = session.session_key
        self.user.save(update_fields=['active_session_key'])

        netloc = urlparse(self.live_server_url)
        self.context.add_cookies([{
            'name': 'sessionid',
            'value': session.session_key,
            'domain': netloc.hostname,
            'path': '/',
            'httpOnly': True,
            'secure': False,
            'sameSite': 'Lax'
        }])

    def test_browser_httponly_and_csrf_provider(self):
        """
        Verify that document.cookie does not contain csrftoken (HttpOnly),
        and window.CSRFProvider provides a valid masked token backed by get_token().
        """
        self.login_in_browser()
        self.page.goto(f"{self.live_server_url}/staff/check-in/")
        self.page.wait_for_load_state('networkidle')

        # 1. HttpOnly Check: document.cookie must not expose csrftoken
        cookies_in_dom = self.page.evaluate("document.cookie")
        self.assertNotIn('csrftoken=', cookies_in_dom, "HttpOnly CSRF cookie must NEVER be visible in document.cookie")

        # 2. CSRFProvider must be defined
        has_provider = self.page.evaluate("typeof window.CSRFProvider !== 'undefined'")
        self.assertTrue(has_provider, "window.CSRFProvider must be globally available")

        # 3. Masked token retrieval from provider
        token = self.page.evaluate("window.CSRFProvider.getToken()")
        self.assertTrue(isinstance(token, str) and len(token) == 64, f"Masked token must be 64-char string, got {token}")

        # 4. Token refresh endpoint call from browser
        refresh_result = self.page.evaluate("""
            async () => {
                return await window.CSRFProvider.refreshToken();
            }
        """)
        self.assertTrue(refresh_result.get('success'), f"Token refresh must succeed: {refresh_result}")
        self.assertEqual(len(refresh_result.get('token', '')), 64)

    def test_browser_main_form_photo_and_uuid_preservation(self):
        """
        Main form check-in submission in real browser:
        - Uploads a real photo Blob and distinct punch UUID.
        - Stale CSRF token triggers 403 CSRF failure on initial attempt.
        - window.CSRFProvider.fetchWithCsrf catches 403, calls /attendance/csrf-token/,
          refreshes the token, and retries the submission.
        - Verifies the photo Blob and original punch UUID are preserved intact.
        - Verifies the database record is created with the exact sync_uuid and decodable photo.
        """
        self.policy.photo_required = True
        self.policy.save()

        self.login_in_browser()
        self.page.goto(f"{self.live_server_url}/staff/check-in/")
        self.page.wait_for_load_state('networkidle')

        punch_uuid = str(uuid.uuid4())

        # Submit check-in with a dynamically generated JPEG Blob and stale CSRF token
        submit_result = self.page.evaluate(f"""
            async () => {{
                // 1. Create a real 100x100 JPEG blob in canvas
                const canvas = document.createElement('canvas');
                canvas.width = 100;
                canvas.height = 100;
                const ctx = canvas.getContext('2d');
                ctx.fillStyle = '#10B981';
                ctx.fillRect(0, 0, 100, 100);

                const photoBlob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.9));

                // 2. Corrupt token to trigger 403 CSRF failure
                window.CSRFProvider.setToken('STALE_CORRUPTED_TOKEN_BEFORE_ROTATION_123456789012345678901234');

                // 3. Prepare FormData with photo and sync_uuid
                const formData = new FormData();
                formData.append('latitude', '23.8103');
                formData.append('longitude', '90.4125');
                formData.append('accuracy', '15');
                formData.append('sync_uuid', '{punch_uuid}');
                formData.append('photo', photoBlob, 'selfie.jpg');
                formData.append('csrfmiddlewaretoken', 'STALE_CORRUPTED_TOKEN_BEFORE_ROTATION_123456789012345678901234');

                // 4. Dispatch through resilient fetchWithCsrf
                const response = await window.CSRFProvider.fetchWithCsrf('/attendance/check-in/', {{
                    method: 'POST',
                    body: formData,
                    credentials: 'same-origin'
                }});

                return {{
                    status: response.status,
                    data: await response.json()
                }};
            }}
        """)

        self.assertEqual(submit_result['status'], 200, f"Expected 200 after auto-refresh, got: {submit_result}")
        self.assertTrue(submit_result['data'].get('success'), f"Expected success: true, got: {submit_result}")

        # 5. Verify database integrity: record exists with exact sync_uuid and decodable photo
        record = Attendance.objects.get(sync_uuid=punch_uuid)
        self.assertEqual(record.employee, self.employee)
        self.assertEqual(record.attendance_type, 'check_in')
        self.assertTrue(record.photo, "Check-in photo must be saved")
        self.assertTrue(os.path.exists(record.photo.path), "Photo file must exist on disk")

        with Image.open(record.photo.path) as img:
            self.assertGreater(img.size[0], 0)
            self.assertGreater(img.size[1], 0)

    def test_browser_widget_checkout_rotation_recovery(self):
        """
        Attendance control centre widget check-out in browser:
        - Employee has an active check-in session.
        - Check-out submitted via CSRFProvider.fetchWithCsrf with stale CSRF token.
        - Token rotation is caught and recovered; checkout succeeds.
        - Session is marked closed and checkout location record is created.
        """
        # Create active check-in session
        ci_record = Attendance.objects.create(
            employee=self.employee,
            date=django.utils.timezone.localdate(),
            check_in_time=django.utils.timezone.now() - django.utils.timezone.timedelta(hours=2),
            attendance_type='check_in',
            sync_uuid=uuid.uuid4()
        )

        self.login_in_browser()
        self.page.goto(f"{self.live_server_url}/staff/check-in/")
        self.page.wait_for_load_state('networkidle')

        checkout_uuid = str(uuid.uuid4())

        checkout_result = self.page.evaluate(f"""
            async () => {{
                // Corrupt in-memory CSRF token
                window.CSRFProvider.setToken('STALE_WIDGET_TOKEN_ROTATION_12345678901234567890123456789012');

                const fd = new FormData();
                fd.append('latitude', '23.8103');
                fd.append('longitude', '90.4125');
                fd.append('accuracy', '10');
                fd.append('sync_uuid', '{checkout_uuid}');
                fd.append('address', 'Tech Park Checkout');
                fd.append('csrfmiddlewaretoken', 'STALE_WIDGET_TOKEN_ROTATION_12345678901234567890123456789012');

                const response = await window.CSRFProvider.fetchWithCsrf('/attendance/check-out/', {{
                    method: 'POST',
                    credentials: 'same-origin',
                    body: fd
                }});

                return {{
                    status: response.status,
                    data: await response.json()
                }};
            }}
        """)

        self.assertEqual(checkout_result['status'], 200, f"Expected 200 after recovery, got: {checkout_result}")
        self.assertTrue(checkout_result['data'].get('success'), f"Expected success: true, got: {checkout_result}")

        # Verify active session is closed in database
        ci_record.refresh_from_db()
        self.assertIsNotNone(ci_record.check_out_time, "Check-out time must be populated")
        co_loc = AttendanceLocation.objects.get(sync_uuid=checkout_uuid, event='check_out')
        self.assertEqual(co_loc.attendance, ci_record)

    def test_browser_htmx_config_and_recovery(self):
        """
        Verify that HTMX requests receive X-CSRFToken and credentials via configRequest,
        and htmx:responseError triggers token refresh on 403 CSRF failure.
        """
        self.login_in_browser()
        self.page.goto(f"{self.live_server_url}/staff/check-in/")
        self.page.wait_for_load_state('networkidle')

        # 1. Trigger custom htmx:configRequest event to verify headers & parameter injection
        event_detail = self.page.evaluate("""
            () => {
                const detail = {
                    headers: {},
                    parameters: {}
                };
                const evt = new CustomEvent('htmx:configRequest', { detail: detail, bubbles: true });
                document.body.dispatchEvent(evt);
                return detail;
            }
        """)
        self.assertIn('X-CSRFToken', event_detail['headers'])
        self.assertEqual(len(event_detail['headers']['X-CSRFToken']), 64)
        self.assertTrue(event_detail.get('withCredentials'))
        self.assertIn('csrfmiddlewaretoken', event_detail['parameters'])
        self.assertEqual(len(event_detail['parameters']['csrfmiddlewaretoken']), 64)

        # 2. Trigger htmx:responseError with CSRF failure and verify token refresh is triggered
        recovery_detail = self.page.evaluate("""
            async () => {
                window.CSRFProvider.setToken('STALE_HTMX_TOKEN_BEFORE_RECOVERY_123456789012345678901234567890');
                const xhrMock = {
                    status: 403,
                    responseText: '{"code": "CSRF_FAILURE", "error": "CSRF verification failed"}'
                };
                const errEvt = new CustomEvent('htmx:responseError', {
                    detail: { xhr: xhrMock },
                    bubbles: true
                });
                document.body.dispatchEvent(errEvt);

                // Give event listener a moment to call refreshToken()
                await new Promise(r => setTimeout(r, 1000));
                return window.CSRFProvider.getToken();
            }
        """)
        self.assertEqual(len(recovery_detail), 64)
        self.assertNotEqual(recovery_detail, 'STALE_HTMX_TOKEN_BEFORE_RECOVERY_123456789012345678901234567890')

    def test_browser_offline_reconnect_sync_with_photo_and_uuid(self):
        """
        Offline reconnect sync in real browser:
        - Queues offline check-in with a photo base64 data URI and unique punch UUID in IndexedDB (fieldtrack_db).
        - Triggers SyncEngine.processQueue() simulating network reconnect.
        - Verifies SyncEngine dispatches to /attendance/bulk-sync/ using window.CSRFProvider.
        - Verifies bulk-sync decodes the photo, enforces photo policy, and saves Attendance with exact sync_uuid.
        - Verifies IndexedDB sync item is marked 'synced'.
        """
        self.policy.photo_required = True
        self.policy.save()

        self.login_in_browser()
        self.page.goto(f"{self.live_server_url}/staff/check-in/")
        self.page.wait_for_load_state('networkidle')

        offline_uuid = str(uuid.uuid4())

        sync_result = self.page.evaluate(f"""
            async () => {{
                // 1. Generate base64 photo Data URL in canvas
                const canvas = document.createElement('canvas');
                canvas.width = 120;
                canvas.height = 120;
                const ctx = canvas.getContext('2d');
                ctx.fillStyle = '#2563EB';
                ctx.fillRect(0, 0, 120, 120);
                const photoDataUrl = canvas.toDataURL('image/jpeg', 0.85);

                // 2. Queue offline punch with photo into IndexedDB
                await window.FieldTrackDB.enqueueSyncItem('attendance', 'check_in', {{
                    latitude: 23.8103,
                    longitude: 90.4125,
                    accuracy: 12,
                    sync_uuid: '{offline_uuid}',
                    client_event_time: new Date().toISOString(),
                    photo: photoDataUrl
                }}, 1, '{offline_uuid}');

                // 3. Verify item is initially pending
                const pendingBefore = await window.FieldTrackDB.getPendingSyncItems();
                const queuedItem = pendingBefore.find(i => i.uuid === '{offline_uuid}');
                if (!queuedItem) return {{ success: false, error: 'Item not queued in IndexedDB' }};

                // 4. Trigger queue processing
                await window.SyncEngine.processQueue();

                // 5. Check item status in IndexedDB
                const itemAfter = await window.FieldTrackDB.getSyncItem('{offline_uuid}');
                return {{
                    success: true,
                    statusAfter: itemAfter ? itemAfter.status : 'removed_as_completed'
                }};
            }}
        """)

        self.assertTrue(sync_result['success'], f"Sync script failed: {sync_result}")
        self.assertIn(sync_result['statusAfter'], ['synced', 'removed_as_completed'])

        # 6. Verify Attendance record created in DB with decoded photo and exact sync_uuid
        att = Attendance.objects.get(sync_uuid=offline_uuid)
        self.assertEqual(att.employee, self.employee)
        self.assertEqual(att.attendance_type, 'check_in')
        self.assertTrue(att.photo, "Photo must be attached to attendance record")
        self.assertTrue(os.path.exists(att.photo.path), "Photo file must exist on disk")

        with Image.open(att.photo.path) as img:
            self.assertGreater(img.size[0], 0)
            self.assertGreater(img.size[1], 0)

    def test_browser_offline_sync_missing_photo_remains_pending(self):
        """
        Missing required photo in offline sync:
        - When photo_required=True, queuing a punch WITHOUT a photo must NOT be marked synced.
        - After bulk-sync execution, the item must remain status='pending' in IndexedDB
          with a clear error message that offline punch remains pending until photo is attached.
        - No Attendance record is created.
        """
        self.policy.photo_required = True
        self.policy.save()

        self.login_in_browser()
        self.page.goto(f"{self.live_server_url}/staff/check-in/")
        self.page.wait_for_load_state('networkidle')

        missing_photo_uuid = str(uuid.uuid4())

        res = self.page.evaluate(f"""
            async () => {{
                // 1. Queue offline punch WITHOUT photo
                await window.FieldTrackDB.enqueueSyncItem('attendance', 'check_in', {{
                    latitude: 23.8103,
                    longitude: 90.4125,
                    accuracy: 12,
                    sync_uuid: '{missing_photo_uuid}',
                    client_event_time: new Date().toISOString(),
                    photo: null
                }}, 1, '{missing_photo_uuid}');

                // 2. Trigger sync
                await window.SyncEngine.processQueue();

                // 3. Inspect item in IndexedDB
                const item = await window.FieldTrackDB.getSyncItem('{missing_photo_uuid}');
                return {{
                    status: item ? item.status : null,
                    error: item ? item.last_error : null
                }};
            }}
        """)

        self.assertEqual(res['status'], 'pending', f"Missing required photo must remain pending: {res}")
        self.assertIn('photo is required', res['error'].lower())

        # Verify no Attendance record created
        self.assertFalse(
            Attendance.objects.filter(sync_uuid=missing_photo_uuid).exists(),
            "No attendance record should be created when required photo is missing"
        )
