import os
import json
import sqlite3
from django.test import TestCase
from django.contrib.auth import get_user_model
from apps.backups.models import BackupRecord, GoogleDriveConfig
from apps.backups.utils import create_backup

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
