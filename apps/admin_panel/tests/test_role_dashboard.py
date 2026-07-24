from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from apps.employees.models import Employee, EmployeeProfile, Department, Designation
from apps.branches.models import Branch
from apps.admin_panel.dashboard_services import (
    determine_user_role_variant,
    get_employee_dashboard_data,
    get_manager_dashboard_data,
    get_hr_dashboard_data,
    get_admin_dashboard_data,
)

User = get_user_model()


class RoleBasedDashboardTestCase(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name="Main Branch", address="123 Main St", latitude=23.8103, longitude=90.4125)
        self.dept = Department.objects.create(name="Engineering", code="ENG")
        self.desig = Designation.objects.create(name="Software Engineer", code="SE")

        # Admin user
        self.admin_user = User.objects.create_superuser(
            email="admin@example.com", password="password123", role="admin"
        )

        # Manager user & employee master
        self.manager_user = User.objects.create_user(
            email="manager@example.com", password="password123", role="manager"
        )
        self.manager_emp = Employee.objects.create(
            employee_number="EMP001",
            first_name="Manager",
            last_name="One",
            user=self.manager_user,
            branch=self.branch,
            department=self.dept,
            designation=self.desig,
        )

        # Staff user & employee master (reports to manager_emp)
        self.staff_user = User.objects.create_user(
            email="staff@example.com", password="password123", role="staff"
        )
        self.staff_emp = Employee.objects.create(
            employee_number="EMP002",
            first_name="Staff",
            last_name="One",
            user=self.staff_user,
            reporting_manager=self.manager_emp,
            branch=self.branch,
            department=self.dept,
            designation=self.desig,
        )
        self.staff_profile = EmployeeProfile.objects.create(
            user=self.staff_user,
            master_employee=self.staff_emp,
            employee_id="EMP002",
            full_name="Staff One",
            branch=self.branch,
            phone="1234567890",
            joined_date="2026-01-01",
        )

    def test_determine_user_role_variant(self):
        self.assertEqual(determine_user_role_variant(self.admin_user), "admin")
        self.assertEqual(determine_user_role_variant(self.manager_user), "manager")
        self.assertEqual(determine_user_role_variant(self.staff_user), "employee")

    def test_get_employee_dashboard_data(self):
        data = get_employee_dashboard_data(self.staff_user)
        self.assertIn("today_attendance", data)
        self.assertIn("assigned_tasks", data)
        self.assertIn("my_leave_requests", data)

    def test_get_manager_dashboard_data(self):
        data = get_manager_dashboard_data(self.manager_user)
        self.assertEqual(data["team_count"], 1)

    def test_dashboard_view_renders_correct_template(self):
        client = Client()
        client.force_login(self.staff_user)
        response = client.get("/dashboard/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "dashboard/employee_dashboard.html")

        client.force_login(self.manager_user)
        response = client.get("/dashboard/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "dashboard/manager_dashboard.html")

        client.force_login(self.admin_user)
        response = client.get("/dashboard/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "dashboard/admin_dashboard.html")
