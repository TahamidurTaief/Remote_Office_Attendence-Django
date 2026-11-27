import datetime
import hashlib
import os
import tempfile
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import Sum
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.backups import importer as backup_importer
from apps.backups.exporter import export_portable_archive
from apps.branches.models import Branch
from apps.employees.models import Department, Designation, Employee, EmployeeDocument, EmployeeProfile
from apps.leave.models import LeaveBalance, LeaveRequest, LeaveType
from apps.payroll.models import EmployeePayrollCalculation, PayrollConfiguration, PayrollRun
from apps.projects.models import Project, ProjectType
from apps.tenants.models import Tenant

User = get_user_model()


class PortableArchiveAdditiveImportTests(TestCase):
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

        self.admin = User.objects.create_superuser(
            email="imp_admin@example.com", password="pass", role="admin"
        )

    def _create_source_fixture_and_export(self, zip_path):
        tenant, _ = Tenant.objects.get_or_create(slug="imp-tenant", defaults={"name": "Imp Tenant"})
        branch = Branch.objects.create(name="Imp Branch", latitude=Decimal("23.81"), longitude=Decimal("90.41"))
        dept = Department.objects.create(name="Imp Dept")
        desig = Designation.objects.create(name="Imp Engineer", department=dept)

        u1 = User.objects.create_user(email="imp_u1@example.com", password="pass", role="staff")
        u2 = User.objects.create_user(email="imp_u2@example.com", password="pass", role="staff")

        e1 = Employee.objects.create(
            employee_number="IMP-001",
            first_name="Ada",
            last_name="Lovelace",
            branch=branch,
            department=dept,
            designation=desig,
            user=u1,
        )
        e2 = Employee.objects.create(
            employee_number="IMP-002",
            first_name="Grace",
            last_name="Hopper",
            branch=branch,
            department=dept,
            designation=desig,
            user=u2,
            reporting_manager=e1,
        )
        Employee.objects.filter(pk=e1.pk).update(reporting_manager=e2)

        p1 = EmployeeProfile.objects.create(
            user=u1,
            master_employee=e1,
            employee_id="IMP-001",
            full_name="Ada Lovelace",
            phone="01788880001",
            branch=branch,
            joined_date=datetime.date(2025, 1, 1),
        )
        p2 = EmployeeProfile.objects.create(
            user=u2,
            master_employee=e2,
            employee_id="IMP-002",
            full_name="Grace Hopper",
            phone="01788880002",
            branch=branch,
            joined_date=datetime.date(2025, 1, 1),
        )

        doc_bytes = b"%PDF-1.4\nPortable Import Contract\n%%EOF"
        doc = EmployeeDocument.objects.create(
            employee=p1,
            document_type="contract",
            title="Ada Contract",
            file=SimpleUploadedFile("ada_contract.pdf", doc_bytes, content_type="application/pdf"),
            uploaded_by=u1,
        )

        lt = LeaveType.objects.create(name="Annual Leave Imp", default_days_per_year=20)
        LeaveBalance.objects.create(
            employee=p1, leave_type=lt, year=2026, total_days=Decimal("20.00"), used_days=Decimal("2.00")
        )
        # Insert LeaveRequest via bulk_create so initial balance stays 2.00
        LeaveRequest.objects.bulk_create([
            LeaveRequest(
                employee=p1,
                leave_type=lt,
                start_date=datetime.date(2026, 11, 10),
                end_date=datetime.date(2026, 11, 11),
                number_of_days=Decimal("2.00"),
                reason="Family trip",
                status="approved",
            )
        ])

        ptype = ProjectType.objects.create(name="Imp Infra")
        proj = Project.objects.create(
            name="Imp Bridge",
            client_name="City",
            project_type=ptype,
            start_date=datetime.date(2026, 1, 1),
            branch=branch,
            created_by=u1,
        )
        proj.project_managers.add(p1)
        proj.project_members.add(p1, p2)

        pcfg = PayrollConfiguration.objects.create(tenant=tenant, notes="Imp Config", created_by=u1)
        prun = PayrollRun.objects.create(
            name="Imp Nov 2026",
            period_start=datetime.date(2026, 11, 1),
            period_end=datetime.date(2026, 11, 30),
            configuration=pcfg,
        )
        EmployeePayrollCalculation.objects.create(
            payroll_run=prun,
            employee=e1,
            gross_salary=Decimal("55000.25"),
            payment_mode="bank",
            total_earnings=Decimal("55000.25"),
            total_deductions=Decimal("1250.50"),
            net_payable=Decimal("53749.75"),
            bank_payable=Decimal("53749.75"),
            cash_payable=Decimal("0.00"),
            structure_snapshot={"basic": "40000.00"},
        )

        export_portable_archive(zip_path)
        return {
            "e1_pk": e1.pk,
            "e2_pk": e2.pk,
            "doc_bytes": doc_bytes,
            "doc_name": doc.file.name,
        }

    def _clear_business_fixture(self):
        EmployeePayrollCalculation.objects.all().delete()
        PayrollRun.objects.all().delete()
        PayrollConfiguration.objects.all().delete()
        Project.objects.all().delete()
        ProjectType.objects.all().delete()
        LeaveRequest.objects.all().delete()
        LeaveBalance.objects.all().delete()
        LeaveType.objects.all().delete()
        EmployeeDocument.objects.all().delete()
        EmployeeProfile.objects.all().delete()
        Employee.objects.all().delete()
        Designation.objects.all().delete()
        Department.objects.all().delete()
        Branch.objects.all().delete()
        User.objects.exclude(pk=self.admin.pk).delete()
        Tenant.objects.filter(slug="imp-tenant").delete()
        AuditEvent.objects.all().delete()

    def test_1_round_trip_relationships_media_and_pk_remapping(self):
        with override_settings(MEDIA_ROOT=self.media_dir.name):
            csrf_client = Client(enforce_csrf_checks=True)
            csrf_client.force_login(self.admin)
            csrf_client.get(reverse("backups:backup_list"))
            csrf_token = csrf_client.cookies["csrftoken"].value

            zip_path = os.path.join(self.tmp_dir.name, "roundtrip.zip")
            src = self._create_source_fixture_and_export(zip_path)

            # Remove source media and business rows, then create a colliding local Employee at src['e1_pk']
            if default_storage.exists(src["doc_name"]):
                default_storage.delete(src["doc_name"])
            self._clear_business_fixture()

            local_branch = Branch.objects.create(name="Local Colliding Branch", latitude=23.7, longitude=90.3)
            colliding_emp = Employee(
                id=src["e1_pk"],
                employee_number="LOCAL-999",
                first_name="Local",
                last_name="Existing",
                branch=local_branch,
            )
            Employee.objects.bulk_create([colliding_emp])

            with open(zip_path, "rb") as fh:
                prev_resp = csrf_client.post(
                    reverse("backups:preview_import"),
                    {"archive": SimpleUploadedFile("roundtrip.zip", fh.read(), content_type="application/zip")},
                    HTTP_X_CSRFTOKEN=csrf_token,
                )
            self.assertEqual(prev_resp.status_code, 200)
            prev_data = prev_resp.json()
            sha = prev_data["archive_sha256"]
            pid = prev_data["preview_id"]

            exec_resp = csrf_client.post(
                reverse("backups:execute_import"),
                {"preview_id": pid, "archive_sha256": sha},
                HTTP_X_CSRFTOKEN=csrf_token,
            )
            self.assertEqual(exec_resp.status_code, 200, exec_resp.content.decode("utf-8"))
            res = exec_resp.json()
            self.assertEqual(res["status"], "completed")

            # Colliding local employee was NOT overwritten; imported Ada got a new PK
            colliding_emp.refresh_from_db()
            self.assertEqual(colliding_emp.employee_number, "LOCAL-999")
            ada = Employee.objects.get(employee_number="IMP-001")
            grace = Employee.objects.get(employee_number="IMP-002")
            self.assertNotEqual(ada.pk, src["e1_pk"])
            self.assertEqual(ada.reporting_manager_id, grace.pk)
            self.assertEqual(grace.reporting_manager_id, ada.pk)

            # Independent counts & exact monetary values
            self.assertEqual(Employee.objects.count(), 3)  # colliding_emp + Ada + Grace
            self.assertEqual(EmployeeProfile.objects.count(), 2)
            self.assertEqual(EmployeePayrollCalculation.objects.count(), 1)
            calc = EmployeePayrollCalculation.objects.get(employee=ada)
            self.assertEqual(calc.net_payable, Decimal("53749.75"))
            self.assertEqual(calc.total_deductions, Decimal("1250.50"))

            # No duplicate leave deduction side effect
            bal = LeaveBalance.objects.get(employee__employee_id="IMP-001", year=2026)
            self.assertEqual(bal.used_days, Decimal("2.00"))

            # M2M restored to remapped profiles
            proj = Project.objects.get(name="Imp Bridge")
            self.assertEqual(proj.project_members.count(), 2)
            self.assertEqual(proj.project_managers.count(), 1)

            # Media bytes restored accurately
            restored_doc = EmployeeDocument.objects.get(title="Ada Contract")
            with default_storage.open(restored_doc.file.name, "rb") as fh:
                self.assertEqual(fh.read(), src["doc_bytes"])

    def test_2_repeat_import_idempotency(self):
        with override_settings(MEDIA_ROOT=self.media_dir.name):
            zip_path = os.path.join(self.tmp_dir.name, "idempotent.zip")
            self._create_source_fixture_and_export(zip_path)
            sha = backup_importer.compute_file_sha256(zip_path)

            # Import the exact same archive into the already-populated DB
            res = backup_importer.execute_additive_import(zip_path, expected_sha256=sha)
            self.assertEqual(res["created_total"], 0)
            self.assertEqual(Employee.objects.count(), 2)
            self.assertEqual(EmployeeProfile.objects.count(), 2)
            self.assertEqual(EmployeeDocument.objects.count(), 1)
            self.assertEqual(LeaveRequest.objects.count(), 1)
            self.assertEqual(EmployeePayrollCalculation.objects.count(), 1)

            # Verify monetary & leave balance totals are unchanged
            total_net = EmployeePayrollCalculation.objects.aggregate(s=Sum("net_payable"))["s"]
            self.assertEqual(total_net, Decimal("53749.75"))
            bal = LeaveBalance.objects.get(employee__employee_id="IMP-001", year=2026)
            self.assertEqual(bal.used_days, Decimal("2.00"))

    def test_3_injected_failure_recovery_and_destination_change_after_preview(self):
        with override_settings(MEDIA_ROOT=self.media_dir.name):
            zip_path = os.path.join(self.tmp_dir.name, "recovery.zip")
            src = self._create_source_fixture_and_export(zip_path)
            sha = backup_importer.compute_file_sha256(zip_path)

            if default_storage.exists(src["doc_name"]):
                default_storage.delete(src["doc_name"])
            self._clear_business_fixture()

            # Place a pre-existing unrelated media file that must survive any rollback
            pre_existing_name = default_storage.save("employees/documents/keep_me.txt", ContentFile(b"DO_NOT_DELETE"))

            def _raise_after_media():
                raise RuntimeError("Injected failure after media placement")

            with self.assertRaises(RuntimeError):
                backup_importer.execute_additive_import(
                    zip_path,
                    expected_sha256=sha,
                    post_media_hook=_raise_after_media,
                )

            # Both DB and newly placed media rolled back; pre-existing media untouched
            self.assertEqual(Employee.objects.count(), 0)
            self.assertEqual(EmployeeDocument.objects.count(), 0)
            self.assertFalse(default_storage.exists(src["doc_name"]))
            self.assertTrue(default_storage.exists(pre_existing_name))

            # Preview succeeds, then destination mutates to introduce a conflict -> execute must abort (409)
            self.client.force_login(self.admin)
            with open(zip_path, "rb") as fh:
                prev_resp = self.client.post(
                    reverse("backups:preview_import"),
                    {"archive": SimpleUploadedFile("recovery.zip", fh.read(), content_type="application/zip")},
                )
            self.assertEqual(prev_resp.status_code, 200)
            pid = prev_resp.json()["preview_id"]

            # Destination changes after preview: conflicting IMP-001 created locally
            local_branch = Branch.objects.create(name="Conflict Branch", latitude=23.8, longitude=90.4)
            Employee.objects.create(
                employee_number="IMP-001",
                first_name="Conflicting",
                last_name="Local",
                branch=local_branch,
            )

            exec_resp = self.client.post(
                reverse("backups:execute_import"),
                {"preview_id": pid, "archive_sha256": sha},
            )
            self.assertEqual(exec_resp.status_code, 409)
            self.assertEqual(Employee.objects.get(employee_number="IMP-001").first_name, "Conflicting")
