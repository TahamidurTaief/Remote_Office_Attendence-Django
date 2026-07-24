from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from apps.branches.models import Branch
from apps.employees.models import EmployeeProfile
from apps.projects.models import Project, ProjectType
from apps.attendance.models import Attendance
from apps.leave.models import LeaveType, LeaveBalance, LeaveRequest
import datetime

User = get_user_model()

class DashboardScopingTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.password = 'testpass123'
        
        # 1. Create Branches
        self.branch1 = Branch.objects.create(
            name='Branch One',
            latitude=23.8103,
            longitude=90.4125,
            radius_meters=100
        )
        self.branch2 = Branch.objects.create(
            name='Branch Two',
            latitude=23.8103,
            longitude=90.4125,
            radius_meters=100
        )

        # 2. Create Users & Employee Profiles
        # Admin User
        self.admin_user = User.objects.create_user(
            email='admin@test.com',
            password=self.password,
            role='admin'
        )

        # Manager 1: Linked to Branch 1
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

        # Manager 2: No Branch link, manages projects
        self.manager2_user = User.objects.create_user(
            email='mgr2@test.com',
            password=self.password,
            role='manager'
        )
        self.manager2_profile = EmployeeProfile.objects.create(
            user=self.manager2_user,
            employee_id='MGR-002',
            full_name='Manager Two',
            phone='+8801700000011',
            joined_date=datetime.date(2026, 1, 1),
            branch=None,
            is_active=True
        )

        # Staff User
        self.staff_user = User.objects.create_user(
            email='staff@test.com',
            password=self.password,
            role='staff'
        )

        # 3. Create regular employees
        # Employee 1 in Branch 1
        self.emp1_user = User.objects.create_user(
            email='emp1@test.com',
            password=self.password,
            role='staff'
        )
        self.emp1_profile = EmployeeProfile.objects.create(
            user=self.emp1_user,
            employee_id='EMP-001',
            full_name='Employee One',
            phone='+8801700000001',
            joined_date=datetime.date(2026, 1, 1),
            branch=self.branch1,
            is_active=True
        )

        # Employee 2 in Branch 2
        self.emp2_user = User.objects.create_user(
            email='emp2@test.com',
            password=self.password,
            role='staff'
        )
        self.emp2_profile = EmployeeProfile.objects.create(
            user=self.emp2_user,
            employee_id='EMP-002',
            full_name='Employee Two',
            phone='+8801700000002',
            joined_date=datetime.date(2026, 1, 1),
            branch=self.branch2,
            is_active=True
        )

        # 4. Create Project
        self.project_type = ProjectType.objects.create(name='Construction')
        self.project = Project.objects.create(
            name='Test Project',
            client_name='Test Client',
            location='Test Location',
            project_type=self.project_type,
            start_date=datetime.date(2026, 1, 1),
            status='In Progress'
        )
        self.project.project_managers.add(self.manager2_profile)
        self.project.site_engineers.add(self.emp2_profile)

        # 5. Create Attendance records for today
        self.today = timezone.localdate()
        self.check_in_time = timezone.make_aware(datetime.datetime.combine(self.today, datetime.time(9, 0)))
        
        # Emp1 (Branch 1) checked in
        self.att1 = Attendance.objects.create(
            employee=self.emp1_profile,
            date=self.today,
            check_in_time=self.check_in_time,
            type='office',
            status='on_time',
            attendance_type='check_in'
        )
        
        # Emp2 (Branch 2, Project Site Engineer) checked in
        self.att2 = Attendance.objects.create(
            employee=self.emp2_profile,
            date=self.today,
            check_in_time=self.check_in_time,
            type='office',
            status='on_time',
            attendance_type='check_in'
        )

    def test_admin_dashboard_full_access(self):
        self.client.login(email='admin@test.com', password=self.password)
        response = self.client.get(reverse('admin_panel:dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['can_view_all'])
        # Admin should see both branches
        self.assertEqual(len(response.context['branches']), 2)
        # Present count should show 2 (both employees)
        self.assertEqual(response.context['present_today'], 2)

    def test_manager_branch_scoped_access(self):
        self.client.login(email='mgr1@test.com', password=self.password)
        response = self.client.get(reverse('admin_panel:dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_view_all'])
        # Manager 1 should only see their branch (Branch 1)
        self.assertEqual(list(response.context['branches']), [self.branch1])
        # Employee list in context should only contain employees of Branch 1 (Emp 1 and Manager 1)
        employees_in_ctx = list(response.context['employees'])
        self.assertIn(self.emp1_profile, employees_in_ctx)
        self.assertNotIn(self.emp2_profile, employees_in_ctx)
        # Stats should be scoped to Branch 1 (only Emp 1 is present)
        self.assertEqual(response.context['present_today'], 1)

    def test_manager_project_scoped_access(self):
        self.client.login(email='mgr2@test.com', password=self.password)
        response = self.client.get(reverse('admin_panel:dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_view_all'])
        self.assertTrue(response.context['report_missing_branch'])
        # Manager 2 is project-scoped (no branch)
        self.assertEqual(list(response.context['branches']), [])
        # Employee list should only contain their project employees (Emp 2) and they might not see Emp 1
        employees_in_ctx = list(response.context['employees'])
        self.assertIn(self.emp2_profile, employees_in_ctx)
        self.assertNotIn(self.emp1_profile, employees_in_ctx)
        # Present today should show 1 (Emp 2)
        self.assertEqual(response.context['present_today'], 1)

    def test_staff_blocked(self):
        self.client.login(email='staff@test.com', password=self.password)
        response = self.client.get(reverse('admin_panel:dashboard'))
        # Should redirect to /staff/home/
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, '/staff/home/')
