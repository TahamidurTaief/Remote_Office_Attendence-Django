from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import UserSession
from apps.audit.models import AuditAccessLog, AuditEvent, TrashEntry
from apps.audit.services import AuditService, TrashService
from apps.branches.models import Branch
from apps.employees.models import Department, Designation, Employee, EmployeeStatus

User = get_user_model()


class AuditTrashFoundationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.client.defaults["HTTP_X_FORWARDED_PROTO"] = "https"
        self.admin_password = "AdminPass123!"
        self.staff_password = "StaffPass123!"
        self.admin = User.objects.create_superuser(
            email="audit-admin@example.com",
            password=self.admin_password,
            role="admin",
        )
        self.staff = User.objects.create_user(
            email="audit-staff@example.com",
            password=self.staff_password,
            role="staff",
        )
        self.other = User.objects.create_user(
            email="audit-other@example.com",
            password="OtherPass123!",
            role="staff",
        )
        self.branch = Branch.objects.create(name="Audit Branch", latitude=23.7, longitude=90.4)
        self.department = Department.objects.create(name="Audit Dept", code="AUD")
        self.designation = Designation.objects.create(name="Auditor", code="ADT")
        self.employee = Employee.objects.create(
            employee_number="EMP-AUD-001",
            first_name="Audit",
            last_name="Subject",
            branch=self.branch,
            department=self.department,
            designation=self.designation,
            user=self.staff,
            status=EmployeeStatus.ACTIVE,
        )
        self.other_employee = Employee.objects.create(
            employee_number="EMP-AUD-002",
            first_name="Other",
            last_name="Subject",
            branch=self.branch,
            department=self.department,
            designation=self.designation,
            user=self.other,
            status=EmployeeStatus.ACTIVE,
        )

    def _login(self, user):
        self.client.force_login(user)
        UserSession.objects.update_or_create(
            user=user,
            session_key=self.client.session.session_key,
            defaults={"device_id": f"device-{user.pk}", "is_active": True},
        )

    def test_employee_soft_delete_creates_trash_entry(self):
        entry, created = TrashService.soft_delete(self.employee, actor=self.admin, reason="Duplicate profile")
        self.employee.refresh_from_db()
        self.assertTrue(created)
        self.assertTrue(self.employee.is_trashed)
        self.assertEqual(entry.status, TrashEntry.STATUS_ACTIVE)
        self.assertEqual(entry.metadata["previous_status"], EmployeeStatus.ACTIVE)
        self.assertTrue(AuditEvent.objects.filter(action="deleted", object_id=str(self.employee.pk)).exists())

    def test_duplicate_delete_returns_existing_entry(self):
        first, created_first = TrashService.soft_delete(self.employee, actor=self.admin, reason="First")
        second, created_second = TrashService.soft_delete(self.employee, actor=self.admin, reason="Second")
        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(TrashEntry.objects.filter(object_id=str(self.employee.pk), status=TrashEntry.STATUS_ACTIVE).count(), 1)

    def test_restore_returns_employee_without_changing_status(self):
        self.employee.status = EmployeeStatus.SUSPENDED
        self.employee.is_suspended = True
        self.employee.save()
        entry, _ = TrashService.soft_delete(self.employee, actor=self.admin, reason="Restore me")
        restored, changed = TrashService.restore(entry, actor=self.admin)
        self.employee.refresh_from_db()
        self.assertTrue(changed)
        self.assertEqual(restored.status, TrashEntry.STATUS_RESTORED)
        self.assertFalse(self.employee.is_trashed)
        self.assertEqual(self.employee.status, EmployeeStatus.SUSPENDED)

    def test_dependency_blocked_hard_delete(self):
        entry, _ = TrashService.soft_delete(self.employee, actor=self.admin, reason="Blocked")
        with mock.patch("apps.audit.services.TrashService.get_dependencies", return_value={"blocked": True, "items": [{"message": "Attendance history exists."}]}):
            with self.assertRaises(ValidationError):
                TrashService.permanent_delete(entry, actor=self.admin)
        self.employee.refresh_from_db()
        self.assertTrue(self.employee.is_trashed)

    def test_superadmin_permanent_delete(self):
        entry, _ = TrashService.soft_delete(self.employee, actor=self.admin, reason="Purge")
        TrashService.permanent_delete(entry, actor=self.admin)
        self.assertFalse(Employee.objects.filter(pk=self.employee.pk).exists())
        entry.refresh_from_db()
        self.assertEqual(entry.status, TrashEntry.STATUS_PURGED)

    def test_bulk_restore_and_bulk_permanent_delete(self):
        self._login(self.admin)
        emp2 = Employee.objects.create(
            employee_number="EMP-AUD-003",
            first_name="Bulk",
            last_name="Restore",
            branch=self.branch,
            department=self.department,
            designation=self.designation,
            status=EmployeeStatus.ACTIVE,
        )
        entry1, _ = TrashService.soft_delete(self.employee, actor=self.admin, reason="Bulk")
        entry2, _ = TrashService.soft_delete(emp2, actor=self.admin, reason="Bulk")
        restore_resp = self.client.post(reverse("audit:trash_bulk"), {"ids": [entry1.pk, entry2.pk], "bulk_action": "restore"})
        self.assertEqual(restore_resp.status_code, 302)
        self.employee.refresh_from_db()
        emp2.refresh_from_db()
        self.assertFalse(self.employee.is_trashed)
        self.assertFalse(emp2.is_trashed)

        entry1, _ = TrashService.soft_delete(self.employee, actor=self.admin, reason="Bulk purge")
        entry2, _ = TrashService.soft_delete(emp2, actor=self.admin, reason="Bulk purge")
        purge_resp = self.client.post(reverse("audit:trash_bulk"), {"ids": [entry1.pk, entry2.pk], "bulk_action": "permanent_delete"})
        self.assertEqual(purge_resp.status_code, 302)
        entry1.refresh_from_db()
        entry2.refresh_from_db()
        self.assertEqual(entry1.status, TrashEntry.STATUS_PURGED)
        self.assertEqual(entry2.status, TrashEntry.STATUS_PURGED)
        self.assertFalse(Employee.objects.filter(pk=self.employee.pk).exists())
        self.assertFalse(Employee.objects.filter(pk=emp2.pk).exists())

    def test_staff_activity_scope_only_sees_own_records(self):
        own_event = AuditEvent.objects.create(
            actor_user=self.admin,
            actor_role="admin",
            module="employees",
            object_type="Employee",
            object_id=str(self.employee.pk),
            object_label=self.employee.get_full_name(),
            action="updated",
            related_employee=self.employee,
        )
        AuditEvent.objects.create(
            actor_user=self.admin,
            actor_role="admin",
            module="employees",
            object_type="Employee",
            object_id=str(self.other_employee.pk),
            object_label=self.other_employee.get_full_name(),
            action="updated",
            related_employee=self.other_employee,
        )
        scoped_ids = set(AuditService.get_scoped_events(self.staff).values_list("pk", flat=True))
        self.assertIn(own_event.pk, scoped_ids)
        self.assertNotIn(
            AuditEvent.objects.filter(object_id=str(self.other_employee.pk), action="updated").first().pk,
            scoped_ids,
        )

    def test_detailed_audit_locked_until_reauth(self):
        event = AuditEvent.objects.create(
            actor_user=self.admin,
            actor_role="admin",
            module="employees",
            object_type="Employee",
            object_id=str(self.employee.pk),
            object_label=self.employee.get_full_name(),
            action="updated",
            related_employee=self.employee,
            before_data={"branch": "Dhaka"},
            after_data={"branch": "Chattogram"},
        )
        self._login(self.staff)
        resp = self.client.get(reverse("audit:event_detail", kwargs={"pk": event.pk}), HTTP_HX_REQUEST="true")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Audit Detail Unlock")

    def test_correct_password_unlock_and_access_logging(self):
        event = AuditEvent.objects.create(
            actor_user=self.admin,
            actor_role="admin",
            module="employees",
            object_type="Employee",
            object_id=str(self.employee.pk),
            object_label=self.employee.get_full_name(),
            action="updated",
            related_employee=self.employee,
            before_data={"branch": "Dhaka"},
            after_data={"branch": "Chattogram"},
        )
        self._login(self.staff)
        unlock_resp = self.client.post(reverse("accounts:security_reauth"), {
            "reauth_credential": self.staff_password,
            "target_url": reverse("audit:event_detail", kwargs={"pk": event.pk}),
            "reauth_scope": "audit_detail",
        })
        self.assertEqual(unlock_resp.status_code, 302)
        detail_resp = self.client.get(reverse("audit:event_detail", kwargs={"pk": event.pk}), HTTP_HX_REQUEST="true")
        self.assertEqual(detail_resp.status_code, 200)
        self.assertContains(detail_resp, "Chattogram")
        self.assertTrue(AuditAccessLog.objects.filter(user=self.staff, audit_event=event).exists())

    def test_wrong_password_rejected_for_unlock(self):
        event = AuditEvent.objects.create(
            actor_user=self.admin,
            actor_role="admin",
            module="employees",
            object_type="Employee",
            object_id=str(self.employee.pk),
            object_label=self.employee.get_full_name(),
            action="updated",
            related_employee=self.employee,
        )
        self._login(self.staff)
        resp = self.client.post(reverse("accounts:security_reauth"), {
            "reauth_credential": "wrong-password",
            "target_url": reverse("audit:event_detail", kwargs={"pk": event.pk}),
            "reauth_scope": "audit_detail",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Authentication failed")
        detail_resp = self.client.get(reverse("audit:event_detail", kwargs={"pk": event.pk}), HTTP_HX_REQUEST="true")
        self.assertContains(detail_resp, "Audit Detail Unlock")

    def test_status_history_preservation_after_restore(self):
        self.employee.status = EmployeeStatus.SUSPENDED
        self.employee.save()
        entry, _ = TrashService.soft_delete(self.employee, actor=self.admin)
        restored, changed = TrashService.restore(entry, actor=self.admin)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.status, EmployeeStatus.SUSPENDED)
        self.assertFalse(self.employee.is_trashed)

    def test_staff_unauthorized_trash_action(self):
        self._login(self.staff)
        resp = self.client.get(reverse("audit:trash_list"))
        self.assertEqual(resp.status_code, 302)

    def test_manager_cross_scope_actions_blocked(self):
        mgr_user = User.objects.create_user(email="mgr@example.com", password="Password123", role="manager")
        from apps.accounts.rbac_models import Role, UserRoleAssignment
        role_mgr = Role.objects.get_or_create(code="manager", defaults={"name": "Manager", "is_active": True})[0]
        UserRoleAssignment.objects.create(user=mgr_user, role=role_mgr)
        self._login(mgr_user)
        entry, _ = TrashService.soft_delete(self.other_employee, actor=self.admin)
        resp = self.client.post(reverse("audit:trash_restore", kwargs={"pk": entry.pk}))
        self.assertEqual(resp.status_code, 404)

    def test_tampered_bulk_ids_blocked_securely(self):
        mgr_user = User.objects.create_user(email="mgr2@example.com", password="Password123", role="manager")
        from apps.accounts.rbac_models import Role, UserRoleAssignment
        role_mgr = Role.objects.get_or_create(code="manager", defaults={"name": "Manager", "is_active": True})[0]
        UserRoleAssignment.objects.create(user=mgr_user, role=role_mgr)
        self._login(mgr_user)
        entry, _ = TrashService.soft_delete(self.other_employee, actor=self.admin)
        resp = self.client.post(reverse("audit:trash_bulk"), {"ids": [entry.pk], "bulk_action": "restore"})
        self.assertEqual(resp.status_code, 302)
        entry.refresh_from_db()
        self.assertEqual(entry.status, TrashEntry.STATUS_ACTIVE)

    def test_real_empty_trash(self):
        self._login(self.admin)
        entry1, _ = TrashService.soft_delete(self.employee, actor=self.admin)
        entry2, _ = TrashService.soft_delete(self.other_employee, actor=self.admin)
        resp = self.client.post(reverse("audit:trash_bulk"), {"bulk_action": "empty_trash"})
        self.assertEqual(resp.status_code, 302)
        entry1.refresh_from_db()
        entry2.refresh_from_db()
        self.assertEqual(entry1.status, TrashEntry.STATUS_PURGED)
        self.assertEqual(entry2.status, TrashEntry.STATUS_PURGED)

    def test_partial_empty_trash_with_blocked_records(self):
        self._login(self.admin)
        entry1, _ = TrashService.soft_delete(self.employee, actor=self.admin)
        entry2, _ = TrashService.soft_delete(self.other_employee, actor=self.admin)
        with mock.patch("apps.audit.services.TrashService.get_dependencies", side_effect=lambda obj: {"blocked": True, "items": [{"message": "Blocked."}]} if obj.pk == self.employee.pk else {"blocked": False, "items": []}):
            resp = self.client.post(reverse("audit:trash_bulk"), {"bulk_action": "empty_trash"})
            self.assertEqual(resp.status_code, 302)
            entry1.refresh_from_db()
            entry2.refresh_from_db()
            self.assertEqual(entry1.status, TrashEntry.STATUS_ACTIVE)
            self.assertEqual(entry2.status, TrashEntry.STATUS_PURGED)

    def test_sensitive_field_masking(self):
        self.employee.bank_account = "123456789"
        self.employee.save()
        event = AuditEvent.objects.filter(object_id=str(self.employee.pk)).first()
        self.assertIsNotNone(event)
        self.assertEqual(event.after_data.get("bank_account"), "********")

    def test_unlock_expiry(self):
        from apps.audit.auth import grant_audit_unlock, has_audit_unlock
        class MockRequestSession(dict):
            modified = False
        class MockRequest:
            def __init__(self):
                self.session = MockRequestSession()
        req = MockRequest()
        grant_audit_unlock(req, seconds=-10)
        self.assertFalse(has_audit_unlock(req))

    def test_legacy_history_remains_readable(self):
        self._login(self.admin)
        from apps.employees.models import EmployeeAuditLog
        EmployeeAuditLog.objects.create(
            employee=self.employee,
            old_value={"status": "active"},
            new_value={"status": "suspended"},
            changed_by=self.admin
        )
        resp = self.client.get(reverse("employees:master_audit", kwargs={"pk": self.employee.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "suspended")

