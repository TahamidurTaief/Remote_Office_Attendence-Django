from django.test import TestCase
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from apps.accounts.models import Role, UserRoleAssignment, RolePermission, Permission, Module, Action, DataScope
from apps.admin_panel.forms import DynamicRoleForm, normalize_role_code

User = get_user_model()


class DynamicRBACAdminSecurityTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(
            email='root@example.com',
            password='testpassword123'
        )
        self.branch_admin = User.objects.create_user(
            email='admin@branch.com',
            password='testpassword123',
            role='admin'
        )
        self.system_owner_role = Role.objects.create(
            name='System Owner',
            code='system_owner',
            is_system_protected=True,
            is_active=True
        )

    def test_normalize_role_code(self):
        self.assertEqual(normalize_role_code("  Project   Supervisor  "), "project_supervisor")
        self.assertEqual(normalize_role_code("HR-Manager! 2026"), "hr_manager_2026")
        self.assertEqual(normalize_role_code("___leading_trailing___"), "leading_trailing")

    def test_code_auto_generation_and_casing_collision(self):
        form = DynamicRoleForm(
            data={'name': 'Project Supervisor', 'code': '', 'is_active': True},
            user=self.branch_admin
        )
        self.assertTrue(form.is_valid(), form.errors)
        role = form.save()
        self.assertEqual(role.code, 'project_supervisor')

        # Edge case 1: Collision on different casing/whitespace
        dup_form = DynamicRoleForm(
            data={'name': '  pRoJeCt   SuPeRvIsOr  ', 'code': '', 'is_active': True},
            user=self.branch_admin
        )
        self.assertFalse(dup_form.is_valid())
        self.assertIn('name', dup_form.errors)

        dup_code_form = DynamicRoleForm(
            data={'name': 'Different Name', 'code': '  PROJECT_SUPERVISOR  ', 'is_active': True},
            user=self.branch_admin
        )
        self.assertFalse(dup_code_form.is_valid())
        self.assertIn('code', dup_code_form.errors)

    def test_system_owner_protection(self):
        form = DynamicRoleForm(
            data={'name': 'System Owner Clone', 'code': 'system_owner', 'is_active': True},
            user=self.branch_admin
        )
        self.assertFalse(form.is_valid())
        self.assertIn('code', form.errors)

        # Cannot disable system owner
        edit_form = DynamicRoleForm(
            instance=self.system_owner_role,
            data={'name': 'System Owner', 'code': 'system_owner', 'is_active': False},
            user=self.superuser
        )
        self.assertFalse(edit_form.is_valid())
        self.assertIn('is_active', edit_form.errors)

    def test_super_admin_boundary(self):
        # Non-superuser cannot create super_admin
        non_su_form = DynamicRoleForm(
            data={'name': 'Super Admin', 'code': 'super_admin', 'is_active': True},
            user=self.branch_admin
        )
        self.assertFalse(non_su_form.is_valid())
        self.assertIn('code', non_su_form.errors)

        # Superuser can create dynamic super_admin with is_superuser=False
        su_form = DynamicRoleForm(
            data={'name': 'Super Admin', 'code': 'super_admin', 'is_active': True},
            user=self.superuser
        )
        self.assertTrue(su_form.is_valid(), su_form.errors)
        sa_role = su_form.save()
        self.assertEqual(sa_role.code, 'super_admin')
        self.assertFalse(sa_role.is_system_protected)

    def test_role_code_immutability_after_assignment(self):
        role = Role.objects.create(name='Accountant', code='accountant', is_active=True)
        UserRoleAssignment.objects.create(user=self.branch_admin, role=role)

        edit_form = DynamicRoleForm(
            instance=role,
            data={'name': 'Senior Accountant', 'code': 'senior_accountant', 'is_active': True},
            user=self.branch_admin
        )
        self.assertFalse(edit_form.is_valid())
        self.assertIn('code', edit_form.errors)

    def test_last_privileged_role_lockout_prevention(self):
        admin_role = Role.objects.create(name='Administrator', code='admin', is_active=True)
        # Deactivate while no other active superuser/privileged user exists
        User.objects.filter(is_superuser=True).delete()

        form = DynamicRoleForm(
            instance=admin_role,
            data={'name': 'Administrator', 'code': 'admin', 'is_active': False},
            user=self.branch_admin
        )
        self.assertFalse(form.is_valid())
        self.assertIn('is_active', form.errors)
        self.assertIn('lockout prevention', form.errors['is_active'][0])
