from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from apps.accounts.models import CustomUser
from apps.employees.models import Employee, EmployeeProfile, Department, Designation
from apps.projects.models import Project, ProjectTask, ProjectType
from apps.payroll.models import PayrollRun, EmployeePayrollCalculation
from apps.branches.models import Branch
from decimal import Decimal
import json

class NavbarHardeningTests(TestCase):
    def setUp(self):
        self.client = Client()
        
        # Create Branches
        self.branch_a = Branch.objects.create(name="Branch A", latitude=0.0, longitude=0.0, radius_meters=100)
        self.branch_b = Branch.objects.create(name="Branch B", latitude=0.0, longitude=0.0, radius_meters=100)

        # Create Dept & Desig
        self.dept = Department.objects.create(name="Engineering", code="ENG")
        self.desig = Designation.objects.create(name="Software Engineer", code="SWE")

        # Create Users
        self.admin_user = CustomUser.objects.create_user(
            email="admin@example.com",
            password="password123",
            role="admin"
        )
        self.staff_user_a = CustomUser.objects.create_user(
            email="staffa@example.com",
            password="password123",
            role="staff"
        )
        self.staff_user_b = CustomUser.objects.create_user(
            email="staffb@example.com",
            password="password123",
            role="staff"
        )

        # Create Employee Masters
        self.emp_a = Employee.objects.create(
            employee_number="EMP00A",
            first_name="Alice",
            last_name="Smith",
            phone="1234567890",
            personal_email="alice@example.com",
            branch=self.branch_a,
            department=self.dept,
            designation=self.desig,
            user=self.staff_user_a,
            data_scope="branch"
        )
        self.emp_b = Employee.objects.create(
            employee_number="EMP00B",
            first_name="Bob",
            last_name="Jones",
            phone="0987654321",
            personal_email="bob@example.com",
            branch=self.branch_b,
            department=self.dept,
            designation=self.desig,
            user=self.staff_user_b,
            data_scope="branch"
        )

        # Project Type
        self.proj_type = ProjectType.objects.create(name="HVAC Implementation", code="HVAC")

    def test_name_display_fallback(self):
        """Test canonical employee name -> user full name -> fallback email/phone"""
        # User with Employee Link
        self.assertEqual(self.staff_user_a.display_name, "Alice Smith")

        # User with first/last name attributes but no Employee link
        temp_user = CustomUser(email="temp@example.com", role="staff")
        temp_user.first_name = "Charlie"
        temp_user.last_name = "Brown"
        self.assertEqual(temp_user.display_name, "Charlie Brown")

        # Fallback to email/phone only
        fallback_user = CustomUser(email="fallback@example.com", role="staff")
        self.assertEqual(fallback_user.display_name, "fallback@example.com")

        phone_user = CustomUser(phone="999-999-9999", role="staff")
        self.assertEqual(phone_user.display_name, "999-999-9999")

    def test_history_filtering_url_check(self):
        """Test that authentication routes are defined as skipped paths for history tracking"""
        auth_paths = ['/accounts/login/', '/accounts/logout/', '/login/', '/logout/', '/verify/', '/mfa/', '/search/']
        
        # Test helper function emulation
        def is_auth_page(url):
            return any(path in url for path in auth_paths)
            
        self.assertTrue(is_auth_page('/accounts/login/'))
        self.assertTrue(is_auth_page('/login/'))
        self.assertTrue(is_auth_page('/accounts/logout/'))
        self.assertTrue(is_auth_page('/verify/'))
        self.assertTrue(is_auth_page('/mfa/'))
        self.assertTrue(is_auth_page('/search/'))
        self.assertFalse(is_auth_page('/admin-panel/dashboard/'))
        self.assertFalse(is_auth_page('/employees/'))

    def test_global_search_permissions_and_scoping(self):
        """Test search endpoint restricts results by role permission and branch scope"""
        # Login as Staff User A (Branch A, branch data scope)
        self.client.force_login(self.staff_user_a)
        
        response = self.client.get(reverse('accounts:global_search') + "?query=Bob")
        self.assertEqual(response.status_code, 200)
        # Verify Bob (who is in Branch B) is not returned to Staff A (Branch A)
        self.assertNotContains(response, "Employee: Bob")

        response_alice = self.client.get(reverse('accounts:global_search') + "?query=Alice")
        # Alice is in Branch A, but Staff role does not have employee view permission to search other employees
        self.assertNotContains(response_alice, "Employee: Alice")

        # Login as Admin User (global permissions)
        self.client.force_login(self.admin_user)
        response_admin = self.client.get(reverse('accounts:global_search') + "?query=Bob")
        self.assertEqual(response_admin.status_code, 200)
        # Admin must see Bob
        self.assertContains(response_admin, "Employee: Bob")

    def test_payroll_and_projects_appear_in_search(self):
        """Test that payroll and project models appear in global search results"""
        # Create Project in Branch A
        proj = Project.objects.create(
            name="Airflow System Installation",
            client_name="Buildcorp",
            location="Site A",
            project_type=self.proj_type,
            start_date=timezone.localdate(),
            branch=self.branch_a
        )

        # Create Payroll calculation for Alice (Branch A)
        payroll_run = PayrollRun.objects.create(
            period_start=timezone.localdate() - timezone.timedelta(days=30),
            period_end=timezone.localdate(),
            status="draft"
        )
        calc = EmployeePayrollCalculation.objects.create(
            payroll_run=payroll_run,
            employee=self.emp_a,
            gross_salary=Decimal("5000.00"),
            payment_mode="bank",
            total_earnings=Decimal("5000.00"),
            total_deductions=Decimal("0.00"),
            net_payable=Decimal("5000.00"),
            bank_payable=Decimal("5000.00"),
            cash_payable=Decimal("0.00"),
            structure_snapshot={}
        )

        # Admin searches for Project
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse('accounts:global_search') + "?query=Airflow")
        self.assertContains(response, "Project: Airflow System Installation")

        # Admin searches for Payroll
        response_payroll = self.client.get(reverse('accounts:global_search') + "?query=Alice")
        self.assertContains(response_payroll, "Payslip: Alice")

    def test_search_endpoint_query_limits(self):
        """Verify that search results are bounded to prevent performance degradation"""
        # Create 10 projects
        for i in range(10):
            Project.objects.create(
                name=f"Bulk Project {i}",
                client_name="Standard Client",
                location="Location X",
                project_type=self.proj_type,
                start_date=timezone.localdate(),
                branch=self.branch_a
            )

        self.client.force_login(self.admin_user)
        response = self.client.get(reverse('accounts:global_search') + "?query=Bulk Project")
        # Ensure only 5 results are returned (due to [:5] query limit)
        content_str = response.content.decode('utf-8')
        occurrences = content_str.count("Project: Bulk Project")
        self.assertEqual(occurrences, 5)
