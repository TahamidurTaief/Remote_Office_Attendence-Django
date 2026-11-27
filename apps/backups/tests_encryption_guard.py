import os
import tempfile

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.backups.encryption import encrypt_file, generate_wrapped_key, unwrap_key
from apps.backups.models import BackupRecord, GoogleDriveConfig

User = get_user_model()


class BackupEncryptionRotationGuardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            email="enc_adm@example.com", password="pass", role="admin"
        )
        cls.staff = User.objects.create_user(
            email="enc_staff@example.com", password="pass", role="staff"
        )

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)

    def _make_encrypted_backup(self, wrapped_key, payload=b'{"ok": true}'):
        src_path = os.path.join(self.tmp_dir.name, "plain.json")
        enc_path = os.path.join(self.tmp_dir.name, "encrypted_backup.json")
        with open(src_path, "wb") as f:
            f.write(payload)
        encrypt_file(src_path, enc_path, wrapped_key)
        return BackupRecord.objects.create(
            backup_type="manual",
            status="completed",
            file_name="encrypted_backup.json",
            file_path=enc_path,
            file_size=len(payload),
            is_encrypted=True,
            created_by=self.admin,
        )

    def test_1_initial_key_generation_allowed_when_no_key_exists(self):
        cfg = GoogleDriveConfig.get_config()
        self.assertEqual(cfg.master_key_wrapped, "")

        self.client.force_login(self.admin)
        resp = self.client.post(reverse("backups:manage_encryption"), {"action": "generate"})
        self.assertEqual(resp.status_code, 302)

        cfg.refresh_from_db()
        self.assertTrue(cfg.encryption_enabled)
        self.assertTrue(bool(cfg.master_key_wrapped))
        unwrap_key(cfg.master_key_wrapped)

    def test_2_rotate_blocked_with_dependent_encrypted_backup_and_old_backup_stays_decryptable(self):
        original_key = generate_wrapped_key()
        cfg = GoogleDriveConfig.get_config()
        cfg.master_key_wrapped = original_key
        cfg.encryption_enabled = True
        cfg.save()

        payload = b'{"synthetic": "important_backup"}'
        rec = self._make_encrypted_backup(original_key, payload=payload)

        self.client.force_login(self.admin)
        resp = self.client.post(reverse("backups:manage_encryption"), {"action": "rotate"})
        self.assertEqual(resp.status_code, 409)
        self.assertNotIn(original_key, resp.content.decode())

        cfg.refresh_from_db()
        self.assertEqual(cfg.master_key_wrapped, original_key)

        dl_resp = self.client.get(reverse("backups:download_backup", kwargs={"pk": rec.pk}))
        self.assertEqual(dl_resp.status_code, 200)
        self.assertEqual(dl_resp.content, payload)

    def test_3_repeated_generate_blocked_and_preserves_existing_key(self):
        original_key = generate_wrapped_key()
        cfg = GoogleDriveConfig.get_config()
        cfg.master_key_wrapped = original_key
        cfg.encryption_enabled = True
        cfg.save()

        self.client.force_login(self.admin)
        resp = self.client.post(reverse("backups:manage_encryption"), {"action": "generate"})
        self.assertEqual(resp.status_code, 409)

        cfg.refresh_from_db()
        self.assertEqual(cfg.master_key_wrapped, original_key)

        # Non-admin remains denied
        self.client.force_login(self.staff)
        staff_resp = self.client.post(reverse("backups:manage_encryption"), {"action": "generate"})
        self.assertEqual(staff_resp.status_code, 403)

    def test_4_corrupt_key_and_inconsistent_metadata_blocked(self):
        # Corrupt key
        cfg = GoogleDriveConfig.get_config()
        cfg.master_key_wrapped = "not-a-valid-fernet-token"
        cfg.encryption_enabled = False
        cfg.save()

        self.client.force_login(self.admin)
        resp_corrupt = self.client.post(reverse("backups:manage_encryption"), {"action": "rotate"})
        self.assertEqual(resp_corrupt.status_code, 409)
        cfg.refresh_from_db()
        self.assertEqual(cfg.master_key_wrapped, "not-a-valid-fernet-token")

        # Missing key but BackupRecord(is_encrypted=True) exists (inconsistent metadata)
        cfg.master_key_wrapped = ""
        cfg.save()
        BackupRecord.objects.create(
            backup_type="manual",
            status="completed",
            file_name="orphan.json",
            is_encrypted=True,
        )
        resp_inconsistent = self.client.post(reverse("backups:manage_encryption"), {"action": "generate"})
        self.assertEqual(resp_inconsistent.status_code, 409)
        cfg.refresh_from_db()
        self.assertEqual(cfg.master_key_wrapped, "")

