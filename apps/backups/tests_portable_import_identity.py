import datetime
import json
import os
import tempfile
import uuid
import zipfile
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import Sum
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.attendance.models import Attendance, AttendanceActivityLog
from apps.backups import importer as backup_importer
from apps.backups.exporter import export_portable_archive, inventory_models
from apps.branches.models import Branch
from apps.employees.models import Employee, EmployeeDocument, EmployeeProfile
from apps.leave.models import LeaveBalance, LeaveType
from apps.payroll.models import EmployeePayrollCalculation, PayrollConfiguration, PayrollRun
from apps.tenants.models import Tenant

User = get_user_model()


class PortableImportIdentityAndDuplicateTests(TestCase):
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

    def _clear_all_business_rows(self):
        AttendanceActivityLog.objects.all().delete()
        Attendance.objects.all().delete()
        EmployeePayrollCalculation.objects.all().delete()
        PayrollRun.objects.all().delete()
        PayrollConfiguration.objects.all().delete()
        LeaveBalance.objects.all().delete()
        LeaveType.objects.all().delete()
        EmployeeDocument.objects.all().delete()
        EmployeeProfile.objects.all().delete()
        Employee.objects.all().delete()
        Branch.objects.all().delete()
        User.objects.all().delete()
        Tenant.objects.all().delete()

    def test_1_multiple_attendance_sessions_same_day_repeat_import_and_missing_sync_uuid(self):
        tenant, _ = Tenant.objects.get_or_create(slug="id-tenant", defaults={"name": "ID Tenant"})
        branch = Branch.objects.create(name="ID Branch", latitude=Decimal("23.81"), longitude=Decimal("90.41"))
        user = User.objects.create_user(email="id_staff@example.com", password="pass", role="staff")
        emp = Employee.objects.create(
            employee_number="ID-001",
            first_name="Session",
            last_name="Tester",
            branch=branch,
            user=user,
        )
        profile = EmployeeProfile.objects.create(
            user=user,
            master_employee=emp,
            employee_id="ID-001",
            full_name="Session Tester",
            phone="01711110001",
            branch=branch,
            joined_date=datetime.date(2025, 1, 1),
        )
        session_date = datetime.date(2026, 11, 20)
        t_morning = timezone.now().replace(hour=9, minute=0, second=0, microsecond=0)
        t_afternoon = timezone.now().replace(hour=14, minute=0, second=0, microsecond=0)

        s1 = Attendance.objects.create(
            employee=profile,
            date=session_date,
            check_in_time=t_morning,
            total_hours=Decimal("4.00"),
            status="on_time",
            attendance_type="check_in",
        )
        s2 = Attendance.objects.create(
            employee=profile,
            date=session_date,
            check_in_time=t_afternoon,
            total_hours=Decimal("4.50"),
            status="on_time",
            attendance_type="check_in",
        )

        lt = LeaveType.objects.create(name="ID Annual", default_days_per_year=20)
        LeaveBalance.objects.create(
            employee=profile, leave_type=lt, year=2026, total_days=Decimal("20.00"), used_days=Decimal("2.00")
        )
        pcfg = PayrollConfiguration.objects.create(tenant=tenant, notes="ID Config")
        prun = PayrollRun.objects.create(
            name="ID Nov 2026",
            period_start=datetime.date(2026, 11, 1),
            period_end=datetime.date(2026, 11, 30),
            configuration=pcfg,
        )
        EmployeePayrollCalculation.objects.create(
            payroll_run=prun,
            employee=emp,
            gross_salary=Decimal("55000.25"),
            payment_mode="bank",
            total_earnings=Decimal("55000.25"),
            total_deductions=Decimal("1250.50"),
            net_payable=Decimal("53749.75"),
            bank_payable=Decimal("53749.75"),
            cash_payable=Decimal("0.00"),
            structure_snapshot={"basic": "40000.00"},
        )

        zip_path = os.path.join(self.tmp_dir.name, "multi_session.zip")
        export_portable_archive(zip_path)
        sha = backup_importer.compute_file_sha256(zip_path)
        src_emp_pk = emp.pk

        self._clear_all_business_rows()

        # Introduce a source PK collision at src_emp_pk
        local_branch = Branch.objects.create(name="Local Collision Branch", latitude=Decimal("23.70"), longitude=Decimal("90.30"))
        Employee.objects.bulk_create([
            Employee(
                id=src_emp_pk,
                employee_number="LOCAL-COLLISION",
                first_name="Local",
                last_name="Row",
                branch=local_branch,
            )
        ])

        # First additive import
        res1 = backup_importer.execute_additive_import(zip_path, expected_sha256=sha)
        self.assertEqual(res1["status"], "completed")
        sessions = Attendance.objects.filter(employee__employee_id="ID-001", date=session_date)
        self.assertEqual(sessions.count(), 2)
        self.assertEqual(
            set(sessions.values_list("sync_uuid", flat=True)),
            {s1.sync_uuid, s2.sync_uuid},
        )
        self.assertEqual(sessions.aggregate(s=Sum("total_hours"))["s"], Decimal("8.50"))
        self.assertEqual(
            EmployeePayrollCalculation.objects.aggregate(s=Sum("net_payable"))["s"],
            Decimal("53749.75"),
        )
        self.assertEqual(
            LeaveBalance.objects.get(employee__employee_id="ID-001", year=2026).used_days,
            Decimal("2.00"),
        )

        # Repeat import must be a clean no-op with both sessions preserved
        res2 = backup_importer.execute_additive_import(zip_path, expected_sha256=sha)
        self.assertEqual(res2["created_total"], 0)
        self.assertEqual(Attendance.objects.filter(employee__employee_id="ID-001", date=session_date).count(), 2)
        self.assertEqual(Employee.objects.count(), 2)
        self.assertEqual(
            EmployeePayrollCalculation.objects.aggregate(s=Sum("net_payable"))["s"],
            Decimal("53749.75"),
        )

        # Missing sync_uuid on Attendance must fail closed before writes
        self._clear_all_business_rows()
        bad_zip = os.path.join(self.tmp_dir.name, "missing_sync_uuid.zip")
        with zipfile.ZipFile(zip_path, "r") as src, zipfile.ZipFile(bad_zip, "w", compression=zipfile.ZIP_DEFLATED) as dst:
            manifest = json.loads(src.read("manifest.json").decode("utf-8"))
            for name in src.namelist():
                if name == "manifest.json":
                    continue
                data = src.read(name)
                if name.startswith("data/attendance.Attendance/"):
                    import hashlib
                    lines = []
                    for line in data.decode("utf-8").splitlines():
                        rec = json.loads(line)
                        rec["fields"].pop("sync_uuid", None)
                        lines.append(json.dumps(rec))
                    data = "\n".join(lines).encode("utf-8")
                    for c in manifest["models"]["attendance.Attendance"]["chunks"]:
                        if c["path"] == name:
                            c["sha256"] = hashlib.sha256(data).hexdigest()
                dst.writestr(name, data)
            dst.writestr("manifest.json", json.dumps(manifest).encode("utf-8"))

        bad_preview = backup_importer.validate_and_preview_archive(bad_zip)
        self.assertFalse(bad_preview["valid"])
        self.assertGreaterEqual(bad_preview["totals"]["missing_identities"], 1)
        bad_sha = backup_importer.compute_file_sha256(bad_zip)
        with self.assertRaises(backup_importer.ImportConflictError):
            backup_importer.execute_additive_import(bad_zip, expected_sha256=bad_sha)
        self.assertEqual(Branch.objects.count(), 0)
        self.assertEqual(Attendance.objects.count(), 0)

    def test_2_ambiguous_identity_cross_record_and_multiple_matches_rejected(self):
        branch = Branch.objects.create(name="Amb Branch", latitude=Decimal("23.81"), longitude=Decimal("90.41"))
        emp_a = Employee.objects.create(
            employee_number="AMB-A",
            first_name="Alice",
            last_name="One",
            branch=branch,
        )
        emp_b = Employee.objects.create(
            employee_number="AMB-B",
            first_name="Bob",
            last_name="Two",
            branch=branch,
        )

        zip_path = os.path.join(self.tmp_dir.name, "amb_cross.zip")
        export_portable_archive(zip_path)

        # Mutate archive so emp_a's record carries emp_a.employee_number AND emp_b.uuid
        cross_zip = os.path.join(self.tmp_dir.name, "amb_cross_tampered.zip")
        with zipfile.ZipFile(zip_path, "r") as src, zipfile.ZipFile(cross_zip, "w", compression=zipfile.ZIP_DEFLATED) as dst:
            import hashlib
            manifest = json.loads(src.read("manifest.json").decode("utf-8"))
            for name in src.namelist():
                if name == "manifest.json":
                    continue
                data = src.read(name)
                if name.startswith("data/employees.Employee/"):
                    lines = []
                    for line in data.decode("utf-8").splitlines():
                        rec = json.loads(line)
                        if rec["fields"]["employee_number"] == "AMB-A":
                            rec["fields"]["uuid"] = str(emp_b.uuid)
                        lines.append(json.dumps(rec))
                    data = "\n".join(lines).encode("utf-8")
                    for c in manifest["models"]["employees.Employee"]["chunks"]:
                        if c["path"] == name:
                            c["sha256"] = hashlib.sha256(data).hexdigest()
                dst.writestr(name, data)
            dst.writestr("manifest.json", json.dumps(manifest).encode("utf-8"))

        preview = backup_importer.validate_and_preview_archive(cross_zip)
        self.assertFalse(preview["valid"])
        self.assertFalse(preview["ready_for_additive_import"])
        self.assertGreaterEqual(preview["totals"]["ambiguous_identities"], 1)

        cross_sha = backup_importer.compute_file_sha256(cross_zip)
        with self.assertRaises(backup_importer.ImportConflictError):
            backup_importer.execute_additive_import(cross_zip, expected_sha256=cross_sha)

        # Also test single rule matching multiple destination rows (duplicate Branch name)
        self._clear_all_business_rows()
        Branch.objects.create(name="Dup Branch", latitude=Decimal("23.81"), longitude=Decimal("90.41"))
        single_zip = os.path.join(self.tmp_dir.name, "single_branch.zip")
        export_portable_archive(single_zip)

        Branch.objects.create(name="Dup Branch", latitude=Decimal("23.82"), longitude=Decimal("90.42"))
        dup_preview = backup_importer.validate_and_preview_archive(single_zip)
        self.assertFalse(dup_preview["valid"])
        self.assertGreaterEqual(dup_preview["totals"]["ambiguous_identities"], 1)

        dup_sha = backup_importer.compute_file_sha256(single_zip)
        with self.assertRaises(backup_importer.ImportConflictError):
            backup_importer.execute_additive_import(single_zip, expected_sha256=dup_sha)
        self.assertEqual(Branch.objects.filter(name="Dup Branch").count(), 2)

    def test_3_unsupported_identity_model_fails_closed_zero_writes(self):
        branch = Branch.objects.create(name="Unsup Branch", latitude=Decimal("23.81"), longitude=Decimal("90.41"))
        user = User.objects.create_user(email="unsup@example.com", password="pass", role="staff")
        emp = Employee.objects.create(
            employee_number="UNS-001", first_name="Unsup", last_name="User", branch=branch, user=user
        )
        profile = EmployeeProfile.objects.create(
            user=user,
            master_employee=emp,
            employee_id="UNS-001",
            full_name="Unsup User",
            phone="01722220001",
            branch=branch,
            joined_date=datetime.date(2025, 1, 1),
        )
        AttendanceActivityLog.objects.create(
            employee=profile,
            action="check_in",
            description="Checked in at 09:00 AM",
        )

        zip_path = os.path.join(self.tmp_dir.name, "unsupported.zip")
        export_portable_archive(zip_path)
        sha = backup_importer.compute_file_sha256(zip_path)
        self._clear_all_business_rows()

        preview = backup_importer.validate_and_preview_archive(zip_path)
        self.assertFalse(preview["valid"])
        self.assertFalse(preview["ready_for_additive_import"])
        self.assertEqual(preview["totals"]["unsupported_identities"], 1)
        self.assertEqual(preview["per_model"]["attendance.AttendanceActivityLog"]["creates"], 0)
        self.assertEqual(preview["per_model"]["attendance.AttendanceActivityLog"]["unsupported_identities"], 1)

        with self.assertRaises(backup_importer.ImportConflictError):
            backup_importer.execute_additive_import(zip_path, expected_sha256=sha)

        # Zero DB writes across all models
        self.assertEqual(Branch.objects.count(), 0)
        self.assertEqual(Employee.objects.count(), 0)
        self.assertEqual(AttendanceActivityLog.objects.count(), 0)

    def test_4_media_filename_remapping_survives_repeat_import(self):
        with override_settings(MEDIA_ROOT=self.media_dir.name):
            branch = Branch.objects.create(name="Media Branch", latitude=Decimal("23.81"), longitude=Decimal("90.41"))
            user = User.objects.create_user(email="media@example.com", password="pass", role="staff")
            emp = Employee.objects.create(
                employee_number="MED-001", first_name="Media", last_name="Test", branch=branch, user=user
            )
            profile = EmployeeProfile.objects.create(
                user=user,
                master_employee=emp,
                employee_id="MED-001",
                full_name="Media Test",
                phone="01733330001",
                branch=branch,
                joined_date=datetime.date(2025, 1, 1),
            )
            doc_bytes = b"%PDF-1.4\nRemapped Media Content\n%%EOF"
            doc = EmployeeDocument.objects.create(
                employee=profile,
                document_type="contract",
                title="Media Contract",
                file=SimpleUploadedFile("media_contract.pdf", doc_bytes, content_type="application/pdf"),
                uploaded_by=user,
            )
            original_storage_name = doc.file.name

            zip_path = os.path.join(self.tmp_dir.name, "media_remap.zip")
            export_portable_archive(zip_path)
            sha = backup_importer.compute_file_sha256(zip_path)

            if default_storage.exists(original_storage_name):
                default_storage.delete(original_storage_name)
            self._clear_all_business_rows()

            # Place an unrelated file at original_storage_name to force remapping on first import
            unrelated_bytes = b"UNRELATED_EXISTING_DOCUMENT"
            default_storage.save(original_storage_name, ContentFile(unrelated_bytes))

            res1 = backup_importer.execute_additive_import(zip_path, expected_sha256=sha)
            self.assertEqual(res1["status"], "completed")
            restored_doc = EmployeeDocument.objects.get(title="Media Contract")
            self.assertNotEqual(restored_doc.file.name, original_storage_name)

            with default_storage.open(original_storage_name, "rb") as fh:
                self.assertEqual(fh.read(), unrelated_bytes)
            with default_storage.open(restored_doc.file.name, "rb") as fh:
                self.assertEqual(fh.read(), doc_bytes)

            # Repeat import must classify as exact_duplicate (no false conflict on renamed media)
            preview2 = backup_importer.validate_and_preview_archive(zip_path)
            self.assertTrue(preview2["valid"])
            self.assertTrue(preview2["ready_for_additive_import"])
            self.assertEqual(preview2["totals"]["conflicts"], 0)
            self.assertEqual(preview2["totals"]["creates"], 0)
            self.assertEqual(preview2["totals"]["exact_duplicates"], preview2["totals"]["records"])

            res2 = backup_importer.execute_additive_import(zip_path, expected_sha256=sha)
            self.assertEqual(res2["created_total"], 0)
            self.assertEqual(EmployeeDocument.objects.count(), 1)

    def test_5_every_exported_model_explicitly_classified(self):
        included_models, _, _ = inventory_models()
        included_labels = {m._meta.label for m in included_models}
        classified = set(backup_importer.STABLE_IDENTITY_RULES.keys()) | set(
            backup_importer.UNSUPPORTED_IDENTITY_MODELS.keys()
        )
        self.assertEqual(included_labels - classified, set())
        self.assertEqual(
            backup_importer.STABLE_IDENTITY_RULES["attendance.Attendance"],
            [("sync_uuid",)],
        )
