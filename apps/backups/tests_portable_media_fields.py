import datetime
import hashlib
import io
import os
import tempfile
from decimal import Decimal
from unittest.mock import patch

from PIL import Image
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.attendance.models import Attendance
from apps.backups import importer as backup_importer
from apps.backups.exporter import IncompleteBackupError, export_portable_archive
from apps.branches.models import Branch
from apps.employees.models import Employee, EmployeeDocument, EmployeeProfile

User = get_user_model()


def _make_png_bytes(color="blue", size=(48, 48)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


class PortableMediaFieldTests(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.staging_dir = tempfile.TemporaryDirectory()
        self.media_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)
        self.addCleanup(self.staging_dir.cleanup)
        self.addCleanup(self.media_dir.cleanup)
        self.patch_staging = patch.object(backup_importer, "STAGING_DIR", self.staging_dir.name)
        self.patch_staging.start()
        self.addCleanup(self.patch_staging.stop)

    def _clear_rows(self):
        Attendance.objects.all().delete()
        EmployeeDocument.objects.all().delete()
        EmployeeProfile.objects.all().delete()
        Employee.objects.all().delete()
        Branch.objects.all().delete()
        User.objects.all().delete()

    def test_1_processed_image_field_round_trip_empty_and_missing_fail(self):
        with override_settings(MEDIA_ROOT=self.media_dir.name):
            branch = Branch.objects.create(name="Photo Branch", latitude=Decimal("23.81"), longitude=Decimal("90.41"))
            u1 = User.objects.create_user(email="photo1@example.com", password="pass", role="staff")
            u2 = User.objects.create_user(email="photo2@example.com", password="pass", role="staff")
            e1 = Employee.objects.create(
                employee_number="PH-001", first_name="With", last_name="Photo", branch=branch, user=u1
            )
            e2 = Employee.objects.create(
                employee_number="PH-002", first_name="No", last_name="Photo", branch=branch, user=u2
            )
            p1 = EmployeeProfile.objects.create(
                user=u1,
                master_employee=e1,
                employee_id="PH-001",
                full_name="With Photo",
                phone="01744440001",
                branch=branch,
                joined_date=datetime.date(2025, 1, 1),
                profile_photo=SimpleUploadedFile("avatar.png", _make_png_bytes("navy"), content_type="image/png"),
            )
            p2 = EmployeeProfile.objects.create(
                user=u2,
                master_employee=e2,
                employee_id="PH-002",
                full_name="No Photo",
                phone="01744440002",
                branch=branch,
                joined_date=datetime.date(2025, 1, 1),
            )
            att = Attendance.objects.create(
                employee=p1,
                date=datetime.date(2026, 11, 20),
                check_in_time=timezone.now(),
                total_hours=Decimal("8.00"),
                status="on_time",
                photo=SimpleUploadedFile("checkin.png", _make_png_bytes("green"), content_type="image/png"),
            )

            p1_path = p1.profile_photo.name
            att_path = att.photo.name
            with default_storage.open(p1_path, "rb") as fh:
                p1_stored_bytes = fh.read()
            with default_storage.open(att_path, "rb") as fh:
                att_stored_bytes = fh.read()
            p1_sha = hashlib.sha256(p1_stored_bytes).hexdigest()
            att_sha = hashlib.sha256(att_stored_bytes).hexdigest()

            zip_path = os.path.join(self.tmp_dir.name, "photos.zip")
            manifest = export_portable_archive(zip_path)
            self.assertEqual(len(manifest["media"]["files"]), 2)
            manifest_shas = {m["storage_name"]: m["sha256"] for m in manifest["media"]["files"]}
            self.assertEqual(manifest_shas[p1_path], p1_sha)
            self.assertEqual(manifest_shas[att_path], att_sha)

            # Missing referenced image must fail export cleanly
            default_storage.delete(att_path)
            missing_zip = os.path.join(self.tmp_dir.name, "missing_photo.zip")
            with self.assertRaises(IncompleteBackupError):
                export_portable_archive(missing_zip)

            # Restore into clean DB and clean media storage
            if default_storage.exists(p1_path):
                default_storage.delete(p1_path)
            self._clear_rows()

            sha = backup_importer.compute_file_sha256(zip_path)
            res = backup_importer.execute_additive_import(zip_path, expected_sha256=sha)
            self.assertEqual(res["status"], "completed")
            self.assertEqual(res["media_placed"], 2)

            restored_p1 = EmployeeProfile.objects.get(employee_id="PH-001")
            restored_p2 = EmployeeProfile.objects.get(employee_id="PH-002")
            restored_att = Attendance.objects.get(employee=restored_p1)

            self.assertEqual(restored_p1.profile_photo.name, p1_path)
            self.assertFalse(bool(restored_p2.profile_photo))
            self.assertEqual(restored_att.photo.name, att_path)

            with default_storage.open(restored_p1.profile_photo.name, "rb") as fh:
                self.assertEqual(hashlib.sha256(fh.read()).hexdigest(), p1_sha)
            with default_storage.open(restored_att.photo.name, "rb") as fh:
                self.assertEqual(hashlib.sha256(fh.read()).hexdigest(), att_sha)

    def test_2_ordinary_file_field_regression(self):
        with override_settings(MEDIA_ROOT=self.media_dir.name):
            branch = Branch.objects.create(name="Doc Branch", latitude=Decimal("23.81"), longitude=Decimal("90.41"))
            u = User.objects.create_user(email="doc@example.com", password="pass", role="staff")
            emp = Employee.objects.create(
                employee_number="DOC-001", first_name="Doc", last_name="User", branch=branch, user=u
            )
            profile = EmployeeProfile.objects.create(
                user=u,
                master_employee=emp,
                employee_id="DOC-001",
                full_name="Doc User",
                phone="01744440003",
                branch=branch,
                joined_date=datetime.date(2025, 1, 1),
            )
            raw_pdf = b"%PDF-1.4\nOrdinary FileField Regression\n%%EOF"
            expected_sha = hashlib.sha256(raw_pdf).hexdigest()
            doc = EmployeeDocument.objects.create(
                employee=profile,
                document_type="id_proof",
                title="National ID",
                file=SimpleUploadedFile("nid.pdf", raw_pdf, content_type="application/pdf"),
                uploaded_by=u,
            )
            doc_path = doc.file.name

            zip_path = os.path.join(self.tmp_dir.name, "doc.zip")
            export_portable_archive(zip_path)
            default_storage.delete(doc_path)
            self._clear_rows()

            sha = backup_importer.compute_file_sha256(zip_path)
            res = backup_importer.execute_additive_import(zip_path, expected_sha256=sha)
            self.assertEqual(res["media_placed"], 1)

            restored_doc = EmployeeDocument.objects.get(title="National ID")
            self.assertEqual(restored_doc.file.name, doc_path)
            with default_storage.open(restored_doc.file.name, "rb") as fh:
                self.assertEqual(hashlib.sha256(fh.read()).hexdigest(), expected_sha)

    def test_3_image_destination_collision_and_repeat_import_safe(self):
        with override_settings(MEDIA_ROOT=self.media_dir.name):
            branch = Branch.objects.create(name="Col Branch", latitude=Decimal("23.81"), longitude=Decimal("90.41"))
            u = User.objects.create_user(email="col@example.com", password="pass", role="staff")
            emp = Employee.objects.create(
                employee_number="COL-001", first_name="Col", last_name="User", branch=branch, user=u
            )
            p = EmployeeProfile.objects.create(
                user=u,
                master_employee=emp,
                employee_id="COL-001",
                full_name="Col User",
                phone="01744440004",
                branch=branch,
                joined_date=datetime.date(2025, 1, 1),
                profile_photo=SimpleUploadedFile("col.png", _make_png_bytes("red"), content_type="image/png"),
            )
            orig_path = p.profile_photo.name
            with default_storage.open(orig_path, "rb") as fh:
                orig_sha = hashlib.sha256(fh.read()).hexdigest()

            zip_path = os.path.join(self.tmp_dir.name, "col_photo.zip")
            export_portable_archive(zip_path)
            sha = backup_importer.compute_file_sha256(zip_path)

            default_storage.delete(orig_path)
            self._clear_rows()

            # Existing unrelated file at destination path must not be overwritten
            unrelated_bytes = b"UNRELATED_DESTINATION_IMAGE_BYTES"
            default_storage.save(orig_path, ContentFile(unrelated_bytes))

            res1 = backup_importer.execute_additive_import(zip_path, expected_sha256=sha)
            self.assertEqual(res1["media_placed"], 1)

            restored_p = EmployeeProfile.objects.get(employee_id="COL-001")
            self.assertNotEqual(restored_p.profile_photo.name, orig_path)
            with default_storage.open(orig_path, "rb") as fh:
                self.assertEqual(fh.read(), unrelated_bytes)
            with default_storage.open(restored_p.profile_photo.name, "rb") as fh:
                self.assertEqual(hashlib.sha256(fh.read()).hexdigest(), orig_sha)

            # Repeat import must succeed with 0 creates, 0 conflicts, and 0 media_placed
            preview2 = backup_importer.validate_and_preview_archive(zip_path)
            self.assertTrue(preview2["ready_for_additive_import"])
            self.assertEqual(preview2["totals"]["conflicts"], 0)
            res2 = backup_importer.execute_additive_import(zip_path, expected_sha256=sha)
            self.assertEqual(res2["created_total"], 0)
            self.assertEqual(res2["media_placed"], 0)
