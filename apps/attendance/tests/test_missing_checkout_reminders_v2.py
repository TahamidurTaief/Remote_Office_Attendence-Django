import decimal
import json
from datetime import datetime, time, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client, TestCase
from django.urls import reverse

from apps.attendance.models import Attendance, AttendancePolicy
from apps.attendance.reminder_service import evaluate_and_send_missing_checkout_reminder
from apps.branches.models import Branch, OfficeSchedule
from apps.employees.models import Employee, EmployeeProfile
from apps.notifications.models import Notification, WebPushSubscription

User = get_user_model()
BD_TZ = ZoneInfo("Asia/Dhaka")


class MissingCheckoutReminderIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch = Branch.objects.create(
            name="Rem HQ",
            latitude=decimal.Decimal("23.810300"),
            longitude=decimal.Decimal("90.412500"),
            radius_meters=200,
        )
        cls.schedule, _ = OfficeSchedule.objects.update_or_create(
            branch=cls.branch,
            defaults={
                "office_start_time": time(9, 0),
                "office_end_time": time(18, 0),
                "late_after_minutes": 15,
            },
        )
        cls.policy = AttendancePolicy.objects.create(
            branch=cls.branch,
            gps_required="required",
            photo_required=False,
        )
        cls.user = User.objects.create_user(
            email="rem_staff@example.com",
            password="password123",
            role="staff",
        )
        cls.master = Employee.objects.create(
            user=cls.user,
            employee_number="EMP-REM-01",
            first_name="Rem",
            last_name="Staff",
            joined_date=datetime(2026, 10, 1, tzinfo=BD_TZ).date(),
        )
        cls.emp = EmployeeProfile.objects.create(
            user=cls.user,
            master_employee=cls.master,
            employee_id="EMP-REM-01",
            full_name="Rem Staff",
            phone="01766666666",
            branch=cls.branch,
            is_active=True,
            joined_date=datetime(2026, 10, 1, tzinfo=BD_TZ).date(),
        )
        cls.other_user = User.objects.create_user(
            email="rem_other@example.com",
            password="password123",
            role="staff",
        )
        cls.other_master = Employee.objects.create(
            user=cls.other_user,
            employee_number="EMP-REM-02",
            first_name="Other",
            last_name="Staff",
            joined_date=datetime(2026, 10, 1, tzinfo=BD_TZ).date(),
        )
        cls.other_emp = EmployeeProfile.objects.create(
            user=cls.other_user,
            master_employee=cls.other_master,
            employee_id="EMP-REM-02",
            full_name="Other Staff",
            phone="01766666667",
            branch=cls.branch,
            is_active=True,
            joined_date=datetime(2026, 10, 1, tzinfo=BD_TZ).date(),
        )
        from apps.tenants.models import Tenant
        tenant = Tenant.objects.first() or Tenant.objects.create(name="Default", slug="default")
        cls.sub = WebPushSubscription.objects.create(
            tenant=tenant,
            user=cls.user,
            endpoint="https://push.example.com/sub/rem1",
            endpoint_hash="hash-rem-v2-1",
            p256dh="p256dh_key",
            auth="auth_key",
            is_active=True,
        )

    def test_1_immutable_snapshot_respected_and_unsnapshotted_legacy_never_expired(self):
        """Snapshotted deadline survives schedule edits; unsnapshotted legacy session never claims expired."""
        # Unsnapshotted legacy session from 2 days ago
        legacy_att = Attendance.objects.create(
            employee=self.emp,
            date=datetime(2026, 11, 23).date(),
            check_in_time=datetime(2026, 11, 23, 9, 0, 0, tzinfo=BD_TZ),
            attendance_type="check_in",
            type="office",
        )
        now = datetime(2026, 11, 25, 18, 20, 0, tzinfo=BD_TZ)
        with patch("apps.attendance.reminder_service.send_web_push", return_value=True):
            res_legacy = evaluate_and_send_missing_checkout_reminder(self.emp, now=now)
        self.assertTrue(res_legacy["sent"])
        self.assertEqual(res_legacy["action"], "checkout")

        # Snapshotted session with 18:00 end and 09:00 next-day deadline, even after schedule edit
        snap_att = Attendance.objects.create(
            employee=self.emp,
            date=datetime(2026, 11, 26).date(),
            check_in_time=datetime(2026, 11, 26, 9, 0, 0, tzinfo=BD_TZ),
            attendance_type="check_in",
            type="office",
            shift_date=datetime(2026, 11, 26).date(),
            shift_start_time=time(9, 0),
            shift_end_time=time(18, 0),
            checkout_deadline_at=datetime(2026, 11, 27, 9, 0, 0, tzinfo=BD_TZ),
            shift_policy_version=1,
        )
        self.schedule.office_start_time = time(10, 0)
        self.schedule.office_end_time = time(20, 0)
        self.schedule.save()

        # At 18:15 on 2026-11-26 (15m after immutable 18:00, before edited 20:15) -> reminder fires
        with patch("apps.attendance.reminder_service.send_web_push", return_value=True):
            res_snap = evaluate_and_send_missing_checkout_reminder(
                self.emp, now=datetime(2026, 11, 26, 18, 15, 0, tzinfo=BD_TZ)
            )
        self.assertTrue(res_snap["sent"])
        self.assertIn(snap_att.id, res_snap["session_ids"])
        self.assertEqual(res_snap["action"], "checkout")

        # At 09:00 on 2026-11-27 (immutable deadline, before edited 10:00) -> correction action
        with patch("apps.attendance.reminder_service.send_web_push", return_value=True):
            res_exp = evaluate_and_send_missing_checkout_reminder(
                self.emp, now=datetime(2026, 11, 27, 9, 0, 0, tzinfo=BD_TZ)
            )
        self.assertTrue(res_exp["sent"])
        self.assertEqual(res_exp["action"], "correction")

    def test_2_stable_anchor_prevents_early_repeat_when_open_session_set_changes(self):
        """Adding a newly eligible session mid-window never resets the 60-minute repeat anchor."""
        s1 = Attendance.objects.create(
            employee=self.emp,
            date=datetime(2026, 11, 24).date(),
            check_in_time=datetime(2026, 11, 24, 9, 0, 0, tzinfo=BD_TZ),
            attendance_type="check_in",
            type="office",
            shift_date=datetime(2026, 11, 24).date(),
            shift_start_time=time(9, 0),
            shift_end_time=time(18, 0),
            checkout_deadline_at=datetime(2026, 11, 25, 9, 0, 0, tzinfo=BD_TZ),
        )
        t0 = datetime(2026, 11, 25, 18, 15, 0, tzinfo=BD_TZ)
        with patch("apps.attendance.reminder_service.send_web_push", return_value=True):
            r1 = evaluate_and_send_missing_checkout_reminder(self.emp, now=t0)
        self.assertTrue(r1["sent"])

        # Another session becomes eligible at 18:15 today; evaluating at 18:30 must deduplicate
        Attendance.objects.create(
            employee=self.emp,
            date=datetime(2026, 11, 25).date(),
            check_in_time=datetime(2026, 11, 25, 9, 5, 0, tzinfo=BD_TZ),
            attendance_type="check_in",
            type="office",
            shift_date=datetime(2026, 11, 25).date(),
            shift_start_time=time(9, 0),
            shift_end_time=time(18, 0),
            checkout_deadline_at=datetime(2026, 11, 26, 9, 0, 0, tzinfo=BD_TZ),
        )
        with patch("apps.attendance.reminder_service.send_web_push", return_value=True):
            r_early = evaluate_and_send_missing_checkout_reminder(
                self.emp, now=t0 + timedelta(minutes=15)
            )
        self.assertFalse(r_early["sent"])
        self.assertEqual(r_early["reason"], "deduplicated")

    def test_3_owner_scoped_csrf_snooze_endpoint_and_ui_wiring(self):
        """CSRF-protected POST /attendance/reminders/snooze/ is owner-scoped and wired in feed/UI."""
        Attendance.objects.create(
            employee=self.emp,
            date=datetime(2026, 11, 25).date(),
            check_in_time=datetime(2026, 11, 25, 9, 0, 0, tzinfo=BD_TZ),
            attendance_type="check_in",
            type="office",
        )
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)

        # Missing CSRF token -> 403
        no_csrf = csrf_client.post(reverse("attendance:snooze_missing_checkout"))
        self.assertEqual(no_csrf.status_code, 403)

        # Fetch CSRF token via check-in page and POST snooze (attempting to spoof other_emp is ignored)
        csrf_client.get(reverse("staff:check_in"))
        csrf_token = csrf_client.cookies.get("csrftoken").value
        snooze_time = datetime(2026, 11, 25, 18, 20, 0, tzinfo=BD_TZ)
        with patch("django.utils.timezone.now", return_value=snooze_time):
            ok_resp = csrf_client.post(
                reverse("attendance:snooze_missing_checkout"),
                data=json.dumps({"employee_id": self.other_emp.id}),
                content_type="application/json",
                HTTP_X_CSRFTOKEN=csrf_token,
            )
        self.assertEqual(ok_resp.status_code, 200)
        self.assertTrue(ok_resp.json()["success"])

        # self.emp is snoozed at 18:45; other_emp is NOT snoozed
        with patch("apps.attendance.reminder_service.send_web_push", return_value=True):
            r_snoozed = evaluate_and_send_missing_checkout_reminder(
                self.emp, now=datetime(2026, 11, 25, 18, 45, 0, tzinfo=BD_TZ)
            )
        self.assertFalse(r_snoozed["sent"])
        self.assertEqual(r_snoozed["reason"], "snoozed")

        # Notification feed exposes missing reminder with snooze_url and UI poller wires banner + Sync pending
        notif = Notification.objects.create(
            recipient=self.user,
            employee=self.emp,
            title="Missing Checkout Reminder",
            message="Please check out.",
            notif_type="missing",
        )
        feed_resp = csrf_client.get(reverse("notifications:feed") + f"?after={notif.id - 1}")
        self.assertEqual(feed_resp.status_code, 200)
        items = feed_resp.json()["notifications"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["type"], "missing")
        self.assertEqual(items[0]["snooze_url"], "/attendance/reminders/snooze/")

        poller_src = (Path(settings.BASE_DIR) / "static/js/notification_poller.js").read_text(encoding="utf-8")
        self.assertIn("getReminderSuppressionState", poller_src)
        self.assertIn("Sync pending", poller_src)
        self.assertIn("ft-missing-checkout-snooze-btn", poller_src)

    def test_4_rollback_suppresses_web_push_and_failed_push_not_overclaimed(self):
        """Outer transaction rollback never dispatches Web Push; failed push reports push_sent=0."""
        Attendance.objects.create(
            employee=self.emp,
            date=datetime(2026, 11, 25).date(),
            check_in_time=datetime(2026, 11, 25, 9, 0, 0, tzinfo=BD_TZ),
            attendance_type="check_in",
            type="office",
        )
        t_alert = datetime(2026, 11, 25, 18, 15, 0, tzinfo=BD_TZ)

        class _Boom(Exception):
            pass

        with patch("apps.attendance.reminder_service.send_web_push", return_value=True) as mock_push:
            with self.assertRaises(_Boom):
                with transaction.atomic():
                    evaluate_and_send_missing_checkout_reminder(self.emp, now=t_alert)
                    raise _Boom()
            mock_push.assert_not_called()

        # Now commit with failing push -> DB records created, push_sent=0
        with patch("apps.attendance.reminder_service.send_web_push", return_value=False):
            with self.captureOnCommitCallbacks(execute=True):
                res = evaluate_and_send_missing_checkout_reminder(self.emp, now=t_alert)
        self.assertTrue(res["sent"])
        self.assertEqual(res["push_sent"], 0)
