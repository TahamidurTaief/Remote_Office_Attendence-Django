from django.test import TestCase
from django.template.loader import render_to_string


class GlobalSearchTest(TestCase):
    """Test suite for the Global Search component (cotton/command-palette.html)."""

    def test_global_search_template_renders(self):
        """Verify command-palette contains employee and project navigation items."""
        rendered = render_to_string('cotton/command-palette.html', {})
        self.assertIn('Employee', rendered)
        self.assertIn('Project', rendered)
        self.assertIn('Dashboard', rendered)
        self.assertIn('Attendance', rendered)


from django.contrib.auth import get_user_model
from django.urls import reverse
from apps.employees.models import Employee, EmployeeStatus, Department, Designation
from apps.branches.models import Branch
from apps.accounts.models import UserSession

User = get_user_model()

class GlobalSearchEndpointTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email='globsearchadmin@test.com', password='Password123!')
        self.branch = Branch.objects.create(name='GlobalHQ', latitude=23.8, longitude=90.4)
        self.dept = Department.objects.create(name='DevOps', code='DEV')
        self.desig = Designation.objects.create(name='Cloud Engineer', code='CE')

        self.emp1 = Employee.objects.create(
            employee_number='EMP-SEARCH-101',
            first_name='GlobalSearchFirst',
            last_name='UserOne',
            personal_email='globsearch1@test.com',
            branch=self.branch,
            department=self.dept,
            designation=self.desig,
            status=EmployeeStatus.ACTIVE
        )

    def test_global_search_json_and_htmx(self):
        self.client.force_login(self.admin)
        UserSession.objects.create(user=self.admin, session_key=self.client.session.session_key, is_active=True)

        # 1. JSON search
        url = reverse('admin_panel:global_search') + '?q=GlobalSearchFirst'
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(len(data['results']), 1)
        self.assertEqual(data['results'][0]['employee_number'], 'EMP-SEARCH-101')

        # 2. HTMX search
        res_htmx = self.client.get(url, HTTP_HX_REQUEST='true')
        self.assertEqual(res_htmx.status_code, 200)
        self.assertContains(res_htmx, 'GlobalSearchFirst UserOne')
