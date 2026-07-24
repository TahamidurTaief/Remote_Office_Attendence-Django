from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from apps.accounts.models import (
    Module, Action, Permission, PermissionDependency,
    Role, RolePermission, UserRoleAssignment, UserPermissionOverride,
    DataScope
)

User = get_user_model()


class RBACModelsTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='testuser@example.com', password='Password123!')
        self.admin = User.objects.create_superuser(email='sysowner@example.com', password='Password123!')

        self.module = Module.objects.create(name='Attendance', code='attendance', sort_order=10)
        self.action = Action.objects.create(name='View', code='view')
        self.perm = Permission.objects.create(module=self.module, action=self.action, name='View Attendance')

    def test_permission_codename_auto_generated(self):
        self.assertEqual(self.perm.codename, 'attendance.view')

    def test_system_protected_role_invariants(self):
        role = Role.objects.create(
            name='System Owner',
            code='system_owner',
            is_system_protected=True,
            is_active=True
        )

        # 1. Cannot delete protected role
        with self.assertRaises(ValidationError):
            role.delete()

        # 2. Cannot change code or deactivate protected role
        role.code = 'modified_code'
        with self.assertRaises(ValidationError):
            role.full_clean()
            role.save()

    def test_role_permission_data_scope(self):
        role = Role.objects.create(name='Manager Role', code='manager_role')
        rp = RolePermission.objects.create(role=role, permission=self.perm, data_scope=DataScope.TEAM)
        self.assertEqual(rp.data_scope, 'team')

    def test_user_permission_override(self):
        override = UserPermissionOverride.objects.create(
            user=self.user,
            permission=self.perm,
            is_granted=True,
            data_scope=DataScope.GLOBAL
        )
        self.assertTrue(override.is_granted)
        self.assertEqual(override.data_scope, 'global')

    def test_permission_dependency_self_reference_validation(self):
        dep = PermissionDependency(permission=self.perm, requires_permission=self.perm)
        with self.assertRaises(ValidationError):
            dep.clean()
