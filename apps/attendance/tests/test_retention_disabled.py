import decimal
import io
import os
import tempfile
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from PIL import Image

from apps.attendance.models import Attendance, AttendanceLocation
from apps.attendance.retention import (
    RETENTION_DISABLED_MESSAGE,
    delete_old_expired_records,
    get_retention_stats,
    mark_expired_records,
)
from apps.branches.models import Branch
from apps.employees.models import Employee, EmployeeProfile

User = get_user_model()
BD_TZ = ZoneInfo("Asia/Dhaka")
FROZEN_NOW = datetime(2026, 11, 28, 10, 0, 0, tzinfo=BD_TZ)


def _make_photo(name="ret_test.jpg"):
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color="green").save(buf, format="JPEG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/jpeg")


class RetentionDisabledTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch = Branch.objects.create(
            name="Retention Branch",
            latitude=decimal.Decimal("23.810300"),
            longitude=decimal.Decimal("90.412500"),
            radius_meters=200,
        )
        cls.user = User.objects.create_user(
            email="ret_staff@example.com",
            password="password123",
            role="staff",
        )
        cls.master = Employee.objects.create(
            user=cls.user,
            employee_number="EMP-RET-01",
            first_name="Ret",
            last_name="Staff",
            joined_date=FROZEN_NOW.date() - timedelta(days=400),
        )
        cls.emp = EmployeeProfile.objects.create(
            user=cls.user,
            master_employee=cls.master,
            employee_id="EMP-RET-01",
            full_name="Ret Staff",
            phone="01744444444",
            branch=cls.branch,
            is_active=True,
            joined_date=FROZEN_NOW.date() - timedelta(days=400),
        )

    def _seed_records(self):
        # 1. Old active record (> 3 months old, would have been marked expired)
        old_active = Attendance.objects.create(
            employee=self.emp,
            date=(FROZEN_NOW - timedelta(days=150)).date(),
            check_in_time=FROZEN_NOW - timedelta(days=150),
            attendance_type="check_in",
            type="office",
            is_expired=False,
            photo=_make_photo("old_active.jpg"),
        )
        AttendanceLocation.objects.create(
            attendance=old_active,
            event="check_in",
            latitude=23.8103,
            longitude=90.4125,
            address="HQ",
            accuracy=15.0,
            timestamp=FROZEN_NOW - timedelta(days=150),
        )

        # 2. Already-expired record within 2 months (deleting_soon window)
        exp_soon_at = FROZEN_NOW - timedelta(days=45)
        exp_soon = Attendance.objects.create(
            employee=self.emp,
            date=(FROZEN_NOW - timedelta(days=180)).date(),
            check_in_time=FROZEN_NOW - timedelta(days=180),
            attendance_type="check_in",
            type="office",
            is_expired=True,
            expired_at=exp_soon_at,
            photo=_make_photo("exp_soon.jpg"),
        )
        AttendanceLocation.objects.create(
            attendance=exp_soon,
            event="check_in",
            latitude=23.8103,
            longitude=90.4125,
            address="HQ",
            accuracy=15.0,
            timestamp=FROZEN_NOW - timedelta(days=180),
            is_expired=True,
        )

        # 3. Already-expired record older than 2 months (would have been deleted)
        exp_old_at = FROZEN_NOW - timedelta(days=90)
        exp_old = Attendance.objects.create(
            employee=self.emp,
            date=(FROZEN_NOW - timedelta(days=240)).date(),
            check_in_time=FROZEN_NOW - timedelta(days=240),
            attendance_type="check_in",
            type="office",
            is_expired=True,
            expired_at=exp_old_at,
            photo=_make_photo("exp_old.jpg"),
        )
        AttendanceLocation.objects.create(
            attendance=exp_old,
            event="check_in",
            latitude=23.8103,
            longitude=90.4125,
            address="HQ",
            accuracy=15.0,
            timestamp=FROZEN_NOW - timedelta(days=240),
            is_expired=True,
        )
        return old_active, exp_soon, exp_old

    def test_1_direct_helpers_perform_zero_queries_and_preserve_rows_and_media(self):
        with tempfile.TemporaryDirectory() as tmp_media, override_settings(MEDIA_ROOT=tmp_media):
            old_active, exp_soon, exp_old = self._seed_records()
            media_paths = [old_active.photo.path, exp_soon.photo.path, exp_old.photo.path]
            for p in media_paths:
                self.assertTrue(os.path.exists(p))

            with self.assertNumQueries(0):
                marked = mark_expired_records()
                deleted_att, deleted_loc = delete_old_expired_records()

            self.assertEqual(marked, 0)
            self.assertEqual((deleted_att, deleted_loc), (0, 0))
            self.assertEqual(Attendance.objects.count(), 3)
            self.assertEqual(AttendanceLocation.objects.count(), 3)

            old_active.refresh_from_db()
            exp_soon.refresh_from_db()
            exp_old.refresh_from_db()
            self.assertFalse(old_active.is_expired)
            self.assertIsNone(old_active.expired_at)
            self.assertTrue(exp_soon.is_expired)
            self.assertTrue(exp_old.is_expired)
            for p in media_paths:
                self.assertTrue(os.path.exists(p))

    def test_2_scheduled_run_retention_without_args_reports_disabled_and_preserves_data(self):
        with tempfile.TemporaryDirectory() as tmp_media, override_settings(MEDIA_ROOT=tmp_media):
            old_active, exp_soon, exp_old = self._seed_records()
            media_paths = [old_active.photo.path, exp_soon.photo.path, exp_old.photo.path]
            out = io.StringIO()

            with patch("django.utils.timezone.now", return_value=FROZEN_NOW):
                call_command("run_retention", stdout=out)

            output = out.getvalue()
            self.assertIn(RETENTION_DISABLED_MESSAGE, output)
            self.assertEqual(Attendance.objects.count(), 3)
            self.assertEqual(AttendanceLocation.objects.count(), 3)
            old_active.refresh_from_db()
            self.assertFalse(old_active.is_expired)
            for p in media_paths:
                self.assertTrue(os.path.exists(p))

    def test_3_dry_run_reports_accurate_candidate_stats_with_zero_writes(self):
        with tempfile.TemporaryDirectory() as tmp_media, override_settings(MEDIA_ROOT=tmp_media):
            self._seed_records()
            out = io.StringIO()

            with patch("django.utils.timezone.now", return_value=FROZEN_NOW):
                call_command("run_retention", "--dry-run", stdout=out)

            output = out.getvalue()
            self.assertIn("Active: 1", output)
            self.assertIn("Expired: 2", output)
            self.assertIn("To delete: 1", output)
            self.assertEqual(Attendance.objects.count(), 3)
            self.assertEqual(AttendanceLocation.objects.count(), 3)

    def test_4_get_retention_stats_uses_single_bounded_query(self):
        with tempfile.TemporaryDirectory() as tmp_media, override_settings(MEDIA_ROOT=tmp_media):
            self._seed_records()

            with patch("django.utils.timezone.now", return_value=FROZEN_NOW):
                with self.assertNumQueries(1):
                    stats = get_retention_stats()

            self.assertEqual(
                stats,
                {
                    "active_count": 1,
                    "expired_count": 2,
                    "expiring_soon": 1,
                    "deleting_soon": 1,
                    "to_be_deleted": 1,
                },
            )

    def test_5_no_bypass_flag_accepted(self):
        with self.assertRaises(CommandError):
            call_command("run_retention", "--execute")
        with self.assertRaises(TypeError):
            mark_expired_records(force=True)
        with self.assertRaises(TypeError):
            delete_old_expired_records(force=True)
