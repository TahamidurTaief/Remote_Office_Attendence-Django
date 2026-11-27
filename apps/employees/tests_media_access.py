import importlib
import tempfile
from datetime import date
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import clear_url_caches, reverse

import fieldtrack.urls as root_urls
from apps.accounts.engine import PermissionEngine
from apps.accounts.rbac_models import (
    Action,
    DataScope,
    Module,
    Permission,
    Role,
    RolePermission,
    UserRoleAssignment,
)
from apps.branches.models import Branch
from apps.employees.models import (
    Department,
    Designation,
    DocumentDownloadLog,
    DocumentType,
    Employee,
    EmployeeDocument,
    EmployeeProfile,
    EmployeeStatus,
)
from apps.expense.models import Expense, ExpenseCategory
from apps.notifications.models import AuditLog

User = get_user_model()


class PrivateMediaAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls._temp_media = tempfile.TemporaryDirectory()
        cls.branch_a = Branch.objects.create(
            name="Media Branch A", latitude=23.81, longitude=90.41, radius_meters=200
        )
        cls.branch_b = Branch.objects.create(
            name="Media Branch B", latitude=23.82, longitude=90.42, radius_meters=200
        )
        cls.dept = Department.objects.create(name="Media Dept", is_global=True)
        cls.desig = Designation.objects.create(name="Officer")

        cls.user_a = User.objects.create_user(
            email="media_a@example.com", password="pass", role="staff"
        )
        cls.emp_a = Employee.objects.create(
            user=cls.user_a,
            employee_number="MED-A",
            first_name="Alice",
            last_name="A",
            phone="01710000001",
            branch=cls.branch_a,
            department=cls.dept,
            designation=cls.desig,
            joined_date=date(2024, 1, 1),
            status=EmployeeStatus.ACTIVE,
        )
        cls.prof_a = EmployeeProfile.objects.create(
            user=cls.user_a,
            master_employee=cls.emp_a,
            employee_id="MED-A",
            full_name="Alice A",
            phone="01710000001",
            branch=cls.branch_a,
            is_active=True,
            joined_date=date(2024, 1, 1),
        )

        cls.user_b = User.objects.create_user(
            email="media_b@example.com", password="pass", role="staff"
        )
        cls.emp_b = Employee.objects.create(
            user=cls.user_b,
            employee_number="MED-B",
            first_name="Bob",
            last_name="B",
            phone="01710000002",
            branch=cls.branch_b,
            department=cls.dept,
            designation=cls.desig,
            joined_date=date(2024, 1, 1),
            status=EmployeeStatus.ACTIVE,
        )
        cls.prof_b = EmployeeProfile.objects.create(
            user=cls.user_b,
            master_employee=cls.emp_b,
            employee_id="MED-B",
            full_name="Bob B",
            phone="01710000002",
            branch=cls.branch_b,
            is_active=True,
            joined_date=date(2024, 1, 1),
        )

        # Grant user_a branch-scoped employees.view + employees.download_sensitive_document
        mod, _ = Module.objects.get_or_create(code="employees", defaults={"name": "Employees"})
        act_view, _ = Action.objects.get_or_create(code="view", defaults={"name": "View"})
        perm_view, _ = Permission.objects.get_or_create(
            codename="employees.view",
            defaults={"module": mod, "action": act_view, "name": "View Employees"},
        )
        act_sens, _ = Action.objects.get_or_create(
            code="download_sensitive_document",
            defaults={"name": "Download Sensitive Document"},
        )
        perm_sens, _ = Permission.objects.get_or_create(
            codename="employees.download_sensitive_document",
            defaults={"module": mod, "action": act_sens, "name": "Download Sensitive Document"},
        )
        role_branch = Role.objects.create(name="Branch HR Media Test")
        RolePermission.objects.create(role=role_branch, permission=perm_view, data_scope=DataScope.BRANCH)
        RolePermission.objects.create(role=role_branch, permission=perm_sens, data_scope=DataScope.BRANCH)
        UserRoleAssignment.objects.create(user=cls.user_a, role=role_branch)
        PermissionEngine.invalidate_user_cache(cls.user_a)

    @classmethod
    def tearDownClass(cls):
        cls._temp_media.cleanup()
        super().tearDownClass()

    def setUp(self):
        self.media_override = override_settings(MEDIA_ROOT=self._temp_media.name)
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)

    def _create_doc(self, emp_master, emp_profile, filename="nid_secret.pdf", content=b"%PDF-secret-bytes", title='NID "Test"\r\n'):
        return EmployeeDocument.objects.create(
            employee_master=emp_master,
            employee=emp_profile,
            document_type=DocumentType.NID,
            title=title,
            file=SimpleUploadedFile(filename, content, content_type="application/pdf"),
        )

    def test_1_anonymous_direct_media_blocked_in_debug_and_non_debug(self):
        doc = self._create_doc(self.emp_a, self.prof_a, filename="anon_doc.pdf", content=b"PRIVATE_DOC_BYTES")
        cat, _ = ExpenseCategory.objects.get_or_create(name="Travel")
        exp = Expense.objects.create(
            employee=self.prof_a,
            category=cat,
            amount=100,
            description="Taxi",
            attachment=SimpleUploadedFile("receipt.txt", b"PRIVATE_RECEIPT_BYTES", content_type="text/plain"),
        )

        for debug_flag in (False, True):
            with override_settings(DEBUG=debug_flag):
                importlib.reload(root_urls)
                clear_url_caches()
                for url in (doc.file.url, exp.attachment.url):
                    resp = self.client.get(url)
                    self.assertEqual(resp.status_code, 403)
                    self.assertNotIn(b"PRIVATE_", resp.content)

    def test_2_authorized_owner_downloads_document_streamed_audited_and_bounded(self):
        payload = b"OWNER_DOCUMENT_CONTENT_12345"
        doc = self._create_doc(self.emp_a, self.prof_a, filename="owner_nid.pdf", content=payload)
        self.client.force_login(self.user_a)

        url = reverse("employees:document_download", kwargs={"pk": doc.pk})
        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.streaming)
        body = b"".join(resp.streaming_content)
        self.assertEqual(body, payload)

        cache_ctrl = resp.get("Cache-Control", "")
        self.assertIn("private", cache_ctrl)
        self.assertIn("no-store", cache_ctrl)
        self.assertEqual(resp.get("X-Content-Type-Options"), "nosniff")

        disp = resp.get("Content-Disposition", "")
        self.assertIn("attachment", disp)
        self.assertNotIn("\r", disp)
        self.assertNotIn("\n", disp)
        self.assertIn(".pdf", disp)

        self.assertTrue(
            DocumentDownloadLog.objects.filter(document=doc, downloaded_by=self.user_a).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(actor=self.user_a, action="document_downloaded", target_id=str(doc.pk)).exists()
        )
        self.assertLessEqual(len(ctx), 15)

    def test_3_out_of_scope_user_with_permission_cannot_download(self):
        doc_b = self._create_doc(self.emp_b, self.prof_b, filename="branch_b_nid.pdf", content=b"BRANCH_B_SECRET")
        self.client.force_login(self.user_a)

        # Direct media URL blocked
        direct_resp = self.client.get(doc_b.file.url)
        self.assertEqual(direct_resp.status_code, 403)
        self.assertNotIn(b"BRANCH_B_SECRET", direct_resp.content)

        # Download view blocked despite having employees.view + employees.download_sensitive_document
        view_resp = self.client.get(reverse("employees:document_download", kwargs={"pk": doc_b.pk}))
        self.assertEqual(view_resp.status_code, 403)
        self.assertNotIn(b"BRANCH_B_SECRET", view_resp.content)
        self.assertTrue(
            AuditLog.objects.filter(actor=self.user_a, action="document_access_denied", target_id=str(doc_b.pk)).exists()
        )

    def test_4_encoded_traversal_characters_rejected(self):
        self.client.force_login(self.user_a)
        traversal_paths = [
            "/media/employees/documents/..%2f..%2fsettings.py",
            "/media/%2e%2e/%2e%2e/fieldtrack/settings.py",
            "/media/employees/documents/%252e%252e%252fsecret.txt",
            "/media/employees/documents/..%5c..%5csecret.txt",
        ]
        for path in traversal_paths:
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 403, msg=f"Expected 403 for {path}")
            self.assertEqual(resp.content, b"Invalid media path.")

    def test_5_public_assets_remain_accessible(self):
        photos_dir = Path(self._temp_media.name) / "employees" / "photos"
        photos_dir.mkdir(parents=True, exist_ok=True)
        avatar_bytes = b"PUBLIC_AVATAR_WEBP_BYTES"
        (photos_dir / "avatar_public.webp").write_bytes(avatar_bytes)

        resp = self.client.get("/media/employees/photos/avatar_public.webp")
        self.assertEqual(resp.status_code, 200)
        body = b"".join(resp.streaming_content) if resp.streaming else resp.content
        self.assertEqual(body, avatar_bytes)
