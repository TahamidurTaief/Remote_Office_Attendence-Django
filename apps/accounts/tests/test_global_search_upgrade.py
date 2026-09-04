import json
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth import get_user_model

from apps.accounts.models import Role, Module, Action, Permission, UserRoleAssignment
from apps.accounts.search_service import GlobalSearchService
from apps.branches.models import Branch
from apps.employees.models import Employee, Department, Designation
from apps.projects.models import Project, ProjectType

User = get_user_model()


class GlobalSearchUpgradeTests(TestCase):
    """
    Comprehensive test suite for the Global Search Upgrade:
    - Role, access, permission, module, menu keyword searches
    - Exact and partial matches
    - Bangla & English aliases
    - Result deduplication and ranking
    - Strict server-side permission filtering
    - Non-disclosure of protected roles & inactive data
    - Operational search backward compatibility
    - Error handling & recovery
    """

    def setUp(self):
        # 1. Create Branches
        self.branch_a = Branch.objects.create(
            name="Dhaka HQ",
            address="Gulshan 2, Dhaka",
            latitude=Decimal("23.792500"),
            longitude=Decimal("90.407800"),
            radius_meters=100
        )
        self.branch_b = Branch.objects.create(
            name="Chittagong Office",
            address="Agrabad, Chittagong",
            latitude=Decimal("22.335000"),
            longitude=Decimal("91.832000"),
            radius_meters=100
        )

        # 2. Roles & Permissions setup
        self.role_super = Role.objects.create(
            name="Super Admin",
            code="admin",
            is_system_protected=False,
            is_active=True
        )
        self.role_hr = Role.objects.create(
            name="HR Manager",
            code="hr_manager",
            is_system_protected=False,
            is_active=True
        )
        self.role_accountant = Role.objects.create(
            name="Senior Accountant",
            code="accountant",
            is_system_protected=False,
            is_active=True
        )
        self.role_inactive = Role.objects.create(
            name="Inactive Auditor",
            code="inactive_auditor",
            is_system_protected=False,
            is_active=False
        )
        self.role_protected = Role.objects.create(
            name="System Root Owner",
            code="system_owner",
            is_system_protected=True,
            is_active=True
        )

        # 3. Dynamic Modules & Permissions
        self.mod_payroll = Module.objects.create(
            name="Payroll Management",
            code="payroll",
            description="Manage employee payroll and salary calculations",
            icon="wallet",
            is_active=True,
            sort_order=1
        )
        self.mod_disabled = Module.objects.create(
            name="Disabled Experimental Module",
            code="disabled_mod",
            description="Should never appear in search",
            icon="flask",
            is_active=False,
            sort_order=99
        )

        self.act_view = Action.objects.create(name="View", code="view")
        self.act_execute = Action.objects.create(name="Execute", code="execute")

        self.perm_payroll_view = Permission.objects.create(
            module=self.mod_payroll,
            action=self.act_view,
            name="View Payroll Runs",
            description="Ability to view payroll runs and reports"
        )
        self.perm_payroll_run = Permission.objects.create(
            module=self.mod_payroll,
            action=self.act_execute,
            name="Execute Monthly Payroll",
            description="Ability to execute monthly payroll runs"
        )

        # 4. Users
        # Superuser / Admin
        self.admin_user = User.objects.create_superuser(
            email="admin_search@fieldtrack.local",
            password="TestPassword123!",
            role="admin"
        )

        # HR User
        self.hr_user = User.objects.create_user(
            email="hr_search@fieldtrack.local",
            password="TestPassword123!",
            role="hr"
        )

        # Regular Staff User
        self.staff_user = User.objects.create_user(
            email="staff_search@fieldtrack.local",
            password="TestPassword123!",
            role="staff"
        )

        # Employee masters
        self.emp_admin = Employee.objects.create(
            user=self.admin_user,
            employee_number="EMP-ADM-01",
            first_name="Admin",
            last_name="Commander",
            branch=self.branch_a,
            data_scope="global"
        )
        self.emp_hr = Employee.objects.create(
            user=self.hr_user,
            employee_number="EMP-HR-01",
            first_name="Helen",
            last_name="Reader",
            branch=self.branch_a,
            data_scope="branch"
        )
        self.emp_staff = Employee.objects.create(
            user=self.staff_user,
            employee_number="EMP-STF-01",
            first_name="Sam",
            last_name="Worker",
            branch=self.branch_a,
            data_scope="branch"
        )

    def test_search_role_keywords_returns_roles_and_matrix_destinations_for_admin(self):
        """Searching 'role', 'roles', or 'matrix' returns Role and Access destinations for Admin."""
        self.client.force_login(self.admin_user)
        for term in ['role', 'roles']:
            response = self.client.get(reverse('accounts:global_search') + f"?query={term}")
            self.assertEqual(response.status_code, 200)
            content = response.content.decode('utf-8')
            self.assertIn("System Roles &amp; Access", content)

        # 'matrix' returns Access Matrix
        resp_matrix = self.client.get(reverse('accounts:global_search') + "?query=matrix")
        self.assertEqual(resp_matrix.status_code, 200)
        self.assertIn("Access Matrix", resp_matrix.content.decode('utf-8'))

        # 'access' returns Access Matrix or System Roles
        resp_access = self.client.get(reverse('accounts:global_search') + "?query=access")
        self.assertEqual(resp_access.status_code, 200)
        content_access = resp_access.content.decode('utf-8')
        self.assertTrue("Access Matrix" in content_access or "System Roles &amp; Access" in content_access)

    def test_bangla_keywords_find_matching_destinations(self):
        """Searching Bangla terms 'রোল', 'ম্যাট্রিক্স', 'অনুমতি', 'মডিউল' resolves correctly."""
        self.client.force_login(self.admin_user)

        # 'রোল' -> System Roles & Access
        resp_role = self.client.get(reverse('accounts:global_search') + "?query=রোল")
        self.assertEqual(resp_role.status_code, 200)
        self.assertIn("System Roles &amp; Access", resp_role.content.decode('utf-8'))

        # 'ম্যাট্রিক্স' -> Access Matrix
        resp_matrix = self.client.get(reverse('accounts:global_search') + "?query=ম্যাট্রিক্স")
        self.assertEqual(resp_matrix.status_code, 200)
        self.assertIn("Access Matrix", resp_matrix.content.decode('utf-8'))

        # 'অনুমতি' -> Permissions / Access Matrix
        resp_perm = self.client.get(reverse('accounts:global_search') + "?query=অনুমতি")
        self.assertEqual(resp_perm.status_code, 200)
        content_perm = resp_perm.content.decode('utf-8')
        self.assertTrue("Access Matrix" in content_perm or "System Roles" in content_perm)

    def test_dynamic_role_search_discovers_active_roles(self):
        """Searching for 'Accountant' finds 'Senior Accountant' and links to role destinations."""
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse('accounts:global_search') + "?query=Accountant")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn("Senior Accountant", content)

    def test_protected_role_is_never_disclosed(self):
        """Protected roles (system_owner, is_system_protected=True) must NEVER appear in search."""
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse('accounts:global_search') + "?query=System Root Owner")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertNotIn("System Root Owner", content)

        # Also search by code
        response_code = self.client.get(reverse('accounts:global_search') + "?query=system_owner")
        self.assertEqual(response_code.status_code, 200)
        self.assertNotIn("System Root Owner", response_code.content.decode('utf-8'))

    def test_inactive_roles_and_disabled_modules_excluded(self):
        """Inactive roles and disabled modules must never appear in search results."""
        self.client.force_login(self.admin_user)

        # Inactive role
        resp_role = self.client.get(reverse('accounts:global_search') + "?query=Inactive Auditor")
        self.assertEqual(resp_role.status_code, 200)
        self.assertNotIn("Inactive Auditor", resp_role.content.decode('utf-8'))

        # Disabled module
        resp_mod = self.client.get(reverse('accounts:global_search') + "?query=Disabled Experimental Module")
        self.assertEqual(resp_mod.status_code, 200)
        self.assertNotIn("Disabled Experimental Module", resp_mod.content.decode('utf-8'))

    def test_permission_filtering_staff_cannot_see_role_administration(self):
        """Regular staff user must NOT see Role administration, Access Matrix, or Security Policies."""
        self.client.force_login(self.staff_user)

        resp_roles = self.client.get(reverse('accounts:global_search') + "?query=roles")
        self.assertEqual(resp_roles.status_code, 200)
        content = resp_roles.content.decode('utf-8')
        self.assertNotIn("System Roles &amp; Access", content)
        self.assertNotIn("Access Matrix", content)

        resp_policies = self.client.get(reverse('accounts:global_search') + "?query=security policies")
        self.assertEqual(resp_policies.status_code, 200)
        self.assertNotIn("Security Policies", resp_policies.content.decode('utf-8'))

    def test_dynamic_module_and_permission_search(self):
        """Searching module or permission names returns valid, accessible navigation destinations."""
        self.client.force_login(self.admin_user)

        # Module name
        resp_mod = self.client.get(reverse('accounts:global_search') + "?query=Payroll Management")
        self.assertEqual(resp_mod.status_code, 200)
        self.assertIn("Payroll Management", resp_mod.content.decode('utf-8'))

        # Permission name
        resp_perm = self.client.get(reverse('accounts:global_search') + "?query=Execute Monthly Payroll")
        self.assertEqual(resp_perm.status_code, 200)
        self.assertIn("Execute Monthly Payroll", resp_perm.content.decode('utf-8'))

    def test_result_deduplication(self):
        """Repeated alias hits or overlapping catalog entries must be deduplicated by (href, label)."""
        results = GlobalSearchService.search(self.admin_user, "role")
        seen = set()
        for item in results:
            key = (item['href'].strip().rstrip('/'), item['label'].strip().lower())
            self.assertNotIn(key, seen, f"Duplicate result detected: {item['label']} ({item['href']})")
            seen.add(key)

    def test_empty_query_returns_safe_scoped_routes(self):
        """Empty query returns curated initial quick navigation items without error."""
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse('accounts:global_search') + "?query=")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn("Dashboard", content)
        self.assertIn("Employee Directory", content)

    def test_admin_panel_global_search_returns_nav_results(self):
        """Admin panel global search returns roles and navigation results in HTMX and JSON."""
        self.client.force_login(self.admin_user)

        # HTMX request
        resp_htmx = self.client.get(
            reverse('admin_panel:global_search') + "?query=roles",
            HTTP_HX_REQUEST='true'
        )
        self.assertEqual(resp_htmx.status_code, 200)
        self.assertIn("Roles, Permissions & Navigation", resp_htmx.content.decode('utf-8'))

        # JSON request
        resp_json = self.client.get(reverse('admin_panel:global_search') + "?query=roles")
        self.assertEqual(resp_json.status_code, 200)
        data = json.loads(resp_json.content.decode('utf-8'))
        self.assertIn('nav_results', data)
        self.assertTrue(len(data['nav_results']) > 0)
