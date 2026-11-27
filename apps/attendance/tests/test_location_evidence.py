import decimal
import json
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.attendance.models import Attendance, AttendanceLocation, AttendancePolicy
from apps.attendance.transaction_service import (
    AttendanceTransactionError,
    AttendanceTransactionService,
)
from apps.branches.models import Branch, OfficeSchedule
from apps.employees.models import Employee, EmployeeLocationSync, EmployeeProfile

User = get_user_model()
BD_TZ = ZoneInfo("Asia/Dhaka")
FROZEN_NOW = datetime(2026, 11, 25, 14, 0, 0, tzinfo=BD_TZ)


class LocationEvidenceValidationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch = Branch.objects.create(
            name="Loc HQ",
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
            geofencing_policy="warning",
            allow_outside_geofence=True,
            photo_required=False,
        )
        cls.user = User.objects.create_user(
            email="loc_staff@example.com",
            password="password123",
            role="staff",
        )
        cls.master = Employee.objects.create(
            user=cls.user,
            employee_number="EMP-LOC-01",
            first_name="Loc",
            last_name="Staff",
            joined_date=FROZEN_NOW.date() - timedelta(days=30),
        )
        cls.emp = EmployeeProfile.objects.create(
            user=cls.user,
            master_employee=cls.master,
            employee_id="EMP-LOC-01",
            full_name="Loc Staff",
            phone="01733333333",
            branch=cls.branch,
            is_active=True,
            joined_date=FROZEN_NOW.date() - timedelta(days=30),
        )

    def test_1_worldwide_and_zero_coords_with_fresh_35m_fix_accepted(self):
        """Fresh 35m fix from another country and valid (0.0, 0.0) coords are accepted across all entry points."""
        with patch("django.utils.timezone.now", return_value=FROZEN_NOW), patch(
            "apps.attendance.transaction_service.notify_admins"
        ):
            captured = (FROZEN_NOW - timedelta(seconds=15)).isoformat()
            # London coordinates (worldwide)
            ci = AttendanceTransactionService.check_in(
                self.user,
                {
                    "latitude": 51.5074,
                    "longitude": -0.1278,
                    "accuracy": 35,
                    "location_captured_at": captured,
                },
                validate_photo=False,
            )
            self.assertTrue(ci["success"])

            # Valid (0.0, 0.0) coordinates on location_sync
            self.client.force_login(self.user)
            loc_resp = self.client.post(
                reverse("attendance:location_sync"),
                data=json.dumps({
                    "latitude": 0.0,
                    "longitude": 0.0,
                    "accuracy": 35,
                    "location_captured_at": captured,
                }),
                content_type="application/json",
            )
            self.assertEqual(loc_resp.status_code, 200)
            self.assertTrue(loc_resp.json()["success"])
            self.assertEqual(EmployeeLocationSync.objects.filter(employee=self.emp).count(), 1)

            # Field visit in Tokyo
            fv = AttendanceTransactionService.field_visit(
                self.user,
                {
                    "latitude": 35.6762,
                    "longitude": 139.6503,
                    "accuracy": 35,
                    "visit_title": "Tokyo Client",
                    "location_captured_at": captured,
                },
                validate_photo=False,
            )
            self.assertTrue(fv["success"])

            # Checkout at (0.0, 0.0)
            co = AttendanceTransactionService.check_out(
                self.user,
                {
                    "latitude": 0.0,
                    "longitude": 0.0,
                    "accuracy": 35,
                    "session_id": ci["session_id"],
                    "location_captured_at": captured,
                },
                validate_photo=False,
            )
            self.assertTrue(co["success"])

    def test_2_non_finite_out_of_range_and_bad_accuracy_rejected(self):
        """Non-finite, out-of-range coords and missing/zero/negative/bool accuracy rejected with zero writes."""
        with patch("django.utils.timezone.now", return_value=FROZEN_NOW), patch(
            "apps.attendance.transaction_service.notify_admins"
        ):
            bad_payloads = [
                {"latitude": float("nan"), "longitude": 90.41, "accuracy": 20},
                {"latitude": 91.0, "longitude": 90.41, "accuracy": 20},
                {"latitude": 23.81, "longitude": 181.0, "accuracy": 20},
                {"latitude": 23.81, "longitude": 90.41},
                {"latitude": 23.81, "longitude": 90.41, "accuracy": 0},
                {"latitude": 23.81, "longitude": 90.41, "accuracy": -10},
                {"latitude": 23.81, "longitude": 90.41, "accuracy": True},
            ]
            for bad in bad_payloads:
                for fn in (
                    AttendanceTransactionService.check_in,
                    AttendanceTransactionService.check_out,
                    AttendanceTransactionService.field_visit,
                ):
                    with self.assertRaises(AttendanceTransactionError) as cm:
                        fn(self.user, dict(bad), validate_photo=False)
                    self.assertEqual(cm.exception.status_code, 400)

            self.assertEqual(Attendance.objects.filter(employee=self.emp).count(), 0)
            self.assertEqual(AttendanceLocation.objects.count(), 0)

    def test_3_stale_capture_rejected_while_offline_next_day_upload_accepted(self):
        """Five-hour-old capture is rejected; offline punch captured 15s before event_time and uploaded next day succeeds."""
        with patch("django.utils.timezone.now", return_value=FROZEN_NOW), patch(
            "apps.attendance.transaction_service.notify_admins"
        ):
            stale_capture = (FROZEN_NOW - timedelta(hours=5)).isoformat()
            with self.assertRaises(AttendanceTransactionError) as cm:
                AttendanceTransactionService.check_in(
                    self.user,
                    {
                        "latitude": 23.8103,
                        "longitude": 90.4125,
                        "accuracy": 25,
                        "location_captured_at": stale_capture,
                    },
                    validate_photo=False,
                )
            self.assertEqual(cm.exception.status_code, 400)
            self.assertIn("stale", str(cm.exception).lower())

        # Offline event yesterday at 09:00 with location captured 15s earlier, uploaded today
        yesterday_event = FROZEN_NOW - timedelta(hours=29)
        yesterday_capture = yesterday_event - timedelta(seconds=15)
        with patch("django.utils.timezone.now", return_value=FROZEN_NOW), patch(
            "apps.attendance.transaction_service.notify_admins"
        ):
            res = AttendanceTransactionService.check_in(
                self.user,
                {
                    "latitude": 23.8103,
                    "longitude": 90.4125,
                    "accuracy": 25,
                    "client_event_time": yesterday_event.isoformat(),
                    "location_captured_at": yesterday_capture.isoformat(),
                },
                validate_photo=False,
            )
            self.assertTrue(res["success"])

    def test_4_above_threshold_accuracy_rejected_across_entries(self):
        """Accuracy > 100m (e.g. 150m approximate/indoor fix) rejected across check_in, check_out, field_visit, location_sync."""
        with patch("django.utils.timezone.now", return_value=FROZEN_NOW), patch(
            "apps.attendance.transaction_service.notify_admins"
        ):
            # Create valid active session first
            ci = AttendanceTransactionService.check_in(
                self.user,
                {"latitude": 23.8103, "longitude": 90.4125, "accuracy": 25},
                validate_photo=False,
            )

            poor = {"latitude": 23.8103, "longitude": 90.4125, "accuracy": 150}
            with self.assertRaises(AttendanceTransactionError) as cm_co:
                AttendanceTransactionService.check_out(
                    self.user, {**poor, "session_id": ci["session_id"]}, validate_photo=False
                )
            self.assertEqual(cm_co.exception.status_code, 400)

            with self.assertRaises(AttendanceTransactionError) as cm_fv:
                AttendanceTransactionService.field_visit(self.user, dict(poor), validate_photo=False)
            self.assertEqual(cm_fv.exception.status_code, 400)

            self.client.force_login(self.user)
            loc_resp = self.client.post(
                reverse("attendance:location_sync"),
                data=json.dumps(poor),
                content_type="application/json",
            )
            self.assertEqual(loc_resp.status_code, 400)
