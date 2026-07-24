from django.test import TestCase
from django.contrib.auth import get_user_model
from apps.accounts.models import (
    Module, Action, Permission, PermissionDependency,
    Role, RolePermission, UserRoleAssignment, UserPermissionOverride, DataScope
)
from apps.accounts.engine import PermissionEngine
from apps.employees.models import Employee, EmployeeStatus, Department, Designation
from apps.branches.models import Branch

User = get_user_model()


class RBACEngineTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name='HQ Branch', latitude=23.8, longitude=90.4)
        self.dept = Department.objects.create(name='Engineering', code='ENG')
        self.desig = Designation.objects.create(name='Dev', code='DEV')

        self.user = User.objects.create_user(email='emp_test@example.com', password='Password123!')
        self.emp = Employee.objects.create(
            employee_number='EMP-001',
            first_name='Test',
            last_name='User',
            user=self.user,
            branch=self.branch,
            department=self.dept,
            designation=self.desig,
            status=EmployeeStatus.ACTIVE
        )

        self.mod = Module.objects.create(name='Attendance', code='attendance')
        self.act_view = Action.objects.create(name='View', code='view')
        self.act_edit = Action.objects.create(name='Edit', code='edit')
        self.perm_view = Permission.objects.create(module=self.mod, action=self.act_view, codename='attendance.view')
        self.perm_edit = Permission.objects.create(module=self.mod, action=self.act_edit, codename='attendance.edit')

        self.role = Role.objects.create(name='Attendance Viewer', code='att_viewer')
        RolePermission.objects.create(role=self.role, permission=self.perm_view, data_scope=DataScope.BRANCH)
        UserRoleAssignment.objects.create(user=self.user, role=self.role)

    def test_layer1_suspended_employee_blocked(self):
        self.emp.status = EmployeeStatus.SUSPENDED
        self.emp.save()

        res = PermissionEngine.evaluate(self.user, 'attendance.view')
        self.assertFalse(res.allowed)
        self.assertIn("suspended", res.reason)

    def test_layer1_archived_employee_read_only(self):
        self.emp.status = EmployeeStatus.ARCHIVED
        self.emp.save()

        # View allowed (read-only)
        res_view = PermissionEngine.evaluate(self.user, 'attendance.view', action_type='view')
        self.assertTrue(res_view.allowed)
        self.assertTrue(res_view.read_only)

        # Edit blocked
        res_edit = PermissionEngine.evaluate(self.user, 'attendance.edit', action_type='edit')
        self.assertFalse(res_edit.allowed)

    def test_layer2_5_multi_role_and_overrides(self):
        # 1. User has view permission via role
        res = PermissionEngine.evaluate(self.user, 'attendance.view')
        self.assertTrue(res.allowed)
        self.assertEqual(res.data_scope, 'branch')

        # 2. Grant override for edit
        UserPermissionOverride.objects.create(user=self.user, permission=self.perm_edit, is_granted=True, data_scope=DataScope.GLOBAL)
        res_edit = PermissionEngine.evaluate(self.user, 'attendance.edit')
        self.assertTrue(res_edit.allowed)
        self.assertEqual(res_edit.data_scope, 'global')

        # 3. Direct revoke override for view
        UserPermissionOverride.objects.create(user=self.user, permission=self.perm_view, is_granted=False)
        # Clear request cache
        if hasattr(self.user, '_resolved_permissions_cache'):
            delattr(self.user, '_resolved_permissions_cache')

        res_revoked = PermissionEngine.evaluate(self.user, 'attendance.view')
        self.assertFalse(res_revoked.allowed)

    def test_prerequisite_dependency(self):
        # Edit requires view
        PermissionDependency.objects.create(permission=self.perm_edit, requires_permission=self.perm_view)
        UserPermissionOverride.objects.create(user=self.user, permission=self.perm_edit, is_granted=True)
        # Revoke view
        UserPermissionOverride.objects.create(user=self.user, permission=self.perm_view, is_granted=False)

        if hasattr(self.user, '_resolved_permissions_cache'):
            delattr(self.user, '_resolved_permissions_cache')

        res = PermissionEngine.evaluate(self.user, 'attendance.edit')
        self.assertFalse(res.allowed)
        self.assertIn("requires prerequisite", res.reason)
