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
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import PasswordResetOTP, UserSecurityProfile, UserSession
from apps.admin_panel.models import AISetting
from apps.attendance.models import Attendance
from apps.backups import utils as backup_utils
from apps.backups.exporter import (
    ARCHIVE_FORMAT,
    ARCHIVE_FORMAT_VERSION,
    ExportConsistencyError,
    IncompleteBackupError,
    create_portable_backup,
    export_portable_archive,
)
from apps.backups.models import BackupRecord, GoogleDriveConfig
from apps.branches.models import Branch
from apps.employees.models import Department, Designation, Employee, EmployeeDocument, EmployeeProfile
from apps.payroll.models import (
    EmployeePayrollCalculation,
    PayrollConfiguration,
    PayrollRun,
    SalaryComponent,
    SalaryStructure,
)
from apps.projects.models import Project, ProjectType
from apps.tenants.models import CompanyConfiguration, Tenant

User = get_user_model()


class PortableArchiveExportTests(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.media_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)
        self.addCleanup(self.media_dir.cleanup)
        self.patch_dir = patch.object(backup_utils, "BACKUP_DIR", self.tmp_dir.name)
        self.patch_cleanup = patch.object(backup_utils, "cleanup_old_backups")
        self.patch_dir.start()
        self.patch_cleanup.start()
        self.addCleanup(self.patch_dir.stop)
        self.addCleanup(self.patch_cleanup.stop)

    def test_1_coverage_exclusions_no_sidecars_and_concurrent_change_detection(self):
        tenant = Tenant.objects.create(name="Acme Corp", slug="acme-corp")
        CompanyConfiguration.objects.create(
            tenant=tenant,
            company_name="Acme Corp",
            api_secret="super-secret-tenant-key-123",
        )
        user = User.objects.create_user(
            email="cov_user@example.com",
            password="RawPassword!99",
            role="admin",
            active_session_key="sess-secret-xyz",
        )
        PasswordResetOTP.objects.create(
            user=user,
            otp_code="999888",
            reset_token="reset-secret-token",
            expires_at=timezone.now() + datetime.timedelta(minutes=10),
        )
        UserSession.objects.create(
            user=user,
            device_id="dev-1",
            session_key="active-session-key-abc",
        )
        UserSecurityProfile.objects.create(
            user=user,
            mfa_enabled=True,
            mfa_secret="JBSWY3DPEHPK3PXP",
            backup_codes=["code1", "code2"],
        )
        AISetting.objects.create(primary_api_key="sk-live-secret-ai-key")
        cfg = GoogleDriveConfig.get_config()
        cfg.service_account_json = '{"private_key": "secret-sa-key"}'
        cfg.save()

        record = create_portable_backup(backup_type="manual", created_by=user)
        self.assertEqual(record.status, "completed")
        self.assertTrue(record.file_name.endswith(".zip"))
        self.assertTrue(os.path.exists(record.file_path))

        # No SQLite or SQL sidecars generated for portable ZIP format
        self.assertFalse(os.path.exists(record.file_path.replace(".zip", ".sqlite3")))
        self.assertFalse(os.path.exists(record.file_path.replace(".zip", ".sql")))

        with zipfile.ZipFile(record.file_path, "r") as zf:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            self.assertEqual(manifest["format"], ARCHIVE_FORMAT)
            self.assertEqual(manifest["format_version"], ARCHIVE_FORMAT_VERSION)
            self.assertTrue(manifest["consistency"]["consistent"])
            self.assertTrue(manifest["completeness"]["all_business_modules_covered"])
            self.assertEqual(manifest["completeness"]["missing_business_modules"], [])

            for expected_app in (
                "tenants", "accounts", "audit", "employees", "attendance",
                "branches", "notifications", "leave", "projects",
                "schedule", "expense", "workflow", "payroll",
            ):
                self.assertIn(expected_app, manifest["completeness"]["business_apps_covered"])

            excluded = manifest["completeness"]["excluded_models"]
            for secret_model in (
                "accounts.PasswordResetOTP",
                "accounts.UserSession",
                "accounts.UserSecurityProfile",
                "backups.GoogleDriveConfig",
                "admin_panel.AISetting",
                "sessions.Session",
            ):
                self.assertIn(secret_model, excluded)
                self.assertNotIn(secret_model, manifest["models"])

            # Verify excluded fields are absent from exported JSONL
            archive_text = "".join(
                zf.read(name).decode("utf-8")
                for name in zf.namelist()
                if name.startswith("data/")
            )
            self.assertNotIn("super-secret-tenant-key-123", archive_text)
            self.assertNotIn("sess-secret-xyz", archive_text)
            self.assertNotIn("999888", archive_text)
            self.assertNotIn("JBSWY3DPEHPK3PXP", archive_text)
            self.assertNotIn("sk-live-secret-ai-key", archive_text)
            self.assertNotIn("secret-sa-key", archive_text)

        # Concurrent data change during export must fail visibly and never produce a completed archive
        def _mutate_during_export():
            Branch.objects.create(name="Concurrent Branch", latitude=23.7, longitude=90.3)

        with self.assertRaises(ExportConsistencyError):
            create_portable_backup(
                backup_type="manual",
                created_by=user,
                pre_finalize_hook=_mutate_during_export,
            )
        failed_rec = BackupRecord.objects.latest("id")
        self.assertEqual(failed_rec.status, "failed")
        self.assertIn("Inconsistent export", failed_rec.error_message)
        self.assertFalse(os.path.exists(failed_rec.file_path))

    def test_2_relationships_types_and_cyclic_fk_m2m_preservation(self):
        branch = Branch.objects.create(
            name="HQ Branch", latitude=Decimal("23.810300"), longitude=Decimal("90.412500"), radius_meters=150
        )
        dept = Department.objects.create(name="Engineering")
        dept.branches.add(branch)
        desig = Designation.objects.create(name="Lead Engineer", department=dept)

        user = User.objects.create_user(email="rel_emp@example.com", password="pass", role="staff")
        emp = Employee.objects.create(
            employee_number="EMP-100",
            first_name="Ada",
            last_name="Lovelace",
            personal_email="rel_emp@example.com",
            branch=branch,
            department=dept,
            designation=desig,
            user=user,
        )
        emp2 = Employee.objects.create(
            employee_number="EMP-101",
            first_name="Grace",
            last_name="Hopper",
            personal_email="rel_emp2@example.com",
            branch=branch,
            department=dept,
            designation=desig,
            reporting_manager=emp,
        )
        # Cyclic FK: emp -> emp2 -> emp
        Employee.objects.filter(pk=emp.pk).update(reporting_manager=emp2)

        profile = EmployeeProfile.objects.create(
            user=user,
            master_employee=emp,
            employee_id="EMP-100",
            full_name="Ada Lovelace",
            phone="01700000001",
            branch=branch,
            joined_date=datetime.date(2025, 1, 15),
        )

        # Timezone-aware attendance + NULL check_out_time
        check_in_dt = timezone.make_aware(datetime.datetime(2026, 11, 20, 9, 15, 30))
        att = Attendance.objects.create(
            employee=profile,
            date=datetime.date(2026, 11, 20),
            check_in_time=check_in_dt,
            check_out_time=None,
            total_hours=Decimal("8.75"),
            status="present",
        )

        # Project with M2M relationships
        ptype = ProjectType.objects.create(name="Infrastructure")
        proj = Project.objects.create(
            name="Bridge Alpha",
            client_name="City Corp",
            project_type=ptype,
            start_date=datetime.date(2026, 1, 1),
            branch=branch,
            created_by=user,
        )
        proj.project_managers.add(profile)
        proj.project_members.add(profile)

        # Payroll with exact Decimal values
        tenant, _ = Tenant.objects.get_or_create(slug="default-rel", defaults={"name": "Default Rel"})
        SalaryComponent.objects.create(name="Basic", code="BASIC", type="earning")
        SalaryStructure.objects.create(name="Standard")
        p_cfg = PayrollConfiguration.objects.create(tenant=tenant, notes="Std Config", created_by=user)
        p_run = PayrollRun.objects.create(
            name="Nov 2026 Run",
            period_start=datetime.date(2026, 11, 1),
            period_end=datetime.date(2026, 11, 30),
            configuration=p_cfg,
        )
        p_calc = EmployeePayrollCalculation.objects.create(
            payroll_run=p_run,
            employee=emp,
            gross_salary=Decimal("55000.25"),
            payment_mode="bank",
            total_earnings=Decimal("55000.25"),
            total_deductions=Decimal("1250.50"),
            net_payable=Decimal("53749.75"),
            bank_payable=Decimal("53749.75"),
            cash_payable=Decimal("0.00"),
            ot_hours=Decimal("4.50"),
            structure_snapshot={"basic": "40000.00"},
        )

        zip_path = os.path.join(self.tmp_dir.name, "rel_types.zip")
        manifest = export_portable_archive(zip_path, chunk_size=1)
        self.assertGreater(manifest["totals"]["record_count"], 0)

        with zipfile.ZipFile(zip_path, "r") as zf:
            def _load_records(model_label):
                rows = []
                for chunk in manifest["models"][model_label]["chunks"]:
                    for line in zf.read(chunk["path"]).decode("utf-8").splitlines():
                        rows.append(json.loads(line))
                return rows

            emp_rows = {r["pk"]: r for r in _load_records("employees.Employee")}
            self.assertEqual(
                emp_rows[emp.pk]["fields"]["reporting_manager"]["ref"],
                f"employees.Employee:{emp2.pk}",
            )
            self.assertEqual(
                emp_rows[emp2.pk]["fields"]["reporting_manager"]["ref"],
                f"employees.Employee:{emp.pk}",
            )

            att_rows = {r["pk"]: r for r in _load_records("attendance.Attendance")}
            att_fields = att_rows[att.pk]["fields"]
            self.assertIsNone(att_fields["check_out_time"])
            self.assertEqual(att_fields["total_hours"], "8.75")
            parsed_dt = datetime.datetime.fromisoformat(att_fields["check_in_time"])
            self.assertIsNotNone(parsed_dt.tzinfo)

            proj_rows = {r["pk"]: r for r in _load_records("projects.Project")}
            self.assertEqual(
                proj_rows[proj.pk]["m2m"]["project_managers"],
                [{"target_model": "employees.EmployeeProfile", "pk": profile.pk, "ref": f"employees.EmployeeProfile:{profile.pk}"}],
            )

            calc_rows = {r["pk"]: r for r in _load_records("payroll.EmployeePayrollCalculation")}
            self.assertEqual(calc_rows[p_calc.pk]["fields"]["net_payable"], "53749.75")
            self.assertEqual(calc_rows[p_calc.pk]["fields"]["ot_hours"], "4.50")

    def test_3_media_bytes_checksums_and_missing_document_failure(self):
        with override_settings(MEDIA_ROOT=self.media_dir.name):
            user = User.objects.create_user(email="media_emp@example.com", password="pass", role="staff")
            branch = Branch.objects.create(name="Media Branch", latitude=23.8, longitude=90.4)
            profile = EmployeeProfile.objects.create(
                user=user,
                employee_id="MED-001",
                full_name="Media User",
                phone="01711111111",
                branch=branch,
                joined_date=datetime.date(2025, 1, 1),
            )
            doc_bytes = b"%PDF-1.4\nSynthetic Employee Contract Bytes\n%%EOF"
            doc = EmployeeDocument.objects.create(
                employee=profile,
                document_type="contract",
                title="Signed Contract",
                file=SimpleUploadedFile("contract_med_001.pdf", doc_bytes, content_type="application/pdf"),
                uploaded_by=user,
            )

            record = create_portable_backup(backup_type="manual", created_by=user)
            self.assertEqual(record.status, "completed")

            with zipfile.ZipFile(record.file_path, "r") as zf:
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
                self.assertEqual(manifest["media"]["file_count"], 1)
                media_entry = manifest["media"]["files"][0]
                self.assertEqual(media_entry["size"], len(doc_bytes))
                self.assertEqual(media_entry["sha256"], hashlib.sha256(doc_bytes).hexdigest())
                self.assertEqual(zf.read(media_entry["archive_path"]), doc_bytes)

                # Verify every chunk SHA-256 matches actual bytes
                for model_meta in manifest["models"].values():
                    for chunk in model_meta["chunks"]:
                        chunk_bytes = zf.read(chunk["path"])
                        self.assertEqual(
                            hashlib.sha256(chunk_bytes).hexdigest(),
                            chunk["sha256"],
                        )

            # Now delete the referenced media file on disk -> must fail with IncompleteBackupError
            stored_path = os.path.join(self.media_dir.name, doc.file.name)
            os.remove(stored_path)

            with self.assertRaises(IncompleteBackupError):
                create_portable_backup(backup_type="manual", created_by=user)

            failed_rec = BackupRecord.objects.latest("id")
            self.assertEqual(failed_rec.status, "failed")
            self.assertIn("missing referenced media", failed_rec.error_message)
            self.assertFalse(os.path.exists(failed_rec.file_path))
