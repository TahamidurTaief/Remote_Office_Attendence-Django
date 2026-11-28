import decimal
import json
import uuid
from datetime import datetime, time, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.attendance.models import Attendance, AttendancePolicy
from apps.attendance.transaction_service import (
    AttendanceTransactionError,
    AttendanceTransactionService,
)
from apps.branches.models import Branch, Holiday, OfficeSchedule
from apps.employees.models import Employee, EmployeeProfile

User = get_user_model()
BD_TZ = ZoneInfo("Asia/Dhaka")


class ShiftLifecycleEnforcementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch = Branch.objects.create(
            name="Shift HQ",
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
            max_gps_accuracy_meters=100,
            photo_required=False,
        )
        cls.user = User.objects.create_user(
            email="shift_staff@example.com",
            password="password123",
            role="staff",
        )
        cls.master = Employee.objects.create(
            user=cls.user,
            employee_number="EMP-SHF-01",
            first_name="Shift",
            last_name="Staff",
            joined_date=datetime(2026, 10, 1, tzinfo=BD_TZ).date(),
        )
        cls.emp = EmployeeProfile.objects.create(
            user=cls.user,
            master_employee=cls.master,
            employee_id="EMP-SHF-01",
            full_name="Shift Staff",
            phone="01755555555",
            branch=cls.branch,
            is_active=True,
            joined_date=datetime(2026, 10, 1, tzinfo=BD_TZ).date(),
        )

    def test_1_default_policy_is_unactivated_and_legacy_sessions_stay_compatible(self):
        """Default policy leaves shift_lifecycle_enforced=False; unsnapshotted sessions have no invented deadline."""
        self.assertFalse(self.policy.shift_lifecycle_enforced)
        self.assertIsNone(self.policy.shift_lifecycle_activated_at)

        old_ci = datetime(2026, 11, 20, 9, 5, 0, tzinfo=BD_TZ)
        legacy_att = Attendance.objects.create(
            employee=self.emp,
            date=old_ci.date(),
            check_in_time=old_ci,
            attendance_type="check_in",
            type="office",
        )
        self.assertIsNone(legacy_att.checkout_deadline_at)
        self.assertIsNone(legacy_att.shift_date)

        # Legacy explicit checkout still works even days later when unsnapshotted
        later = datetime(2026, 11, 25, 17, 0, 0, tzinfo=BD_TZ)
        with patch("django.utils.timezone.now", return_value=later), patch(
            "django.utils.timezone.localtime", return_value=later
        ), patch("apps.attendance.transaction_service.notify_admins"):
            res = AttendanceTransactionService.check_out(
                self.user,
                {
                    "session_id": legacy_att.id,
                    "latitude": 23.8103,
                    "longitude": 90.4125,
                    "accuracy": 20,
                },
                validate_photo=False,
            )
            self.assertTrue(res["success"])

    def test_2_activated_snapshot_is_immutable_across_schedule_edits(self):
        """Activated check-in stores immutable snapshot; subsequent OfficeSchedule edits do not move deadline."""
        self.policy.shift_lifecycle_enforced = True
        self.policy.shift_policy_version = 2
        self.policy.save()

        ci_now = datetime(2026, 11, 25, 9, 5, 0, tzinfo=BD_TZ)
        with patch("django.utils.timezone.now", return_value=ci_now), patch(
            "django.utils.timezone.localtime", return_value=ci_now
        ), patch("apps.attendance.transaction_service.notify_admins"):
            ci = AttendanceTransactionService.check_in(
                self.user,
                {"latitude": 23.8103, "longitude": 90.4125, "accuracy": 20},
                validate_photo=False,
            )

        att = Attendance.objects.get(pk=ci["session_id"])
        expected_deadline = datetime(2026, 11, 26, 9, 0, 0, tzinfo=BD_TZ)
        self.assertEqual(att.shift_date, ci_now.date())
        self.assertEqual(att.shift_start_time, time(9, 0))
        self.assertEqual(att.shift_end_time, time(18, 0))
        self.assertEqual(att.checkout_deadline_at, expected_deadline)
        self.assertEqual(att.shift_policy_version, 2)

        # Edit live schedule to 11:00-20:00 — existing session deadline must NOT move
        self.schedule.office_start_time = time(11, 0)
        self.schedule.office_end_time = time(20, 0)
        self.schedule.save()

        at_old_deadline = datetime(2026, 11, 26, 9, 0, 0, tzinfo=BD_TZ)
        with patch("django.utils.timezone.now", return_value=at_old_deadline), patch(
            "django.utils.timezone.localtime", return_value=at_old_deadline
        ):
            with self.assertRaises(AttendanceTransactionError) as cm:
                AttendanceTransactionService.check_out(
                    self.user,
                    {
                        "session_id": att.id,
                        "latitude": 23.8103,
                        "longitude": 90.4125,
                        "accuracy": 20,
                    },
                    validate_photo=False,
                )
            self.assertEqual(cm.exception.status_code, 400)
            self.assertIn("correction", str(cm.exception).lower())

        att.refresh_from_db()
        self.assertIsNone(att.check_out_time)
        self.assertEqual(att.checkout_deadline_at, expected_deadline)

    def test_3_overnight_2200_0600_boundary_and_holiday_never_extends_deadline(self):
        """Activated 22:00-06:00 allows checkout before next 22:00 and rejects at 22:00 even if next day is holiday."""
        self.policy.shift_lifecycle_enforced = True
        self.policy.save()
        self.schedule.office_start_time = time(22, 0)
        self.schedule.office_end_time = time(6, 0)
        self.schedule.save()

        # Holiday tomorrow (2026-11-26) must not extend deadline
        Holiday.objects.create(name="Test Holiday", date=datetime(2026, 11, 26).date(), branch=self.branch)

        ci_now = datetime(2026, 11, 25, 22, 10, 0, tzinfo=BD_TZ)
        with patch("django.utils.timezone.now", return_value=ci_now), patch(
            "django.utils.timezone.localtime", return_value=ci_now
        ), patch("apps.attendance.transaction_service.notify_admins"):
            ci = AttendanceTransactionService.check_in(
                self.user,
                {"latitude": 23.8103, "longitude": 90.4125, "accuracy": 20},
                validate_photo=False,
            )

        att = Attendance.objects.get(pk=ci["session_id"])
        self.assertEqual(att.checkout_deadline_at, datetime(2026, 11, 26, 22, 0, 0, tzinfo=BD_TZ))

        # At 22:00:00 -> rejected (correction required)
        at_2200 = datetime(2026, 11, 26, 22, 0, 0, tzinfo=BD_TZ)
        with patch("django.utils.timezone.now", return_value=at_2200), patch(
            "django.utils.timezone.localtime", return_value=at_2200
        ):
            with self.assertRaises(AttendanceTransactionError) as cm:
                AttendanceTransactionService.check_out(
                    self.user,
                    {"session_id": att.id, "latitude": 23.8103, "longitude": 90.4125, "accuracy": 20},
                    validate_photo=False,
                )
            self.assertEqual(cm.exception.status_code, 400)

        # Strictly before 22:00:00 (21:59:59) -> allowed
        before_2200 = datetime(2026, 11, 26, 21, 59, 59, tzinfo=BD_TZ)
        with patch("django.utils.timezone.now", return_value=before_2200), patch(
            "django.utils.timezone.localtime", return_value=before_2200
        ), patch("apps.attendance.transaction_service.notify_admins"):
            co = AttendanceTransactionService.check_out(
                self.user,
                {"session_id": att.id, "latitude": 23.8103, "longitude": 90.4125, "accuracy": 20},
                validate_photo=False,
            )
            self.assertTrue(co["success"])

    def test_4_old_unresolved_never_blocks_new_shift_and_offline_chain_idempotent(self):
        """Multiple historical open sessions do not block today's check-in; duplicate same-shift rejected; bulk_sync chain works."""
        self.policy.shift_lifecycle_enforced = True
        self.policy.save()

        # Two historical unresolved sessions (yesterday and day before)
        h1 = Attendance.objects.create(
            employee=self.emp,
            date=datetime(2026, 11, 23).date(),
            check_in_time=datetime(2026, 11, 23, 9, 0, 0, tzinfo=BD_TZ),
            attendance_type="check_in",
            type="office",
            shift_date=datetime(2026, 11, 23).date(),
            checkout_deadline_at=datetime(2026, 11, 24, 9, 0, 0, tzinfo=BD_TZ),
        )
        h2 = Attendance.objects.create(
            employee=self.emp,
            date=datetime(2026, 11, 24).date(),
            check_in_time=datetime(2026, 11, 24, 9, 0, 0, tzinfo=BD_TZ),
            attendance_type="check_in",
            type="office",
            shift_date=datetime(2026, 11, 24).date(),
            checkout_deadline_at=datetime(2026, 11, 25, 9, 0, 0, tzinfo=BD_TZ),
        )

        today_now = datetime(2026, 11, 25, 17, 0, 0, tzinfo=BD_TZ)
        ci_uuid = str(uuid.uuid4())
        co_uuid = str(uuid.uuid4())
        ci_time = datetime(2026, 11, 25, 9, 10, 0, tzinfo=BD_TZ).isoformat()
        co_time = datetime(2026, 11, 25, 16, 45, 0, tzinfo=BD_TZ).isoformat()

        self.client.force_login(self.user)
        with patch("django.utils.timezone.now", return_value=today_now), patch(
            "django.utils.timezone.localtime", return_value=today_now
        ), patch("apps.attendance.transaction_service.notify_admins"):
            # Duplicate check-in within same shift after offline check-in
            bulk_ci = {
                "items": [
                    {
                        "uuid": ci_uuid,
                        "action": "check_in",
                        "payload": {
                            "latitude": 23.8103,
                            "longitude": 90.4125,
                            "accuracy": 20,
                            "client_event_time": ci_time,
                            "sync_uuid": ci_uuid,
                        },
                    }
                ]
            }
            r1 = self.client.post(reverse("attendance:bulk_sync"), data=json.dumps(bulk_ci), content_type="application/json")
            self.assertEqual(r1.status_code, 200)
            self.assertEqual(r1.json()["synced"], 1)

            # Same-shift duplicate check-in must be rejected
            with self.assertRaises(AttendanceTransactionError):
                AttendanceTransactionService.check_in(
                    self.user,
                    {"latitude": 23.8103, "longitude": 90.4125, "accuracy": 20},
                    validate_photo=False,
                )

            # Dependent offline checkout targets the new session cleanly and is idempotent on retry
            bulk_co = {
                "items": [
                    bulk_ci["items"][0],
                    {
                        "uuid": co_uuid,
                        "action": "check_out",
                        "depends_on_sync_uuid": ci_uuid,
                        "payload": {
                            "latitude": 23.8103,
                            "longitude": 90.4125,
                            "accuracy": 20,
                            "client_event_time": co_time,
                            "sync_uuid": co_uuid,
                            "depends_on_sync_uuid": ci_uuid,
                        },
                    },
                ]
            }
            r2 = self.client.post(reverse("attendance:bulk_sync"), data=json.dumps(bulk_co), content_type="application/json")
            self.assertEqual(r2.status_code, 200)
            self.assertEqual(r2.json()["synced"], 2)

        # Historical open sessions remain completely untouched
        h1.refresh_from_db()
        h2.refresh_from_db()
        self.assertIsNone(h1.check_out_time)
        self.assertIsNone(h2.check_out_time)

    def test_5_surfaces_expose_consistent_active_session_and_unresolved_past_count(self):
        """attendance_status, staff:home, staff:attendance_card, and staff:check_in ignore expired past sessions."""
        self.policy.shift_lifecycle_enforced = True
        self.policy.save()

        # Expired unresolved session from yesterday
        Attendance.objects.create(
            employee=self.emp,
            date=datetime(2026, 11, 24).date(),
            check_in_time=datetime(2026, 11, 24, 9, 0, 0, tzinfo=BD_TZ),
            attendance_type="check_in",
            type="office",
            shift_date=datetime(2026, 11, 24).date(),
            checkout_deadline_at=datetime(2026, 11, 25, 9, 0, 0, tzinfo=BD_TZ),
        )

        today_now = datetime(2026, 11, 25, 10, 0, 0, tzinfo=BD_TZ)
        self.client.force_login(self.user)
        with patch("django.utils.timezone.now", return_value=today_now), patch(
            "django.utils.timezone.localtime", return_value=today_now
        ):
            status_resp = self.client.get(reverse("attendance:status") + "?format=json")
            self.assertEqual(status_resp.status_code, 200)
            s_data = status_resp.json()
            self.assertFalse(s_data["has_active_session"])
            self.assertIsNone(s_data["active_session_id"])
            self.assertEqual(s_data["unresolved_past_sessions_count"], 1)

            for url_name in ("staff:home", "staff:attendance_card", "staff:check_in"):
                page_resp = self.client.get(reverse(url_name))
                self.assertEqual(page_resp.status_code, 200, msg=url_name)
                self.assertIsNone(page_resp.context["active_session"], msg=url_name)
