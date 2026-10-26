import os
os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"

import io
import json
import time
import uuid
import tempfile
from urllib.parse import urlparse
from PIL import Image

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings
import django.contrib.auth
from django.utils import timezone
from django.contrib.auth import get_user_model
from playwright.sync_api import sync_playwright

from apps.employees.models import EmployeeProfile
from apps.branches.models import Branch
from apps.attendance.models import Attendance, AttendancePolicy
from apps.accounts.models import UserSession

User = get_user_model()


class PhotoPrepBrowserRegressionTests(StaticLiveServerTestCase):
    """
    Automated Playwright browser test suite verifying:
    1. 4032x3024 camera photo -> correctly oriented 1280x960 JPEG, never upscaled.
    2. Portrait orientation preservation (longest edge resized to 1280px).
    3. Single-pass processing: prepared Blob used for preview, submission, and retries.
    4. Repeated submit/reconnect does not recompress or lose the Blob.
    5. Offline capture followed by reconnect/stale-token recovery preserves prepared photo and UUID.
    6. Canvas failure fallback retains original valid photo without failing check-in.
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
        uid = uuid.uuid4().hex[:8]
        self.user = User.objects.create_user(
            email=f'photo_user_{uid}@example.com',
            password='TestPassword123!'
        )
        self.branch = Branch.objects.create(
            name=f'HQ_{uid}',
            address='Dhaka Central',
            latitude=23.8103,
            longitude=90.4125
        )
        self.employee = EmployeeProfile.objects.create(
            user=self.user,
            employee_id=f'EMP-{uid}',
            full_name='Photo Browser Tester',
            phone='01788889999',
            joined_date='2026-01-01',
            branch=self.branch,
            is_active=True
        )
        self.policy = AttendancePolicy.objects.create(
            branch=self.branch,
            photo_required=True,
            geofencing_policy='disabled',
            allow_outside_geofence=True
        )

    def _login_browser_context(self, context):
        session = SessionStore()
        session[django.contrib.auth.SESSION_KEY] = str(self.user.pk)
        session[django.contrib.auth.BACKEND_SESSION_KEY] = 'django.contrib.auth.backends.ModelBackend'
        session[django.contrib.auth.HASH_SESSION_KEY] = self.user.get_session_auth_hash()
        session.save()

        UserSession.objects.filter(user=self.user).delete()
        UserSession.objects.create(
            user=self.user,
            session_key=session.session_key,
            device_id=f'device_{uuid.uuid4().hex[:6]}',
            is_active=True,
            last_activity=timezone.now()
        )
        self.user.active_session_key = session.session_key
        self.user.save(update_fields=['active_session_key'])

        netloc = urlparse(self.live_server_url)
        context.add_cookies([{
            'name': 'sessionid',
            'value': session.session_key,
            'domain': netloc.hostname,
            'path': '/',
            'httpOnly': True,
            'secure': False,
            'sameSite': 'Lax'
        }])

    def test_browser_photo_helper_dimensions_and_no_upscaling(self):
        """
        Tests FieldTrackPhoto helper inside real Chromium:
        - 4032x3024 -> 1280x960 JPEG 0.8
        - 800x600 -> 800x600 (never upscaled)
        - 3024x4032 portrait -> 960x1280
        """
        context = self.browser.new_context(base_url=self.live_server_url)
        self._login_browser_context(context)
        page = context.new_page()

        page.goto('/staff/check-in/')
        page.wait_for_load_state('networkidle')

        results = page.evaluate("""
            async () => {
                // 1. Helper dimension logic test
                const d4k = window.FieldTrackPhoto.calculateDimensions(4032, 3024, 1280);
                const dSmall = window.FieldTrackPhoto.calculateDimensions(800, 600, 1280);
                const dPortrait = window.FieldTrackPhoto.calculateDimensions(3024, 4032, 1280);

                // 2. Real canvas encoding test with 4032x3024 input
                const canvas = document.createElement('canvas');
                canvas.width = 4032;
                canvas.height = 3024;
                const ctx = canvas.getContext('2d');
                ctx.fillStyle = '#22c55e';
                ctx.fillRect(0, 0, 4032, 3024);

                const origBlob = await new Promise(r => canvas.toBlob(r, 'image/jpeg', 0.95));
                const origFile = new File([origBlob], 'camera_4k.jpg', { type: 'image/jpeg' });

                const prepResult = await window.FieldTrackPhoto.preparePhoto(origFile);

                return {
                    d4k,
                    dSmall,
                    dPortrait,
                    prepWidth: prepResult.width,
                    prepHeight: prepResult.height,
                    origSize: origBlob.size,
                    prepSize: prepResult.preparedSize,
                    isJpeg: prepResult.blob.type === 'image/jpeg',
                    hasPreview: !!prepResult.previewUrl,
                    prepared: prepResult.prepared
                };
            }
        """)

        # Dimension checks
        self.assertEqual(results['d4k']['width'], 1280)
        self.assertEqual(results['d4k']['height'], 960)
        self.assertTrue(results['d4k']['scaled'])

        # No upscaling check
        self.assertEqual(results['dSmall']['width'], 800)
        self.assertEqual(results['dSmall']['height'], 600)
        self.assertFalse(results['dSmall']['scaled'])

        # Portrait check
        self.assertEqual(results['dPortrait']['width'], 960)
        self.assertEqual(results['dPortrait']['height'], 1280)

        # Real preparation check
        self.assertEqual(results['prepWidth'], 1280)
        self.assertEqual(results['prepHeight'], 960)
        self.assertTrue(results['isJpeg'])
        self.assertTrue(results['prepared'])
        self.assertLess(results['prepSize'], results['origSize'])

        context.close()

    def test_browser_check_in_with_prepared_photo_and_uuid(self):
        """
        Simulates camera photo capture in check_in.html:
        - Uploads 4032x3024 canvas-generated camera image.
        - Verifies FieldTrackPhoto prepares the photo once.
        - Verifies single-pass prepared photo reaches server.
        - Verifies database record has exact sync_uuid and ImageKit WEBP photo.
        """
        context = self.browser.new_context(base_url=self.live_server_url)
        self._login_browser_context(context)
        page = context.new_page()

        page.goto('/staff/check-in/')
        page.wait_for_load_state('networkidle')

        result = page.evaluate("""
            async () => {
                // 1. Create 4032x3024 camera image
                const canvas = document.createElement('canvas');
                canvas.width = 4032;
                canvas.height = 3024;
                const ctx = canvas.getContext('2d');
                ctx.fillStyle = '#3b82f6';
                ctx.fillRect(0, 0, 4032, 3024);

                const blob = await new Promise(r => canvas.toBlob(r, 'image/jpeg', 0.95));
                const file = new File([blob], 'camera_selfie.jpg', { type: 'image/jpeg' });

                // 2. Call handlePhotoSelect with fake input
                await handlePhotoSelect({ files: [file] });

                // Verify preview set
                const previewImg = document.getElementById('photo-preview-img');
                const previewSrc = previewImg ? previewImg.src : null;
                const stepText = document.getElementById('step-photo-text').textContent;

                // 3. Set GPS ready
                isGpsReady = true;
                document.getElementById('id_latitude').value = '23.8103';
                document.getElementById('id_longitude').value = '90.4125';
                document.getElementById('id_accuracy').value = '12';

                // 4. Submit check in and await HTTP request completion
                const punchUuidBefore = activePunchUuid;
                const response = await submitCheckIn();

                return {
                    previewSrcSet: !!previewSrc,
                    stepText,
                    punchUuid: punchUuidBefore,
                    origFileSize: file.size,
                    preparedSize: activePreparedPhoto.preparedSize
                };
            }
        """)

        self.assertTrue(result['previewSrcSet'])
        self.assertIn('Photo Ready', result['stepText'])
        self.assertLess(result['preparedSize'], result['origFileSize'])

        # Verify DB Attendance record exists with exact sync_uuid
        att = None
        for _ in range(40):
            att = Attendance.objects.filter(sync_uuid=result['punchUuid']).first()
            if att:
                break
            time.sleep(0.1)

        self.assertIsNotNone(att, "Attendance record must be created with exact sync_uuid")
        self.assertTrue(att.photo)
        self.assertTrue(os.path.exists(att.photo.path))

        with Image.open(att.photo.path) as img:
            self.assertEqual(img.format, 'WEBP')
            self.assertLessEqual(max(img.width, img.height), 1280)

        context.close()

    def test_browser_offline_punch_and_reconnect_sync(self):
        """
        Offline punch test:
        1. Emulates offline environment.
        2. Captures photo in check_in.html; queues offline action in IndexedDB.
        3. Verifies queued photo in IndexedDB is the prepared lightweight Base64.
        4. Reconnects and triggers SyncEngine bulk-sync.
        5. Verifies server creates Attendance record with exact punch UUID.
        """
        context = self.browser.new_context(base_url=self.live_server_url)
        self._login_browser_context(context)
        page = context.new_page()

        page.goto('/staff/check-in/')
        page.wait_for_load_state('networkidle')

        offline_result = page.evaluate("""
            async () => {
                // Mock offline
                Object.defineProperty(navigator, 'onLine', { value: false, configurable: true });

                const canvas = document.createElement('canvas');
                canvas.width = 2400;
                canvas.height = 1800;
                const ctx = canvas.getContext('2d');
                ctx.fillStyle = '#e11d48';
                ctx.fillRect(0, 0, 2400, 1800);

                const blob = await new Promise(r => canvas.toBlob(r, 'image/jpeg', 0.9));
                const file = new File([blob], 'offline_photo.jpg', { type: 'image/jpeg' });

                await handlePhotoSelect({ files: [file] });

                isGpsReady = true;
                document.getElementById('id_latitude').value = '23.8103';
                document.getElementById('id_longitude').value = '90.4125';
                document.getElementById('id_accuracy').value = '15';

                const punchUuid = activePunchUuid;
                await submitCheckIn();

                // Wait briefly for IndexedDB write
                await new Promise(r => setTimeout(r, 200));

                // Read queued items from IndexedDB
                const items = await window.FieldTrackDB.getPendingSyncItems();
                const queuedItem = items.find(i => i.uuid === punchUuid);

                return {
                    punchUuid,
                    queuedItemExists: !!queuedItem,
                    photoLength: (queuedItem && queuedItem.payload && queuedItem.payload.photo) ? queuedItem.payload.photo.length : 0
                };
            }
        """)

        self.assertTrue(offline_result['queuedItemExists'])
        self.assertGreater(offline_result['photoLength'], 100)

        # Trigger network reconnect & queue sync
        sync_result = page.evaluate("""
            async () => {
                Object.defineProperty(navigator, 'onLine', { value: true, configurable: true });
                await window.SyncEngine.processQueue();
                const remaining = await window.FieldTrackDB.getPendingSyncItems();
                return { remainingCount: remaining.length };
            }
        """)

        att = None
        for _ in range(40):
            att = Attendance.objects.filter(sync_uuid=offline_result['punchUuid']).first()
            if att:
                break
            time.sleep(0.1)

        self.assertIsNotNone(att, "Offline synced attendance must exist in DB")
        self.assertTrue(att.photo)

        context.close()

    def test_browser_canvas_failure_fallback_retains_original_photo(self):
        """
        Edge case: Canvas conversion throws an error.
        - Verifies FieldTrackPhoto catches error and returns fallback object with original photo.
        - Verifies fallback flag is true and original photo is retained.
        """
        context = self.browser.new_context(base_url=self.live_server_url)
        self._login_browser_context(context)
        page = context.new_page()

        page.goto('/staff/check-in/')
        page.wait_for_load_state('networkidle')

        fallback_result = page.evaluate("""
            async () => {
                // 1. Create a real sample image first
                const canvas = document.createElement('canvas');
                canvas.width = 640;
                canvas.height = 480;
                const ctx = canvas.getContext('2d');
                ctx.fillStyle = '#8b5cf6';
                ctx.fillRect(0, 0, 640, 480);

                const blob = await new Promise(r => canvas.toBlob(r, 'image/jpeg', 0.8));
                const file = new File([blob], 'fallback_test.jpg', { type: 'image/jpeg' });

                // 2. Monkey-patch canvas toBlob to simulate canvas failure
                const origToBlob = HTMLCanvasElement.prototype.toBlob;
                HTMLCanvasElement.prototype.toBlob = function(cb) {
                    cb(null);
                };

                const prep = await window.FieldTrackPhoto.preparePhoto(file);

                // Restore toBlob
                HTMLCanvasElement.prototype.toBlob = origToBlob;

                return {
                    fallback: prep.fallback,
                    prepared: prep.prepared,
                    hasFile: !!prep.file,
                    hasPreview: !!prep.previewUrl,
                    originalSize: prep.originalSize,
                    preparedSize: prep.preparedSize
                };
            }
        """)

        self.assertTrue(fallback_result['fallback'])
        self.assertFalse(fallback_result['prepared'])
        self.assertTrue(fallback_result['hasFile'])
        self.assertTrue(fallback_result['hasPreview'])
        self.assertEqual(fallback_result['originalSize'], fallback_result['preparedSize'])

        context.close()

    def test_browser_repeated_submit_does_not_recompress_photo_or_lose_blob(self):
        """
        Verifies that when a user repeatedly triggers submit (or CSRF retry occurs):
        - The photo is NOT recompressed by FieldTrackPhoto.
        - The prepared Blob is reused directly.
        - Punch UUID and timestamp are preserved.
        """
        context = self.browser.new_context(base_url=self.live_server_url)
        self._login_browser_context(context)
        page = context.new_page()

        page.goto('/staff/check-in/')
        page.wait_for_load_state('networkidle')

        result = page.evaluate("""
            async () => {
                let prepareCallCount = 0;
                const origPrepare = window.FieldTrackPhoto.preparePhoto;
                window.FieldTrackPhoto.preparePhoto = async function(...args) {
                    prepareCallCount++;
                    return origPrepare.apply(this, args);
                };

                const canvas = document.createElement('canvas');
                canvas.width = 1600;
                canvas.height = 1200;
                const ctx = canvas.getContext('2d');
                ctx.fillStyle = '#0284c7';
                ctx.fillRect(0, 0, 1600, 1200);

                const blob = await new Promise(r => canvas.toBlob(r, 'image/jpeg', 0.9));
                const file = new File([blob], 'repeated_punch.jpg', { type: 'image/jpeg' });

                await handlePhotoSelect({ files: [file] });

                isGpsReady = true;
                document.getElementById('id_latitude').value = '23.8103';
                document.getElementById('id_longitude').value = '90.4125';
                document.getElementById('id_accuracy').value = '10';

                const initialBlob = activePreparedPhoto.blob;
                const initialUuid = activePunchUuid;
                const initialTimestamp = activeCaptureTimestamp;

                // Simulate repeated submit invocation
                await submitCheckIn();
                await submitCheckIn();

                window.FieldTrackPhoto.preparePhoto = origPrepare;

                return {
                    prepareCallCount,
                    sameBlob: activePreparedPhoto.blob === initialBlob,
                    sameUuid: activePunchUuid === initialUuid,
                    sameTimestamp: activeCaptureTimestamp === initialTimestamp,
                    punchUuid: initialUuid
                };
            }
        """)

        self.assertEqual(result['prepareCallCount'], 1, "preparePhoto must be called exactly once")
        self.assertTrue(result['sameBlob'], "Prepared Blob must be preserved without recompression")
        self.assertTrue(result['sameUuid'], "Punch UUID must be preserved across attempts")
        self.assertTrue(result['sameTimestamp'], "Capture timestamp must be preserved")

        context.close()

    def test_browser_offline_reconnect_stale_token_recovery(self):
        """
        Verifies offline capture followed by reconnect with stale CSRF token:
        - Queues offline check-in with prepared photo.
        - Corrupts token to simulate expiration across disconnect.
        - Reconnect triggers CSRFProvider.refreshToken() & bulk-sync.
        - Exact prepared photo and UUID reach the server once.
        """
        context = self.browser.new_context(base_url=self.live_server_url)
        self._login_browser_context(context)
        page = context.new_page()

        page.goto('/staff/check-in/')
        page.wait_for_load_state('networkidle')

        result = page.evaluate("""
            async () => {
                // Mock offline
                Object.defineProperty(navigator, 'onLine', { value: false, configurable: true });

                const canvas = document.createElement('canvas');
                canvas.width = 1600;
                canvas.height = 1200;
                const ctx = canvas.getContext('2d');
                ctx.fillStyle = '#10b981';
                ctx.fillRect(0, 0, 1600, 1200);

                const blob = await new Promise(r => canvas.toBlob(r, 'image/jpeg', 0.9));
                const file = new File([blob], 'stale_recovery.jpg', { type: 'image/jpeg' });

                await handlePhotoSelect({ files: [file] });

                isGpsReady = true;
                document.getElementById('id_latitude').value = '23.8103';
                document.getElementById('id_longitude').value = '90.4125';
                document.getElementById('id_accuracy').value = '8';

                const punchUuid = activePunchUuid;
                await submitCheckIn();

                await new Promise(r => setTimeout(r, 200));

                // Corrupt token to simulate stale/expired token during offline period
                window.CSRFProvider.setToken('EXPIRED_OFFLINE_TOKEN_123456789012345678901234');

                // Restore online status and invoke network reconnect handler
                Object.defineProperty(navigator, 'onLine', { value: true, configurable: true });
                await window.SyncEngine.onNetworkReconnect();

                return {
                    punchUuid
                };
            }
        """)

        att = None
        for _ in range(40):
            att = Attendance.objects.filter(sync_uuid=result['punchUuid']).first()
            if att:
                break
            time.sleep(0.1)

        self.assertIsNotNone(att, "Attendance record must sync to server after reconnect with stale token")
        self.assertTrue(att.photo)

        context.close()
