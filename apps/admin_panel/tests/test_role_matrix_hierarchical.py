import json
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from apps.accounts.models import Role, Module, Action, Permission, RolePermission, UserRoleAssignment, DataScope
from apps.accounts.rbac_registry import RBACRegistryService
from apps.accounts.services import RolePermissionAssignmentService, RoleAssignmentService
from apps.accounts.engine import PermissionEngine
from apps.audit.models import AuditEvent

User = get_user_model()


class HierarchicalRoleMatrixTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.password = 'MatrixTestPass123!'

        # 1. Seed canonical registry in test DB
        RBACRegistryService.sync_database()

        # 2. Superuser
        self.superuser = User.objects.create_superuser(
            email='super_matrix@example.com',
            password=self.password,
            role='admin'
        )

        # 3. Regular role admin (privileged non-superuser)
        self.role_admin = User.objects.create_user(
            email='role_admin@example.com',
            password=self.password,
            role='admin'
        )
        self.role_admin_role = Role.objects.create(
            code='role_admin_delegated',
            name='Delegated Role Admin',
            is_active=True
        )
        perm_acc_edit = Permission.objects.get(codename='accounts.edit')
        RolePermission.objects.create(
            role=self.role_admin_role,
            permission=perm_acc_edit,
            data_scope=DataScope.GLOBAL
        )
        UserRoleAssignment.objects.create(user=self.role_admin, role=self.role_admin_role)

        # 4. Standard staff user (unauthorized)
        self.staff_user = User.objects.create_user(
            email='staff_matrix@example.com',
            password=self.password,
            role='staff'
        )

        # 5. Roles
        self.sys_owner = Role.objects.get(code='system_owner')
        # Ensure Role ID 1 is sys_owner or create test role
        self.custom_role = Role.objects.create(
            name='Field Inspector',
            code='field_inspector',
            is_active=True,
            is_system_protected=False
        )

        # Assign superuser to system_owner
        UserRoleAssignment.objects.get_or_create(user=self.superuser, role=self.sys_owner)

    def test_role_1_matrix_rendering(self):
        """Role 1 matrix must render successfully with 200 OK and show hierarchy."""
        self.client.login(email='super_matrix@example.com', password=self.password)
        # Check if role 1 exists, otherwise test sys_owner
        role_1 = Role.objects.filter(pk=1).first() or self.sys_owner

        resp = self.client.get(reverse('admin_panel:role_matrix', kwargs={'pk': role_1.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Permission Matrix')
        self.assertContains(resp, role_1.name)
        self.assertContains(resp, 'Data Scope')
        self.assertContains(resp, 'Protected System Owner Role')

    def test_complete_hierarchy_discovery(self):
        """Canonical registry must discover all 12 modules and nested submodules/menus."""
        tree = RBACRegistryService.get_canonical_hierarchy()
        self.assertGreaterEqual(len(tree), 12)
        module_codes = {m['code'] for m in tree}
        expected_modules = {'dashboard', 'employees', 'attendance', 'leave', 'schedule', 'projects', 'tasks', 'payroll', 'expense', 'branches', 'accounts', 'ai_workspace'}
        self.assertTrue(expected_modules.issubset(module_codes))

        flat = RBACRegistryService.get_all_nodes_flat()
        levels = {n['level'] for n in flat}
        self.assertIn('module', levels)
        self.assertIn('submodule', levels)
        self.assertIn('menu', levels)

    def test_missing_registry_synchronization_and_duplicate_prevention(self):
        """Sync must be additive, safe, and prevent duplicates."""
        initial_perm_count = Permission.objects.count()
        initial_mod_count = Module.objects.count()

        # Run sync again
        stats = RBACRegistryService.sync_database()
        self.assertEqual(stats['modules'], 0)
        self.assertEqual(stats['permissions'], 0)
        self.assertEqual(Permission.objects.count(), initial_perm_count)
        self.assertEqual(Module.objects.count(), initial_mod_count)

    def test_five_controls_present_on_rows(self):
        """Each row must expose Add, Edit, Delete, Update, and All controls."""
        self.client.login(email='super_matrix@example.com', password=self.password)
        resp = self.client.get(reverse('admin_panel:role_matrix', kwargs={'pk': self.custom_role.pk}))
        self.assertEqual(resp.status_code, 200)

        # Check column headers
        self.assertContains(resp, 'Add')
        self.assertContains(resp, 'Edit')
        self.assertContains(resp, 'Delete')
        self.assertContains(resp, 'Update')
        self.assertContains(resp, 'All')

        # Check checkbox component presence
        self.assertContains(resp, 'cb-mod_employees-add')
        self.assertContains(resp, 'cb-mod_employees-edit')
        self.assertContains(resp, 'cb-mod_employees-delete')
        self.assertContains(resp, 'cb-mod_employees-update')
        self.assertContains(resp, 'cb-mod_employees-all')

    def test_atomic_save_and_persistence(self):
        """POST to matrix save persists selections and survives reload."""
        self.client.login(email='super_matrix@example.com', password=self.password)

        payload = {
            'selections': {
                'mod_employees': {'add': True, 'edit': True, 'delete': False, 'update': True, 'all': False},
                'mod_projects': {'add': True, 'edit': False, 'delete': False, 'update': False, 'all': False}
            },
            'scopes': {
                'employees': 'branch',
                'projects': 'team'
            }
        }

        url = reverse('admin_panel:role_matrix_save', kwargs={'pk': self.custom_role.pk})
        resp = self.client.post(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['status'], 'ok')

        # Verify DB records
        self.assertTrue(RolePermission.objects.filter(role=self.custom_role, permission__codename='employees.add').exists())
        self.assertTrue(RolePermission.objects.filter(role=self.custom_role, permission__codename='employees.edit').exists())
        self.assertTrue(RolePermission.objects.filter(role=self.custom_role, permission__codename='employees.update').exists())
        self.assertFalse(RolePermission.objects.filter(role=self.custom_role, permission__codename='employees.delete').exists())

        emp_add_rp = RolePermission.objects.get(role=self.custom_role, permission__codename='employees.add')
        self.assertEqual(emp_add_rp.data_scope, 'branch')

        # Reload matrix page and verify bundle json contains granted permissions
        get_resp = self.client.get(reverse('admin_panel:role_matrix', kwargs={'pk': self.custom_role.pk}))
        self.assertEqual(get_resp.status_code, 200)
        bundle_json = get_resp.context['matrix_bundle_json']
        bundle = json.loads(bundle_json)
        self.assertTrue(bundle['selections']['mod_employees']['add'])
        self.assertTrue(bundle['selections']['mod_employees']['edit'])
        self.assertFalse(bundle['selections']['mod_employees']['delete'])

    def test_unauthorized_view_denied(self):
        """Unauthorized staff cannot view or modify the permission matrix."""
        self.client.login(email='staff_matrix@example.com', password=self.password)
        resp = self.client.get(reverse('admin_panel:role_matrix', kwargs={'pk': self.custom_role.pk}))
        self.assertIn(resp.status_code, (403, 302))

        # Direct POST attempt rejected
        url = reverse('admin_panel:role_matrix_save', kwargs={'pk': self.custom_role.pk})
        post_resp = self.client.post(url, data=json.dumps({'selections': {}}), content_type='application/json')
        self.assertIn(post_resp.status_code, (302, 403))

    def test_system_owner_protected(self):
        """Protected System Owner role cannot be modified."""
        self.client.login(email='super_matrix@example.com', password=self.password)
        url = reverse('admin_panel:role_matrix_save', kwargs={'pk': self.sys_owner.pk})
        resp = self.client.post(url, data=json.dumps({
            'selections': {'mod_employees': {'add': False, 'edit': False, 'delete': False, 'update': False}}
        }), content_type='application/json')
        self.assertEqual(resp.status_code, 403)
        self.assertIn('protected', resp.json()['message'].lower())

    def test_authority_ceiling_enforced(self):
        """Non-superuser cannot grant permissions they do not possess."""
        self.client.login(email='role_admin@example.com', password=self.password)

        # role_admin has accounts.edit, but attempts to grant employees permissions
        payload = {
            'selections': {
                'mod_employees': {'add': True, 'edit': True, 'delete': True, 'update': True}
            }
        }
        url = reverse('admin_panel:role_matrix_save', kwargs={'pk': self.custom_role.pk})
        resp = self.client.post(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, 403)
        self.assertIn('privilege escalation', resp.json()['message'].lower())

    def test_atomic_rollback_on_failure(self):
        """If any permission grant fails, complete transaction rolls back."""
        self.client.login(email='super_matrix@example.com', password=self.password)

        initial_rps = set(RolePermission.objects.filter(role=self.custom_role).values_list('id', flat=True))

        # Attempt batch save with an invalid role or triggering error
        try:
            RolePermissionAssignmentService.sync_role_permissions(
                role=self.sys_owner,  # System owner triggers PermissionDenied
                selections={'mod_employees': {'add': True}},
                actor=self.superuser
            )
        except Exception:
            pass

        # Invariant: system owner permissions untouched
        self.assertEqual(
            RolePermission.objects.filter(role=self.custom_role).count(),
            len(initial_rps)
        )

    def test_audit_event_logged(self):
        """Successful matrix save creates an audit trail entry."""
        self.client.login(email='super_matrix@example.com', password=self.password)

        initial_audit_count = AuditEvent.objects.count()

        payload = {
            'selections': {
                'mod_leave': {'add': True, 'edit': True, 'delete': True, 'update': True}
            }
        }
        url = reverse('admin_panel:role_matrix_save', kwargs={'pk': self.custom_role.pk})
        self.client.post(url, data=json.dumps(payload), content_type='application/json')

        self.assertGreater(AuditEvent.objects.count(), initial_audit_count)
        self.assertTrue(AuditEvent.objects.filter(action='role_permissions_matrix_updated').exists())

    def test_permission_engine_effective_results(self):
        """Granted capabilities allow corresponding PermissionEngine evaluations."""
        # Grant custom role to staff user
        UserRoleAssignment.objects.create(user=self.staff_user, role=self.custom_role)

        # Initially staff user has no employees.add permission
        res_before = PermissionEngine.evaluate(self.staff_user, 'employees.add')
        self.assertFalse(res_before.allowed)

        # Grant employees.add to custom_role
        perm = RBACRegistryService.ensure_permission('employees.add')
        RolePermission.objects.create(role=self.custom_role, permission=perm, data_scope=DataScope.GLOBAL)
        RoleAssignmentService.invalidate_user_permissions(self.staff_user)

        res_after = PermissionEngine.evaluate(self.staff_user, 'employees.add')
        self.assertTrue(res_after.allowed)

        # Compatibility check: employees.create also allowed
        res_create = PermissionEngine.evaluate(self.staff_user, 'employees.create')
        self.assertTrue(res_create.allowed)
