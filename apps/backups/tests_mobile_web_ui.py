import io
import os
import tempfile
import zipfile
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from apps.backups import importer as backup_importer
from apps.backups import utils as backup_utils
from apps.backups.exporter import export_portable_archive
from apps.branches.models import Branch

User = get_user_model()


class MobileWebBackupIntegrationTests(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.staging_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)
        self.addCleanup(self.staging_dir.cleanup)
        self.patch_dir = patch.object(backup_utils, "BACKUP_DIR", self.tmp_dir.name)
        self.patch_cleanup = patch.object(backup_utils, "cleanup_old_backups")
        self.patch_staging = patch.object(backup_importer, "STAGING_DIR", self.staging_dir.name)
        self.patch_dir.start()
        self.patch_cleanup.start()
        self.patch_staging.start()
        self.addCleanup(self.patch_dir.stop)
        self.addCleanup(self.patch_cleanup.stop)
        self.addCleanup(self.patch_staging.stop)

        self.admin = User.objects.create_superuser(
            email="web_admin@example.com", password="pass", role="admin"
        )

    def test_1_authorized_zip_download_headers_and_no_htmx_swallow(self):
        Branch.objects.create(name="Web UI Branch", latitude=23.81, longitude=90.41)
        self.client.force_login(self.admin)

        # HTML page has hx-boost="false" and no Blob/base64 download hacks
        page = self.client.get(reverse("backups:backup_list"))
        self.assertEqual(page.status_code, 200)
        html = page.content.decode("utf-8")
        self.assertIn('name="format" value="portable"', html)
        self.assertIn('hx-boost="false"', html)
        self.assertNotIn("createObjectURL", html)

        # Authorized portable ZIP creation streams attachment with private/no-store headers
        resp = self.client.post(reverse("backups:create_backup"), {"format": "portable"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/zip")
        self.assertIn("attachment; filename=\"trackme_backup_", resp["Content-Disposition"])
        self.assertIn(".zip\"", resp["Content-Disposition"])
        self.assertIn("private", resp["Cache-Control"])
        self.assertIn("no-store", resp["Cache-Control"])
        self.assertEqual(resp["Pragma"], "no-cache")
        self.assertEqual(resp["X-Content-Type-Options"], "nosniff")

        body = b"".join(resp.streaming_content) if resp.streaming else resp.content
        resp.close()
        with zipfile.ZipFile(io.BytesIO(body), "r") as zf:
            self.assertIn("manifest.json", zf.namelist())

        # History download link also preserves attachment + private/no-store headers
        from apps.backups.models import BackupRecord
        rec = BackupRecord.objects.latest("id")
        dl = self.client.get(reverse("backups:download_backup", args=[rec.pk]))
        self.assertEqual(dl.status_code, 200)
        self.assertEqual(dl["Content-Type"], "application/zip")
        self.assertIn("private", dl["Cache-Control"])
        self.assertIn("no-store", dl["Cache-Control"])
        dl.close()

    def test_2_expired_session_blocks_import_and_retry_prevents_duplicate_execution(self):
        from django.test import Client

        self.client.force_login(self.admin)
        Branch.objects.create(name="Retry Branch", latitude=23.81, longitude=90.41)
        zip_path = os.path.join(self.tmp_dir.name, "retry.zip")
        export_portable_archive(zip_path)

        # Expired / unauthenticated session cannot preview or execute
        anon_client = Client()
        with open(zip_path, "rb") as fh:
            unauth_prev = anon_client.post(
                reverse("backups:preview_import"),
                {"archive": SimpleUploadedFile("retry.zip", fh.read(), content_type="application/zip")},
            )
        self.assertEqual(unauth_prev.status_code, 302)
        unauth_exec = anon_client.post(
            reverse("backups:execute_import"),
            {"preview_id": "abc", "archive_sha256": "0" * 64},
        )
        self.assertEqual(unauth_exec.status_code, 302)

        # Authenticated preview + execute consumes preview_id so retry cannot re-execute
        with open(zip_path, "rb") as fh:
            prev_resp = self.client.post(
                reverse("backups:preview_import"),
                {"archive": SimpleUploadedFile("retry.zip", fh.read(), content_type="application/zip")},
            )
        self.assertEqual(prev_resp.status_code, 200)
        pdata = prev_resp.json()

        exec1 = self.client.post(
            reverse("backups:execute_import"),
            {"preview_id": pdata["preview_id"], "archive_sha256": pdata["archive_sha256"]},
        )
        self.assertEqual(exec1.status_code, 200)

        exec_retry = self.client.post(
            reverse("backups:execute_import"),
            {"preview_id": pdata["preview_id"], "archive_sha256": pdata["archive_sha256"]},
        )
        self.assertEqual(exec_retry.status_code, 404)

        # UI JS includes AbortController cancel, single-flight lock, and expired-session handling
        html = self.client.get(reverse("backups:backup_list")).content.decode("utf-8")
        self.assertIn("AbortController", html)
        self.assertIn("if (!activePreview || executing) return;", html)
        self.assertIn("resp.redirected || resp.status === 401 || resp.status === 403", html)

    def test_3_service_worker_exclusions_and_upload_limit_error(self):
        sw_path = os.path.join(settings.BASE_DIR, "static", "js", "sw.js")
        with open(sw_path, encoding="utf-8") as fh:
            sw_src = fh.read()

        self.assertIn("event.request.method !== 'GET'", sw_src)
        self.assertIn("url.pathname.startsWith('/backups/')", sw_src)
        self.assertIn("url.pathname.startsWith('/attendance/')", sw_src)

        self.client.force_login(self.admin)
        tiny_limit = backup_importer.ArchiveLimits(max_compressed_bytes=16)
        with patch("apps.backups.importer.ArchiveLimits", return_value=tiny_limit):
            resp = self.client.post(
                reverse("backups:preview_import"),
                {"archive": SimpleUploadedFile("big.zip", b"0123456789abcdef0123", content_type="application/zip")},
            )
        self.assertEqual(resp.status_code, 413)
        self.assertIn("exceeds limit", resp.json()["error"])
        self.assertEqual(os.listdir(self.staging_dir.name), [])
