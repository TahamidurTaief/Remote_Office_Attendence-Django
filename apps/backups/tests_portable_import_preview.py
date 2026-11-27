import datetime
import hashlib
import json
import os
import tempfile
import zipfile
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.attendance.models import Attendance
from apps.backups import importer as backup_importer
from apps.backups.exporter import export_portable_archive
from apps.branches.models import Branch
from apps.employees.models import Employee, EmployeeProfile
from apps.payroll.models import PayrollConfiguration, PayrollRun
from apps.tenants.models import Tenant

User = get_user_model()


class PortableArchiveValidationPreviewTests(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.staging_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)
        self.addCleanup(self.staging_dir.cleanup)
        self.patch_staging = patch.object(backup_importer, "STAGING_DIR", self.staging_dir.name)
        self.patch_staging.start()
        self.addCleanup(self.patch_staging.stop)

        self.admin = User.objects.create_superuser(
            email="prev_admin@example.com", password="pass", role="admin"
        )
        self.other_admin = User.objects.create_superuser(
            email="prev_admin2@example.com", password="pass", role="admin"
        )
        self.staff = User.objects.create_user(
            email="prev_staff@example.com", password="pass", role="staff"
        )

    def _seed_sample_data(self):
        tenant, _ = Tenant.objects.get_or_create(slug="prev-tenant", defaults={"name": "Prev Tenant"})
        branch = Branch.objects.create(name="Preview Branch", latitude=Decimal("23.81"), longitude=Decimal("90.41"))
        emp = Employee.objects.create(
            employee_number="PRV-001",
            first_name="Alan",
            last_name="Turing",
            branch=branch,
            user=self.staff,
        )
        profile = EmployeeProfile.objects.create(
            user=self.staff,
            master_employee=emp,
            employee_id="PRV-001",
            full_name="Alan Turing",
            phone="01755550001",
            branch=branch,
            joined_date=datetime.date(2025, 1, 1),
        )
        Attendance.objects.create(
            employee=profile,
            date=datetime.date(2026, 11, 20),
            check_in_time=timezone.now(),
            total_hours=Decimal("8.00"),
            status="present",
        )
        cfg = PayrollConfiguration.objects.create(tenant=tenant, notes="Prev Config")
        PayrollRun.objects.create(
            name="Prev Run",
            period_start=datetime.date(2026, 11, 1),
            period_end=datetime.date(2026, 11, 30),
            configuration=cfg,
        )
        return branch, emp, profile

    def test_1_zero_business_writes_identical_archive_duplicates_and_auth_binding(self):
        self._seed_sample_data()
        zip_path = os.path.join(self.tmp_dir.name, "identical.zip")
        export_portable_archive(zip_path)

        # Verify zero INSERT/UPDATE/DELETE SQL statements during validation/preview
        with CaptureQueriesContext(connection) as ctx:
            preview = backup_importer.validate_and_preview_archive(zip_path)

        write_sql = [
            q["sql"]
            for q in ctx.captured_queries
            if q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        ]
        self.assertEqual(write_sql, [])
        self.assertTrue(preview["valid"])
        self.assertTrue(preview["ready_for_additive_import"])
        self.assertEqual(preview["totals"]["creates"], 0)
        self.assertEqual(preview["totals"]["conflicts"], 0)
        self.assertEqual(preview["totals"]["unresolved_references"], 0)
        self.assertEqual(preview["totals"]["exact_duplicates"], preview["totals"]["records"])

        # CSRF enforcement
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        with open(zip_path, "rb") as fh:
            csrf_resp = csrf_client.post(
                reverse("backups:preview_import"),
                {"archive": SimpleUploadedFile("identical.zip", fh.read(), content_type="application/zip")},
            )
        self.assertEqual(csrf_resp.status_code, 403)

        # Non-admin denied
        self.client.force_login(self.staff)
        with open(zip_path, "rb") as fh:
            staff_resp = self.client.post(
                reverse("backups:preview_import"),
                {"archive": SimpleUploadedFile("identical.zip", fh.read(), content_type="application/zip")},
            )
        self.assertEqual(staff_resp.status_code, 403)

        # Admin upload -> preview bound to user and digest
        self.client.force_login(self.admin)
        with open(zip_path, "rb") as fh:
            ok_resp = self.client.post(
                reverse("backups:preview_import"),
                {"archive": SimpleUploadedFile("identical.zip", fh.read(), content_type="application/zip")},
            )
        self.assertEqual(ok_resp.status_code, 200)
        body = ok_resp.json()
        pid = body["preview_id"]
        sha = body["archive_sha256"]

        # Matching user + matching digest succeeds
        self.assertEqual(
            self.client.get(reverse("backups:preview_status", args=[pid]), {"sha256": sha}).status_code,
            200,
        )
        # Wrong digest rejected
        self.assertEqual(
            self.client.get(reverse("backups:preview_status", args=[pid]), {"sha256": "0" * 64}).status_code,
            404,
        )
        # Another admin cannot access preview bound to first admin
        self.client.force_login(self.other_admin)
        self.assertEqual(
            self.client.get(reverse("backups:preview_status", args=[pid]), {"sha256": sha}).status_code,
            404,
        )

    def test_2_hostile_archives_resource_limits_and_broken_fk_with_valid_checksum(self):
        # 1) Tiny ZIP bomb exceeding uncompressed/ratio limits stops immediately
        bomb_path = os.path.join(self.tmp_dir.name, "bomb.zip")
        with zipfile.ZipFile(bomb_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", b"A" * (512 * 1024))
        self.assertLess(os.path.getsize(bomb_path), 4096)

        with self.assertRaises(backup_importer.ArchiveLimitError):
            backup_importer.validate_and_preview_archive(
                bomb_path,
                limits=backup_importer.ArchiveLimits(
                    max_uncompressed_bytes=64 * 1024,
                    max_single_file_bytes=64 * 1024,
                    max_compression_ratio=20.0,
                ),
            )

        # 2) Entry count limit
        many_path = os.path.join(self.tmp_dir.name, "many.zip")
        with zipfile.ZipFile(many_path, "w") as zf:
            for i in range(6):
                zf.writestr(f"data/f_{i}.jsonl", "{}")
        with self.assertRaises(backup_importer.ArchiveLimitError):
            backup_importer.validate_and_preview_archive(
                many_path,
                limits=backup_importer.ArchiveLimits(max_entry_count=5),
            )

        # 3) Path traversal, absolute path, symlink, duplicate path, unsafe media
        for bad_name in ("../evil.json", "/etc/passwd"):
            p = os.path.join(self.tmp_dir.name, f"bad_{abs(hash(bad_name))}.zip")
            with zipfile.ZipFile(p, "w") as zf:
                zf.writestr(bad_name, "x")
            with self.assertRaises(backup_importer.ArchiveValidationError):
                backup_importer.validate_and_preview_archive(p)

        sym_path = os.path.join(self.tmp_dir.name, "symlink.zip")
        with zipfile.ZipFile(sym_path, "w") as zf:
            zi = zipfile.ZipInfo("media/link.txt")
            zi.external_attr = (0o120777 << 16)
            zf.writestr(zi, "target")
        with self.assertRaises(backup_importer.ArchiveValidationError):
            backup_importer.validate_and_preview_archive(sym_path)

        dup_path = os.path.join(self.tmp_dir.name, "dup.zip")
        with zipfile.ZipFile(dup_path, "w") as zf:
            zf.writestr("manifest.json", "{}")
            zf.writestr("manifest.json", "{}")
        with self.assertRaises(backup_importer.ArchiveValidationError):
            backup_importer.validate_and_preview_archive(dup_path)

        exe_path = os.path.join(self.tmp_dir.name, "unsafe_media.zip")
        with zipfile.ZipFile(exe_path, "w") as zf:
            zf.writestr("manifest.json", "{}")
            zf.writestr("media/payload.exe", b"MZ")
        with self.assertRaises(backup_importer.ArchiveValidationError):
            backup_importer.validate_and_preview_archive(exe_path)

        # 4) Valid checksums but broken FK reference
        branch, emp, _ = self._seed_sample_data()
        valid_zip = os.path.join(self.tmp_dir.name, "base.zip")
        export_portable_archive(valid_zip)

        broken_zip = os.path.join(self.tmp_dir.name, "broken_fk.zip")
        with zipfile.ZipFile(valid_zip, "r") as src, zipfile.ZipFile(broken_zip, "w", compression=zipfile.ZIP_DEFLATED) as dst:
            manifest = json.loads(src.read("manifest.json").decode("utf-8"))
            # Remove branches.Branch records so Employee.branch FK becomes broken
            removed_cnt = manifest["models"]["branches.Branch"]["record_count"]
            manifest["models"]["branches.Branch"]["record_count"] = 0
            manifest["models"]["branches.Branch"]["chunks"] = []
            manifest["totals"]["record_count"] -= removed_cnt

            for name in src.namelist():
                if name == "manifest.json" or name.startswith("data/branches.Branch/"):
                    continue
                dst.writestr(name, src.read(name))
            dst.writestr("manifest.json", json.dumps(manifest).encode("utf-8"))

        broken_preview = backup_importer.validate_and_preview_archive(broken_zip)
        self.assertFalse(broken_preview["valid"])
        self.assertFalse(broken_preview["ready_for_additive_import"])
        self.assertGreater(broken_preview["totals"]["unresolved_references"], 0)
        self.assertTrue(
            any(
                u["model"] == "employees.Employee"
                and any(m["field"] == "branch" for m in u["missing_references"])
                for u in broken_preview["unresolved_references"]
            )
        )

    def test_3_identity_conflicts_detected_without_overwriting_destination_records(self):
        branch, emp, profile = self._seed_sample_data()
        zip_path = os.path.join(self.tmp_dir.name, "before_conflict.zip")
        export_portable_archive(zip_path)

        # Mutate destination records so archive has same stable identity but conflicting attributes
        Employee.objects.filter(pk=emp.pk).update(first_name="Alan-Local-Modified")
        EmployeeProfile.objects.filter(pk=profile.pk).update(full_name="Alan Local Modified")

        preview = backup_importer.validate_and_preview_archive(zip_path)
        self.assertTrue(preview["valid"])
        self.assertFalse(preview["ready_for_additive_import"])
        self.assertGreaterEqual(preview["totals"]["conflicts"], 2)

        conflict_by_model = {c["model"]: c for c in preview["conflicts"]}
        self.assertIn("employees.Employee", conflict_by_model)
        self.assertIn("first_name", conflict_by_model["employees.Employee"]["differing_fields"])
        self.assertIn("employees.EmployeeProfile", conflict_by_model)
        self.assertIn("full_name", conflict_by_model["employees.EmployeeProfile"]["differing_fields"])

        # Destination records must remain completely untouched
        emp.refresh_from_db()
        profile.refresh_from_db()
        self.assertEqual(emp.first_name, "Alan-Local-Modified")
        self.assertEqual(profile.full_name, "Alan Local Modified")
