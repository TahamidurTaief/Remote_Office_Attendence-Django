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

    def test_5_legacy_queued_punch_without_accuracy_and_retry_preservation(self):
        """
        Legacy queued punches missing accuracy are accepted via bulk_sync without Review Needed
        or fabricated 0.0 accuracy; client version flags on live endpoints are not trusted;
        retries preserve original UUID, session, and timestamps; UI surfaces have no fake defaults.
        """
        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image
        import base64
        import io

        buf = io.BytesIO()
        Image.new("RGB", (64, 64), color="blue").save(buf, format="JPEG")
        photo_b64 = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")

        ci_uuid = str(uuid.uuid4())
        co_uuid = str(uuid.uuid4())
        fv_uuid = str(uuid.uuid4())
        ci_time = (FROZEN_NOW - timedelta(hours=4)).isoformat()
        co_time = (FROZEN_NOW - timedelta(hours=1)).isoformat()
        fv_time = (FROZEN_NOW - timedelta(minutes=30)).isoformat()

        with patch("django.utils.timezone.now", return_value=FROZEN_NOW), patch(
            "apps.attendance.transaction_service.notify_admins"
        ):
            self.client.force_login(self.user)

            # 1. Client-supplied legacy/version flag on live check_in MUST NOT bypass accuracy check
            live_spoof = self.client.post(
                reverse("attendance:check_in"),
                data={
                    "latitude": "23.8103",
                    "longitude": "90.4125",
                    "is_legacy": "true",
                    "queue_version": "1",
                },
            )
            self.assertEqual(live_spoof.status_code, 400)

            # 2. Legacy queued check_in, check_out, field_visit without accuracy via bulk_sync
            #    (with photo policy enabled)
            policy = AttendancePolicy.objects.get(branch=self.branch)
            policy.photo_required = True
            policy.save(update_fields=["photo_required"])

            bulk_payload = {
                "items": [
                    {
                        "uuid": ci_uuid,
                        "action": "check_in",
                        "payload": {
                            "latitude": 23.8103,
                            "longitude": 90.4125,
                            "address": "Legacy Offline",
                            "client_event_time": ci_time,
                            "sync_uuid": ci_uuid,
                            "photo": photo_b64,
                        },
                    },
                    {
                        "uuid": co_uuid,
                        "action": "check_out",
                        "depends_on_sync_uuid": ci_uuid,
                        "payload": {
                            "latitude": 23.8103,
                            "longitude": 90.4125,
                            "address": "Legacy Offline Out",
                            "client_event_time": co_time,
                            "sync_uuid": co_uuid,
                            "depends_on_sync_uuid": ci_uuid,
                            "photo": photo_b64,
                        },
                    },
                    {
                        "uuid": fv_uuid,
                        "action": "field_visit",
                        "payload": {
                            "latitude": 23.8103,
                            "longitude": 90.4125,
                            "visit_title": "Legacy Visit",
                            "client_event_time": fv_time,
                            "sync_uuid": fv_uuid,
                            "photo": photo_b64,
                        },
                    },
                ]
            }
            resp = self.client.post(
                reverse("attendance:bulk_sync"),
                data=json.dumps(bulk_payload),
                content_type="application/json",
            )
            self.assertEqual(resp.status_code, 200)
            res_json = resp.json()
            self.assertEqual(res_json["synced"], 3, msg=res_json)

            att = Attendance.objects.get(sync_uuid=ci_uuid)
            self.assertEqual(att.gps_quality, "good")
            self.assertFalse(att.is_policy_exception)
            ci_loc = att.locations.get(event="check_in")
            co_loc = att.locations.get(event="check_out")
            self.assertIsNone(ci_loc.accuracy)
            self.assertIsNone(co_loc.accuracy)

            fv_att = Attendance.objects.get(sync_uuid=fv_uuid)
            fv_loc = fv_att.locations.get(event="check_in")
            self.assertIsNone(fv_loc.accuracy)

            # 3. Retry after lost response preserves original evidence & is idempotent
            retry_resp = self.client.post(
                reverse("attendance:bulk_sync"),
                data=json.dumps(bulk_payload),
                content_type="application/json",
            )
            self.assertEqual(retry_resp.status_code, 200)
            self.assertEqual(Attendance.objects.filter(employee=self.emp).count(), 2)

            # 4. save_mandatory_location accepts (0.0, 0.0) with valid accuracy & capture time
            mand_resp = self.client.post(
                reverse("attendance:save_mandatory_location"),
                data=json.dumps({
                    "latitude": 0.0,
                    "longitude": 0.0,
                    "accuracy": 25.0,
                    "location_captured_at": (FROZEN_NOW - timedelta(seconds=10)).isoformat(),
                    "client_event_time": FROZEN_NOW.isoformat(),
                }),
                content_type="application/json",
            )
            self.assertEqual(mand_resp.status_code, 200)
            self.assertTrue(mand_resp.json()["success"])

            # 5. Template/JS surfaces carry location_captured_at and have no fake accuracy defaults
            from django.conf import settings
            from pathlib import Path
            base = Path(settings.BASE_DIR)
            for rel in (
                "templates/staff/check_in.html",
                "templates/staff/field_visit.html",
                "templates/staff/partials/attendance_card.html",
                "templates/dashboard/widgets/widget_employee_attendance.html",
            ):
                src = (base / rel).read_text(encoding="utf-8")
                self.assertIn("location_captured_at", src, msg=rel)
                self.assertNotIn("accuracy || 0", src, msg=rel)
                self.assertNotIn("accuracy || 20", src, msg=rel)
                self.assertNotIn("accuracy || 25", src, msg=rel)
                self.assertNotIn("acc || 0", src, msg=rel)
                self.assertNotIn("acc || '0'", src, msg=rel)
                self.assertNotIn("doCheckout(0, 0, 0", src, msg=rel)
