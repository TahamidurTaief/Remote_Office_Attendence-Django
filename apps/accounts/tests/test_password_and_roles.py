from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction

from apps.accounts.models import Role, UserRoleAssignment, UserSession, Module, Action, Permission, RolePermission, DataScope
from apps.accounts.services import RoleAssignmentService
from apps.accounts.engine import PermissionEngine
from apps.audit.models import AuditEvent
from apps.notifications.models import AuditLog

User = get_user_model()


class PasswordChangeAndRoleRepairTests(TestCase):
    def setUp(self):
        # Create users
        self.admin = User.objects.create_superuser(
            email='admin@enterprise.local',
            password='ComplexPassword123!@#',
            role='admin'
        )
        self.staff_user = User.objects.create_user(
            email='staff@enterprise.local',
            password='InitialPassword123!@#',
            role='staff'
        )
        self.manager_user = User.objects.create_user(
            email='manager@enterprise.local',
            password='ManagerPassword123!@#',
            role='manager'
        )

        # Setup RBAC modules and permissions
        self.module = Module.objects.create(name='Employees', code='employees')
        self.action_view = Action.objects.create(name='View', code='view')
        self.action_edit = Action.objects.create(name='Edit', code='edit')
        self.perm_view = Permission.objects.create(module=self.module, action=self.action_view)
        self.perm_edit = Permission.objects.create(module=self.module, action=self.action_edit)

        # Setup roles
        self.role_staff = Role.objects.create(name='Staff Member', code='staff_role', is_active=True)
        RolePermission.objects.create(role=self.role_staff, permission=self.perm_view, data_scope=DataScope.OWN)

        self.role_lead = Role.objects.create(name='Team Lead', code='team_lead', is_active=True)
        RolePermission.objects.create(role=self.role_lead, permission=self.perm_view, data_scope=DataScope.TEAM)

        self.role_manager = Role.objects.create(name='Department Manager', code='department_head', is_active=True)
        RolePermission.objects.create(role=self.role_manager, permission=self.perm_edit, data_scope=DataScope.DEPARTMENT)

        self.role_inactive = Role.objects.create(name='Deprecated Role', code='deprecated', is_active=False)
        self.role_system_owner = Role.objects.create(name='System Owner Role', code='system_owner', is_system_protected=True, is_active=True)
        self.role_super_admin = Role.objects.create(name='Super Admin Role', code='super_admin', is_active=True)

        # Assign initial role to staff_user
        UserRoleAssignment.objects.create(user=self.staff_user, role=self.role_staff, assigned_by=self.admin)

    def test_correct_password_change_end_to_end(self):
        client = Client()
        client.login(email='staff@enterprise.local', password='InitialPassword123!@#')

        # Create active session records
        current_session_key = client.session.session_key
        UserSession.objects.create(user=self.staff_user, session_key=current_session_key, is_active=True)
        other_session = UserSession.objects.create(user=self.staff_user, session_key='other_device_session_xyz', is_active=True)

        new_pass = 'BrandNewStrongPass2026!#$'
        resp = client.post(reverse('accounts:change_password'), {
            'old_password': 'InitialPassword123!@#',
            'new_password1': new_pass,
            'new_password2': new_pass,
        }, HTTP_HX_REQUEST='true')

        self.assertEqual(resp.status_code, 200)

        # 1. Verify password changed
        self.staff_user.refresh_from_db()
        self.assertTrue(self.staff_user.check_password(new_pass))
        self.assertFalse(self.staff_user.check_password('InitialPassword123!@#'))

        # 2. Verify current session kept active
        self.assertEqual(int(client.session['_auth_user_id']), self.staff_user.pk)
        curr_us = UserSession.objects.get(session_key=client.session.session_key)
        self.assertTrue(curr_us.is_active)
        verify_resp = client.get(reverse('accounts:change_password'))
        self.assertEqual(verify_resp.status_code, 200)

        # 3. Verify other session revoked
        other_session.refresh_from_db()
        self.assertFalse(other_session.is_active)

        # 4. Verify audit event logged without password leak
        audit_events = AuditEvent.objects.filter(object_id=str(self.staff_user.pk), action='password_change')
        self.assertTrue(audit_events.exists())
        for ev in audit_events:
            self.assertNotIn(new_pass, str(ev.before_data))
            self.assertNotIn(new_pass, str(ev.after_data))
            self.assertNotIn(new_pass, ev.reason_note)

        audit_logs = AuditLog.objects.filter(target_id=str(self.staff_user.pk), action='password_change')
        self.assertTrue(audit_logs.exists())
        for al in audit_logs:
            self.assertNotIn(new_pass, al.summary)

    def test_wrong_current_password_rejected(self):
        client = Client()
        client.login(email='staff@enterprise.local', password='InitialPassword123!@#')

        resp = client.post(reverse('accounts:change_password'), {
            'old_password': 'WrongPassword123!',
            'new_password1': 'BrandNewStrongPass2026!#$',
            'new_password2': 'BrandNewStrongPass2026!#$',
        }, HTTP_HX_REQUEST='true')

        self.assertEqual(resp.status_code, 200)
        self.staff_user.refresh_from_db()
        self.assertTrue(self.staff_user.check_password('InitialPassword123!@#'))
        self.assertContains(resp, 'Current password is incorrect')

    def test_weak_or_invalid_password_rejected(self):
        client = Client()
        client.login(email='staff@enterprise.local', password='InitialPassword123!@#')

        # Too short / common password
        resp = client.post(reverse('accounts:change_password'), {
            'old_password': 'InitialPassword123!@#',
            'new_password1': '123',
            'new_password2': '123',
        }, HTTP_HX_REQUEST='true')

        self.assertEqual(resp.status_code, 200)
        self.staff_user.refresh_from_db()
        self.assertTrue(self.staff_user.check_password('InitialPassword123!@#'))

    def test_password_confirmation_mismatch(self):
        client = Client()
        client.login(email='staff@enterprise.local', password='InitialPassword123!@#')

        resp = client.post(reverse('accounts:change_password'), {
            'old_password': 'InitialPassword123!@#',
            'new_password1': 'BrandNewStrongPass2026!#$',
            'new_password2': 'DifferentPassword2026!#$',
        }, HTTP_HX_REQUEST='true')

        self.assertEqual(resp.status_code, 200)
        self.staff_user.refresh_from_db()
        self.assertTrue(self.staff_user.check_password('InitialPassword123!@#'))
        self.assertContains(resp, 'do not match')

    def test_staff_change_password_endpoint(self):
        client = Client()
        client.login(email='staff@enterprise.local', password='InitialPassword123!@#')

        new_pass = 'StaffStrongNewPassword2026!'
        resp = client.post(reverse('staff:change_password'), {
            'current_password': 'InitialPassword123!@#',
            'new_password': new_pass,
            'confirm_password': new_pass,
        })
        self.assertEqual(resp.status_code, 302)
        self.staff_user.refresh_from_db()
        self.assertTrue(self.staff_user.check_password(new_pass))

    def test_multi_role_selection_and_persistence(self):
        # Assign multiple roles to staff_user
        added, removed = RoleAssignmentService.sync_user_roles(
            user=self.staff_user,
            target_roles=[self.role_staff, self.role_lead],
            actor=self.admin,
            preserve_protected=True
        )
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0].id, self.role_lead.id)

        # Verify persistence
        assigned_ids = set(UserRoleAssignment.objects.filter(user=self.staff_user).values_list('role_id', flat=True))
        self.assertIn(self.role_staff.id, assigned_ids)
        self.assertIn(self.role_lead.id, assigned_ids)

    def test_role_removal(self):
        RoleAssignmentService.sync_user_roles(
            user=self.staff_user,
            target_roles=[self.role_staff, self.role_lead],
            actor=self.admin,
            preserve_protected=True
        )

        # Remove role_lead
        added, removed = RoleAssignmentService.sync_user_roles(
            user=self.staff_user,
            target_roles=[self.role_staff],
            actor=self.admin,
            preserve_protected=True
        )
        self.assertEqual(len(removed), 1)
        self.assertEqual(removed[0].id, self.role_lead.id)

        assigned_ids = set(UserRoleAssignment.objects.filter(user=self.staff_user).values_list('role_id', flat=True))
        self.assertNotIn(self.role_lead.id, assigned_ids)

    def test_inactive_and_forged_roles_rejected(self):
        # Inactive role
        with self.assertRaises((ValidationError, PermissionDenied)):
            RoleAssignmentService.sync_user_roles(
                user=self.staff_user,
                target_roles=[self.role_inactive],
                actor=self.admin
            )

        # Server-side validation in UserPermissionsView
        client = Client()
        client.login(email='admin@enterprise.local', password='ComplexPassword123!@#')
        resp = client.post(reverse('admin_panel:user_permissions', kwargs={'pk': self.staff_user.pk}), {
            'role_ids': ['999999'],  # Forged non-existent ID
        })
        self.assertEqual(resp.status_code, 302)
        assigned_ids = list(UserRoleAssignment.objects.filter(user=self.staff_user).values_list('role_id', flat=True))
        self.assertNotIn(999999, assigned_ids)

    def test_system_owner_role_protected(self):
        # system_owner cannot be assigned
        with self.assertRaises(PermissionDenied):
            RoleAssignmentService.sync_user_roles(
                user=self.staff_user,
                target_roles=[self.role_system_owner],
                actor=self.admin,
                preserve_protected=False
            )

        # system_owner never appears in get_assignable_roles_queryset
        assignable = RoleAssignmentService.get_assignable_roles_queryset(actor=self.admin)
        self.assertNotIn(self.role_system_owner, assignable)

    def test_super_admin_role_boundary(self):
        # Non-superuser manager cannot assign super_admin
        with self.assertRaises(PermissionDenied):
            RoleAssignmentService.sync_user_roles(
                user=self.staff_user,
                target_roles=[self.role_super_admin],
                actor=self.manager_user
            )

        # Superuser CAN assign super_admin
        added, _ = RoleAssignmentService.sync_user_roles(
            user=self.staff_user,
            target_roles=[self.role_staff, self.role_super_admin],
            actor=self.admin
        )
        self.assertTrue(any(r.code == 'super_admin' for r in added))

    def test_permission_scope_ceiling_enforced(self):
        # Give manager_user only role_lead (team scope)
        UserRoleAssignment.objects.create(user=self.manager_user, role=self.role_lead, assigned_by=self.admin)

        # manager_user attempts to assign role_manager (department scope, higher rank)
        with self.assertRaises(PermissionDenied):
            RoleAssignmentService.sync_user_roles(
                user=self.staff_user,
                target_roles=[self.role_manager],
                actor=self.manager_user
            )

    def test_permission_cache_invalidation(self):
        # Seed user cache
        resolved_before = PermissionEngine.get_user_resolved_permissions(self.staff_user)
        self.assertTrue(hasattr(self.staff_user, '_resolved_permissions_cache'))

        # Add lead role
        RoleAssignmentService.sync_user_roles(
            user=self.staff_user,
            target_roles=[self.role_staff, self.role_lead],
            actor=self.admin
        )

        # Check cache is invalidated
        self.assertFalse(hasattr(self.staff_user, '_resolved_permissions_cache'))

    def test_atomic_rollback_on_failure(self):
        initial_roles = list(UserRoleAssignment.objects.filter(user=self.staff_user).values_list('role_id', flat=True))

        try:
            with transaction.atomic():
                RoleAssignmentService.sync_user_roles(
                    user=self.staff_user,
                    target_roles=[self.role_lead, self.role_inactive],  # role_inactive will raise
                    actor=self.admin
                )
        except Exception:
            pass

        # Verify role_lead was rolled back and not persisted
        current_roles = list(UserRoleAssignment.objects.filter(user=self.staff_user).values_list('role_id', flat=True))
        self.assertEqual(initial_roles, current_roles)

    def test_compatibility_persona_mapping(self):
        # Only staff role -> persona is 'staff'
        self.assertEqual(RoleAssignmentService.compute_compatibility_persona([self.role_staff]), 'staff')

        # Lead or manager -> persona is 'manager'
        self.assertEqual(RoleAssignmentService.compute_compatibility_persona([self.role_staff, self.role_manager]), 'manager')

        # Super admin -> persona is 'admin'
        self.assertEqual(RoleAssignmentService.compute_compatibility_persona([self.role_staff, self.role_super_admin]), 'admin')
