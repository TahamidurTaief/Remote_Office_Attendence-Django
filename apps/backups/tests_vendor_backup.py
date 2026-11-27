import os
import sqlite3
import subprocess
import tempfile
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.backups import utils as backup_utils
from apps.backups.models import BackupRecord, GoogleDriveConfig
from apps.branches.models import Branch
from apps.employees.models import EmployeeProfile

User = get_user_model()


class VendorAwareBackupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch = Branch.objects.create(
            name="Backup Synthetic Branch", latitude=23.81, longitude=90.41, radius_meters=200
        )
        cls.user = User.objects.create_user(email="bkp_emp@example.com", password="pass", role="staff")
        cls.emp = EmployeeProfile.objects.create(
            user=cls.user,
            employee_id="BKP-001",
            full_name="Synthetic Backup User",
            phone="01760000001",
            branch=cls.branch,
            is_active=True,
            joined_date=date(2024, 1, 1),
        )

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)
        self.patch_dir = patch.object(backup_utils, "BACKUP_DIR", self.tmp_dir.name)
        self.patch_cleanup = patch.object(backup_utils, "cleanup_old_backups")
        self.patch_dir.start()
        self.patch_cleanup.start()
        self.addCleanup(self.patch_dir.stop)
        self.addCleanup(self.patch_cleanup.stop)

    def test_1_sqlite_backup_round_trip_contains_synthetic_records(self):
        record = backup_utils.create_backup(backup_type="manual", created_by=self.user)
        self.assertEqual(record.status, "completed")
        self.assertTrue(os.path.exists(record.file_path))

        db_path = record.file_path.replace(".json", ".sqlite3")
        self.assertTrue(os.path.exists(db_path))
        self.assertGreater(os.path.getsize(db_path), 0)

        conn = sqlite3.connect(db_path)
        try:
            branch_names = [r[0] for r in conn.execute("SELECT name FROM branches_branch").fetchall()]
            emp_ids = [r[0] for r in conn.execute("SELECT employee_id FROM employees_employeeprofile").fetchall()]
        finally:
            conn.close()

        self.assertIn("Backup Synthetic Branch", branch_names)
        self.assertIn("BKP-001", emp_ids)

    @patch("apps.backups.utils.upload_to_drive")
    @patch("shutil.which", return_value="/usr/bin/pg_dump")
    @patch("subprocess.run")
    def test_2_postgresql_nonzero_exit_fails_without_upload_or_completed_status(
        self, mock_run, _mock_which, mock_upload
    ):
        mock_run.return_value = SimpleNamespace(returncode=1, stderr="connection refused", stdout="")

        with (
            patch("django.db.connection.vendor", "postgresql"),
            patch.dict("django.db.connection.settings_dict", {"NAME": "prod_db", "HOST": "db", "PORT": "5432", "USER": "pg", "PASSWORD": "secret"}, clear=False),
        ):
            with self.assertRaises(RuntimeError):
                backup_utils.create_backup(backup_type="manual", created_by=self.user)

        rec = BackupRecord.objects.latest("id")
        self.assertEqual(rec.status, "failed")
        self.assertIn("pg_dump failed", rec.error_message)
        self.assertNotIn("secret", rec.error_message)
        mock_upload.assert_not_called()

        # Verify subprocess called with arg array, shell=False, isolated env
        call_kwargs = mock_run.call_args.kwargs
        self.assertFalse(call_kwargs["shell"])
        self.assertEqual(set(call_kwargs["env"].keys()), {"PATH", "PGPASSWORD"})
        self.assertGreater(call_kwargs["timeout"], 0)
        self.assertFalse(os.path.exists(rec.file_path.replace(".json", ".sql")))

    @patch("apps.backups.utils.upload_to_drive")
    @patch("shutil.which", return_value=None)
    def test_3_postgresql_missing_pg_dump_fails_visibly(self, _mock_which, mock_upload):
        with patch("django.db.connection.vendor", "postgresql"):
            with self.assertRaises(RuntimeError) as ctx:
                backup_utils.create_backup(backup_type="manual", created_by=self.user)

        self.assertIn("pg_dump utility not found", str(ctx.exception))
        rec = BackupRecord.objects.latest("id")
        self.assertEqual(rec.status, "failed")
        mock_upload.assert_not_called()

    @patch("apps.backups.utils.upload_to_drive")
    @patch("shutil.which", return_value="/usr/bin/pg_dump")
    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["pg_dump"], timeout=300))
    def test_4_postgresql_timeout_fails_and_cleans_temp_file(self, _mock_run, _mock_which, mock_upload):
        with (
            patch("django.db.connection.vendor", "postgresql"),
            patch.dict("django.db.connection.settings_dict", {"NAME": "prod_db"}, clear=False),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                backup_utils.create_backup(backup_type="manual", created_by=self.user)

        self.assertIn("timed out", str(ctx.exception))
        rec = BackupRecord.objects.latest("id")
        self.assertEqual(rec.status, "failed")
        mock_upload.assert_not_called()
        leftover_tmp = [f for f in os.listdir(self.tmp_dir.name) if f.endswith(".tmp")]
        self.assertEqual(leftover_tmp, [])

    @patch("apps.backups.utils.upload_to_drive")
    def test_5_encryption_or_upload_failure_handled_safely(self, mock_upload):
        cfg = GoogleDriveConfig.get_config()
        cfg.encryption_enabled = True
        cfg.master_key_wrapped = "invalid-wrapped-key"
        cfg.is_enabled = True
        cfg.auto_upload_to_drive = True
        cfg.save()

        # Encryption failure -> failed status, no upload
        with patch("apps.backups.utils.encrypt_file", side_effect=ValueError("bad key")):
            with self.assertRaises(RuntimeError):
                backup_utils.create_backup(backup_type="manual", created_by=self.user)

        rec_enc = BackupRecord.objects.latest("id")
        self.assertEqual(rec_enc.status, "failed")
        self.assertIn("Encryption failed", rec_enc.error_message)
        mock_upload.assert_not_called()

        # Upload failure after valid unencrypted backup -> gdrive_uploaded stays False, error recorded
        cfg.encryption_enabled = False
        cfg.master_key_wrapped = ""
        cfg.save()
        mock_upload.side_effect = RuntimeError("Drive network error")

        rec_up = backup_utils.create_backup(backup_type="manual", created_by=self.user)
        self.assertEqual(rec_up.status, "completed")
        self.assertFalse(rec_up.gdrive_uploaded)
        self.assertIn("Drive upload failed", rec_up.error_message)
