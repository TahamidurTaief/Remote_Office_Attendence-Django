import os
import json
import sqlite3
from django.test import TestCase
from django.contrib.auth import get_user_model
from apps.backups.models import BackupRecord, GoogleDriveConfig
from apps.backups.utils import create_backup
from apps.backups.encryption import (
    generate_wrapped_key,
    unwrap_key,
    encrypt_file,
    decrypt_file_to_bytes,
    is_fernet_token,
)

User = get_user_model()

class BackupTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            phone='+8801700000010',
            password='password123',
            role='admin'
        )

    def test_create_backup_json_and_sqlite(self):
        backup = create_backup(backup_type="manual", created_by=self.user)
        self.assertEqual(backup.status, "completed")
        self.assertTrue(os.path.exists(backup.file_path))
        
        # Verify JSON content structure
        with open(backup.file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            self.assertIn("meta", data)
            self.assertIn("employees", data)
            self.assertIn("attendance", data)
            self.assertIn("expired_attendance", data)
            self.assertIn("branches", data)
            self.assertIn("leaves", data)
            self.assertIn("projects", data)
            self.assertIn("tasks", data)
            self.assertIn("progress_logs", data)
            self.assertIn("manpower_deployments", data)
            self.assertIn("project_materials", data)
            self.assertIn("expenses", data)

        # Verify SQLite copy exists and is a valid sqlite3 database
        db_filepath = backup.file_path.replace(".json", ".sqlite3")
        self.assertTrue(os.path.exists(db_filepath))
        
        conn = sqlite3.connect(db_filepath)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [t[0] for t in cursor.fetchall()]
        conn.close()
        
        # Verify some core django/app tables exist in the backup database
        self.assertIn("django_migrations", tables)
        self.assertIn("attendance_attendance", tables)
        self.assertIn("expense_expense", tables)

        # Clean up files
        if os.path.exists(backup.file_path):
            os.remove(backup.file_path)
        if os.path.exists(db_filepath):
            os.remove(db_filepath)


class EncryptionModuleTests(TestCase):
    """Unit tests for apps/backups/encryption.py"""

    def test_generate_wrapped_key_returns_string(self):
        wrapped = generate_wrapped_key()
        self.assertIsInstance(wrapped, str)
        self.assertGreater(len(wrapped), 40)

    def test_unwrap_key_round_trip(self):
        wrapped = generate_wrapped_key()
        fernet = unwrap_key(wrapped)
        plaintext = b"FieldTrack backup test payload"
        token = fernet.encrypt(plaintext)
        self.assertEqual(fernet.decrypt(token), plaintext)

    def test_unwrap_key_fails_on_garbage(self):
        with self.assertRaises(Exception):
            unwrap_key("not-a-valid-wrapped-key")

    def test_unwrap_key_fails_on_empty(self):
        with self.assertRaises(ValueError):
            unwrap_key("")

    def test_file_encrypt_decrypt_round_trip(self):
        import tempfile
        wrapped = generate_wrapped_key()
        plaintext = b'{"meta": "test backup data", "records": [1, 2, 3]}'

        with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tmp:
            tmp.write(plaintext)
            tmp_path = tmp.name

        try:
            # Encrypt in-place
            encrypt_file(tmp_path, tmp_path, wrapped)

            # Raw file should NOT be readable as JSON now
            with open(tmp_path, "rb") as f:
                raw = f.read()
            self.assertNotEqual(raw, plaintext)

            # is_fernet_token should detect it
            self.assertTrue(is_fernet_token(tmp_path))

            # Decrypt to bytes should return original plaintext
            recovered = decrypt_file_to_bytes(tmp_path, wrapped)
            self.assertEqual(recovered, plaintext)
        finally:
            os.remove(tmp_path)

    def test_decrypt_fails_with_wrong_key(self):
        import tempfile
        wrapped1 = generate_wrapped_key()
        wrapped2 = generate_wrapped_key()
        plaintext = b"sensitive backup content"

        with tempfile.NamedTemporaryFile(delete=False, suffix=".enc") as tmp:
            tmp.write(plaintext)
            tmp_path = tmp.name

        try:
            encrypt_file(tmp_path, tmp_path, wrapped1)
            with self.assertRaises(Exception):
                decrypt_file_to_bytes(tmp_path, wrapped2)
        finally:
            os.remove(tmp_path)


class EncryptedBackupIntegrationTests(TestCase):
    """Integration: create_backup() with encryption_enabled writes encrypted files."""

    def setUp(self):
        self.user = User.objects.create_user(
            phone='+8801700000011',
            password='password123',
            role='admin'
        )
        self.config = GoogleDriveConfig.get_config()
        self.config.encryption_enabled = True
        self.config.master_key_wrapped = generate_wrapped_key()
        self.config.save()

    def tearDown(self):
        self.config.encryption_enabled = False
        self.config.master_key_wrapped = ""
        self.config.save()

    def test_backup_file_is_encrypted_on_disk(self):
        backup = create_backup(backup_type="manual", created_by=self.user)
        self.assertEqual(backup.status, "completed")
        self.assertTrue(backup.is_encrypted)

        self.assertTrue(is_fernet_token(backup.file_path))
        with self.assertRaises(Exception):
            with open(backup.file_path, "r", encoding="utf-8") as f:
                json.load(f)

        config = GoogleDriveConfig.get_config()
        plaintext = decrypt_file_to_bytes(backup.file_path, config.master_key_wrapped)
        data = json.loads(plaintext)
        self.assertIn("meta", data)
        self.assertIn("employees", data)

        db_path = backup.file_path.replace(".json", ".sqlite3")
        if os.path.exists(backup.file_path):
            os.remove(backup.file_path)
        if os.path.exists(db_path):
            os.remove(db_path)

    def test_backup_without_key_stays_plaintext(self):
        self.config.master_key_wrapped = ""
        self.config.save()

        backup = create_backup(backup_type="manual", created_by=self.user)
        self.assertEqual(backup.status, "completed")
        self.assertFalse(backup.is_encrypted)

        db_path = backup.file_path.replace(".json", ".sqlite3")
        if os.path.exists(backup.file_path):
            os.remove(backup.file_path)
        if os.path.exists(db_path):
            os.remove(db_path)

