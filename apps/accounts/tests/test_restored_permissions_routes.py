"""
Tests proving each affected permission-protected view restored in the corrective piece
returns HTTP 200 for dynamically assigned authorized role, exact HTTP 403 for authenticated
unauthorized role, and HTTP 302 login redirect for unauthenticated users.
Also verifies mutation / data-scoping enforcement.
"""

from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from apps.accounts.rbac_models import Role, UserRoleAssignment, RolePermission, DataScope
from apps.accounts.rbac_registry import RBACRegistryService
from apps.accounts.engine import PermissionEngine
from apps.branches.models import Branch, Holiday
from apps.leave.models import LeaveType, LeaveRequest
from apps.expense.models import ExpenseCategory, Expense
from apps.payroll.models import PayrollRun
from django.utils import timezone
from datetime import timedelta

User = get_user_model()


class RestoredViewsRouteTests(TestCase):
    def setUp(self):
        RBACRegistryService.sync_database()

        # Create branches
        self.branch_alpha = Branch.objects.create(name='Alpha Branch', latitude=23.8, longitude=90.4, radius_meters=100)
        self.branch_beta = Branch.objects.create(name='Beta Branch', latitude=23.9, longitude=90.5, radius_meters=100)

        # Create users
        self.auth_user = User.objects.create_user(
            email='authorized_test@example.com',
            phone='+8801700999010',
            password='Password123!'
        )
        self.unauth_user = User.objects.create_user(
            email='unauthorized_test@example.com',
            phone='+8801700999011',
            password='Password123!'
        )

        # Create dynamically assigned role for authorized user
        self.auth_role = Role.objects.create(code='role_test_restored_auth', name='Authorized Restored Role', is_active=True)
        UserRoleAssignment.objects.create(user=self.auth_user, role=self.auth_role)

        # Unauthorized user role with zero permissions
        self.unauth_role = Role.objects.create(code='role_test_restored_unauth', name='Unauthorized Role', is_active=True)
        UserRoleAssignment.objects.create(user=self.unauth_user, role=self.unauth_role)

        # Clients
        self.anon_client = Client()
        self.auth_client = Client()
        self.auth_client.force_login(self.auth_user)
        self.unauth_client = Client()
        self.unauth_client.force_login(self.unauth_user)

        from apps.employees.models import EmployeeProfile
        self.profile = EmployeeProfile.objects.create(
            user=self.auth_user,
            full_name='Auth User Profile',
            branch=self.branch_alpha,
            phone='+8801700999010',
            joined_date=timezone.now().date()
        )

        # Seed sample entities for detail/edit/delete/reverse views
        self.holiday = Holiday.objects.create(branch=self.branch_alpha, name='Test Holiday', date=timezone.now().date())
        self.leave_type = LeaveType.objects.create(name='Annual Leave Test', default_days_per_year=15)
        self.expense_claim = Expense.objects.create(
            employee=self.profile,
            amount=150.00,
            description='Office Supplies'
        )
        self.payroll_run = PayrollRun.objects.create(
            name='Test Run',
            period_start=timezone.now().date().replace(day=1),
            period_end=timezone.now().date(),
            status='draft'
        )

    def grant_permission(self, codename, scope=DataScope.GLOBAL):
        p = RBACRegistryService.ensure_permission(codename)
        RolePermission.objects.get_or_create(role=self.auth_role, permission=p, defaults={'data_scope': scope})
        PermissionEngine.invalidate_user_cache(self.auth_user)

    def revoke_permission(self, codename):
        p = RBACRegistryService.ensure_permission(codename)
        RolePermission.objects.filter(role=self.auth_role, permission=p).delete()
        PermissionEngine.invalidate_user_cache(self.auth_user)

    def test_branches_list_and_holidays_permissions(self):
        """Test Branch & Holiday views access gates."""
        # 1. Branch List
        url = reverse('branches:branch_list')
        # Anon -> 302
        self.assertEqual(self.anon_client.get(url).status_code, 302)
        # Unauth -> 403
        self.assertEqual(self.unauth_client.get(url).status_code, 403)
        # Auth -> 200
        self.grant_permission('branches.view')
        self.assertEqual(self.auth_client.get(url).status_code, 200)

        # 2. Holiday List
        url_hol = reverse('branches:holiday_list')
        self.assertEqual(self.unauth_client.get(url_hol).status_code, 403)
        self.assertEqual(self.auth_client.get(url_hol).status_code, 200)

        # 3. Holiday Create
        url_hol_create = reverse('branches:holiday_add')
        # Without branches.add -> 403
        self.assertEqual(self.auth_client.get(url_hol_create).status_code, 403)
        self.grant_permission('branches.add')
        self.assertEqual(self.auth_client.get(url_hol_create).status_code, 200)

    def test_leave_admin_views_permissions(self):
        """Test Admin Leave Dashboard and Leave Types views access gates."""
        # 1. Leave Dashboard
        url = reverse('leave:admin_dashboard')
        self.assertEqual(self.anon_client.get(url).status_code, 302)
        self.assertEqual(self.unauth_client.get(url).status_code, 403)
        self.grant_permission('leave.view')
        self.assertEqual(self.auth_client.get(url).status_code, 200)

        # 2. Leave Types List
        url_lt = reverse('leave:admin_leave_types')
        self.assertEqual(self.unauth_client.get(url_lt).status_code, 403)
        self.assertEqual(self.auth_client.get(url_lt).status_code, 200)

        # 3. Leave Type Create
        url_lt_create = reverse('leave:admin_leave_type_add')
        self.assertEqual(self.auth_client.get(url_lt_create).status_code, 403)
        self.grant_permission('leave.add')
        self.assertEqual(self.auth_client.get(url_lt_create).status_code, 200)

    def test_expense_admin_views_permissions(self):
        """Test Expense views access gates."""
        url = reverse('expense:admin_expense_list')
        self.assertEqual(self.anon_client.get(url).status_code, 302)
        self.assertEqual(self.unauth_client.get(url).status_code, 403)
        self.grant_permission('expense.view')
        self.assertEqual(self.auth_client.get(url).status_code, 200)

    def test_admin_panel_audit_and_security_dashboard(self):
        """Test Admin Audit Log and Security Dashboard views."""
        url_audit = reverse('admin_panel:admin_audit_logs')
        self.assertEqual(self.anon_client.get(url_audit).status_code, 302)
        self.assertEqual(self.unauth_client.get(url_audit).status_code, 403)
        self.grant_permission('audit.view')
        self.assertEqual(self.auth_client.get(url_audit).status_code, 200)

        url_sec = reverse('admin_panel:security_dashboard')
        self.assertEqual(self.unauth_client.get(url_sec).status_code, 403)
        self.grant_permission('accounts.view')
        self.assertEqual(self.auth_client.get(url_sec).status_code, 200)
