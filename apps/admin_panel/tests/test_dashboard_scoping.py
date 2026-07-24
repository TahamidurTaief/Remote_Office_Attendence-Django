from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from apps.branches.models import Branch
from apps.employees.models import EmployeeProfile
import datetime

User = get_user_model()

class DashboardScopingTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.password = 'testpass123'

        self.branch1 = Branch.objects.create(
            name='Branch One',
            latitude=23.8103,
            longitude=90.4125,
            radius_meters=100
        )

        self.admin_user = User.objects.create_user(
            email='admin@test.com',
            password=self.password,
            role='admin'
        )

        self.manager1_user = User.objects.create_user(
            email='mgr1@test.com',
            password=self.password,
            role='manager'
        )
        self.manager1_profile = EmployeeProfile.objects.create(
            user=self.manager1_user,
            employee_id='MGR-001',
            full_name='Manager One',
            phone='+8801700000010',
            joined_date=datetime.date(2026, 1, 1),
            branch=self.branch1,
            is_active=True
        )

        self.staff_user = User.objects.create_user(
            email='staff@test.com',
            password=self.password,
            role='staff'
        )

    def test_admin_dashboard_renders(self):
        self.client.login(email='admin@test.com', password=self.password)
        response = self.client.get(reverse('admin_panel:dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'dashboard/admin_dashboard.html')
        self.assertEqual(response.context['role_variant'], 'admin')

    def test_manager_dashboard_renders(self):
        self.client.login(email='mgr1@test.com', password=self.password)
        response = self.client.get(reverse('admin_panel:dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'dashboard/manager_dashboard.html')
        self.assertEqual(response.context['role_variant'], 'manager')

    def test_staff_access_employee_dashboard(self):
        self.client.login(email='staff@test.com', password=self.password)
        response = self.client.get(reverse('admin_panel:dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'dashboard/employee_dashboard.html')
        self.assertEqual(response.context['role_variant'], 'employee')
