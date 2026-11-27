import decimal
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.attendance.models import Attendance, AttendancePolicy
from apps.attendance.reminder_service import (
    evaluate_and_send_missing_checkout_reminder,
    snooze_missing_checkout_reminder,
)
from apps.branches.models import Branch, OfficeSchedule
from apps.employees.models import Employee, EmployeeProfile
from apps.notifications.models import Notification, WebPushSubscription
from apps.tenants.models import Tenant

User = get_user_model()
BD_TZ = ZoneInfo("Asia/Dhaka")


class MissingCheckoutReminderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch = Branch.objects.create(
            name="Reminder HQ",
            latitude=decimal.Decimal("23.810300"),
            longitude=decimal.Decimal("90.412500"),
            radius_meters=200,
        )
        OfficeSchedule.objects.update_or_create(
            branch=cls.branch,
            defaults={
                "office_start_time": datetime.strptime("09:00", "%H:%M").time(),
                "office_end_time": datetime.strptime("18:00", "%H:%M").time(),
                "late_after_minutes": 15,
            },
        )
        AttendancePolicy.objects.create(
            branch=cls.branch,
            gps_required="required",
            max_gps_accuracy_meters=100,
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
            joined_date=datetime(2025, 1, 1).date(),
        )
        cls.emp = EmployeeProfile.objects.create(
            user=cls.user,
            master_employee=cls.master,
            employee_id="EMP-REM-01",
            full_name="Rem Staff",
            phone="01744444444",
            branch=cls.branch,
            is_active=True,
            joined_date=datetime(2025, 1, 1).date(),
        )
        tenant = Tenant.objects.first() or Tenant.objects.create(name="Default", slug="default")
        cls.sub = WebPushSubscription.objects.create(
            tenant=tenant,
            user=cls.user,
            endpoint="https://push.example.com/sub/1",
            endpoint_hash="hash-rem-1",
            p256dh="key1",
            auth="auth1",
            is_active=True,
        )

    def test_1_15min_after_shift_end_and_deadline_action_switch(self):
        """18:00 shift end -> not eligible at 18:14:59; first alert at 18:15 (checkout); after 09:00 deadline -> correction."""
        ci_time = datetime(2026, 11, 25, 9, 0, 0, tzinfo=BD_TZ)
        Attendance.objects.create(
            employee=self.emp,
            date=ci_time.date(),
            check_in_time=ci_time,
            attendance_type="check_in",
            type="office",
            status="on_time",
        )

        with patch("apps.attendance.reminder_service.send_web_push", return_value=True) as mock_push:
            r_early = evaluate_and_send_missing_checkout_reminder(
                self.emp, now=datetime(2026, 11, 25, 18, 14, 59, tzinfo=BD_TZ)
            )
            self.assertFalse(r_early["sent"])
            self.assertEqual(r_early["reason"], "before_first_reminder")

            r_first = evaluate_and_send_missing_checkout_reminder(
                self.emp, now=datetime(2026, 11, 25, 18, 15, 0, tzinfo=BD_TZ)
            )
            self.assertTrue(r_first["sent"], r_first)
            self.assertEqual(r_first["action"], "checkout")
            self.assertEqual(r_first["push_sent"], 1)

            r_past_deadline = evaluate_and_send_missing_checkout_reminder(
                self.emp, now=datetime(2026, 11, 26, 9, 0, 0, tzinfo=BD_TZ)
            )
            self.assertTrue(r_past_deadline["sent"])
            self.assertEqual(r_past_deadline["action"], "correction")
            self.assertEqual(mock_push.call_count, 2)

    def test_2_worker_dedup_daily_cap_and_60min_snooze(self):
        """Two workers evaluating same window deduplicate; 60m snooze suppresses; daily cap stops at 4."""
        ci_time = datetime(2026, 11, 25, 9, 0, 0, tzinfo=BD_TZ)
        Attendance.objects.create(
            employee=self.emp,
            date=ci_time.date(),
            check_in_time=ci_time,
            attendance_type="check_in",
            type="office",
            status="on_time",
        )

        with patch("apps.attendance.reminder_service.send_web_push", return_value=True) as mock_push:
            t1 = datetime(2026, 11, 25, 18, 15, 0, tzinfo=BD_TZ)
            w1 = evaluate_and_send_missing_checkout_reminder(self.emp, now=t1)
            w2 = evaluate_and_send_missing_checkout_reminder(self.emp, now=t1 + timedelta(minutes=10))
            self.assertTrue(w1["sent"])
            self.assertFalse(w2["sent"])
            self.assertEqual(w2["reason"], "deduplicated")

            # Snooze for 60 minutes suppresses 19:15 window, resumes at 19:20
            snooze_missing_checkout_reminder(self.emp, now=datetime(2026, 11, 25, 18, 20, 0, tzinfo=BD_TZ))
            r_snoozed = evaluate_and_send_missing_checkout_reminder(
                self.emp, now=datetime(2026, 11, 25, 19, 15, 0, tzinfo=BD_TZ)
            )
            self.assertFalse(r_snoozed["sent"])
            self.assertEqual(r_snoozed["reason"], "snoozed")

            # Advance across remaining windows on 2026-11-25 -> capped at 4 total
            for h in (19, 20, 21, 22):
                evaluate_and_send_missing_checkout_reminder(
                    self.emp, now=datetime(2026, 11, 25, h, 25, 0, tzinfo=BD_TZ)
                )
            r_cap = evaluate_and_send_missing_checkout_reminder(
                self.emp, now=datetime(2026, 11, 25, 23, 25, 0, tzinfo=BD_TZ)
            )
            self.assertFalse(r_cap["sent"])
            self.assertEqual(r_cap["reason"], "daily_cap_reached")
            self.assertEqual(Notification.objects.filter(recipient=self.user, notif_type="missing").count(), 4)
            self.assertEqual(mock_push.call_count, 4)

    def test_3_resolved_checkout_stops_reminders_and_unresolved_sessions_group(self):
        """Grouped reminder covers multiple unresolved sessions; once checked out, reminders stop."""
        s1 = Attendance.objects.create(
            employee=self.emp,
            date=datetime(2026, 11, 24, 9, 0, 0, tzinfo=BD_TZ).date(),
            check_in_time=datetime(2026, 11, 24, 9, 0, 0, tzinfo=BD_TZ),
            attendance_type="check_in",
            type="office",
            status="on_time",
        )
        s2 = Attendance.objects.create(
            employee=self.emp,
            date=datetime(2026, 11, 25, 9, 0, 0, tzinfo=BD_TZ).date(),
            check_in_time=datetime(2026, 11, 25, 9, 0, 0, tzinfo=BD_TZ),
            attendance_type="check_in",
            type="office",
            status="on_time",
        )

        with patch("apps.attendance.reminder_service.send_web_push", return_value=True):
            r_grouped = evaluate_and_send_missing_checkout_reminder(
                self.emp, now=datetime(2026, 11, 25, 18, 15, 0, tzinfo=BD_TZ)
            )
            self.assertTrue(r_grouped["sent"])
            self.assertEqual(r_grouped["session_ids"], [s1.id, s2.id])
            self.assertEqual(r_grouped["action"], "correction")

            # Resolve both sessions -> no further reminder
            s1.check_out_time = datetime(2026, 11, 24, 18, 0, 0, tzinfo=BD_TZ)
            s1.save(update_fields=["check_out_time"])
            s2.check_out_time = datetime(2026, 11, 25, 18, 20, 0, tzinfo=BD_TZ)
            s2.save(update_fields=["check_out_time"])

            r_after = evaluate_and_send_missing_checkout_reminder(
                self.emp, now=datetime(2026, 11, 25, 19, 15, 0, tzinfo=BD_TZ)
            )
            self.assertFalse(r_after["sent"])
            self.assertEqual(r_after["reason"], "no_open_sessions")
