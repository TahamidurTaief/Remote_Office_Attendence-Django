from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from apps.branches.models import Branch
from apps.employees.models import EmployeeProfile
from apps.projects.models import Project, ProjectType, ProjectTask
from apps.attendance.models import Attendance
from apps.leave.models import LeaveType, LeaveBalance
import datetime

User = get_user_model()

class StaffProfileTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.password = 'staffpass123'
        
        # Create a branch
        self.branch = Branch.objects.create(
            name='Staff Test Branch',
            latitude=23.8103,
            longitude=90.4125,
            radius_meters=100
        )
        
        # Staff User 1
        self.user1 = User.objects.create_user(
            email='staff1@test.com',
            password=self.password,
            role='staff'
        )
        self.profile1 = EmployeeProfile.objects.create(
            user=self.user1,
            employee_id='EMP-S01',
            full_name='Staff Member One',
            phone='+8801711111111',
            joined_date=datetime.date(2026, 1, 1),
            branch=self.branch,
            is_active=True
        )

        # Staff User 2
        self.user2 = User.objects.create_user(
            email='staff2@test.com',
            password=self.password,
            role='staff'
        )
        self.profile2 = EmployeeProfile.objects.create(
            user=self.user2,
            employee_id='EMP-S02',
            full_name='Staff Member Two',
            phone='+8801722222222',
            joined_date=datetime.date(2026, 1, 1),
            branch=self.branch,
            is_active=True
        )

        # Create leave types
        self.leave_type = LeaveType.objects.create(
            name='Test Sick Leave',
            category='sick',
            default_days_per_year=12
        )

        # Set leave balance for user 1
        self.balance1 = LeaveBalance.objects.create(
            employee=self.profile1,
            leave_type=self.leave_type,
            year=timezone.localdate().year,
            total_days=12,
            used_days=2
        )

        # Create project and tasks
        self.project_type = ProjectType.objects.create(name='HVAC installation')
        self.project = Project.objects.create(
            name='Staff Project',
            client_name='Client A',
            location='Floor 3',
            project_type=self.project_type,
            start_date=datetime.date(2026, 1, 1),
            status='In Progress'
        )
        # Task assigned to User 1
        self.task1 = ProjectTask.objects.create(
            project=self.project,
            order=1,
            activity='Install Ducting',
            responsible_person=self.profile1,
            status='In Progress'
        )
        # Task assigned to User 2
        self.task2 = ProjectTask.objects.create(
            project=self.project,
            order=2,
            activity='Pipe connection',
            responsible_person=self.profile2,
            status='Not Started'
        )

        # Create attendance history
        self.today = timezone.localdate()
        self.att1 = Attendance.objects.create(
            employee=self.profile1,
            date=self.today,
            check_in_time=timezone.make_aware(datetime.datetime.combine(self.today, datetime.time(9, 0))),
            type='office',
            status='on_time',
            attendance_type='check_in'
        )

    def test_profile_displays_only_own_data(self):
        # 1. Log in as Staff User 1
        self.client.login(email='staff1@test.com', password=self.password)
        response = self.client.get(reverse('staff:profile'))
        self.assertEqual(response.status_code, 200)
        
        # Verify Staff 1's details are displayed
        self.assertEqual(response.context['employee'], self.profile1)
        
        # Verify Staff 1's leave balances, tasks, and attendance are in context
        self.assertIn(self.task1, response.context['assigned_tasks'])
        self.assertNotIn(self.task2, response.context['assigned_tasks'])
        self.assertEqual(len(response.context['leave_balances']), 1)
        self.assertEqual(response.context['leave_balances'][0]['remaining'], 10) # 12 - 2
        self.assertIn(self.att1, response.context['attendance_page'].object_list)
        
        # 2. Log in as Staff User 2
        self.client.login(email='staff2@test.com', password=self.password)
        response = self.client.get(reverse('staff:profile'))
        self.assertEqual(response.status_code, 200)
        
        # Verify Staff 2's details are displayed
        self.assertEqual(response.context['employee'], self.profile2)
        
        # Verify Staff 2's details show only their tasks (task2) and not user 1's tasks or attendance
        self.assertIn(self.task2, response.context['assigned_tasks'])
        self.assertNotIn(self.task1, response.context['assigned_tasks'])
        self.assertNotIn(self.att1, response.context['attendance_page'].object_list)

    def test_idor_and_admin_view_block(self):
        # Staff is blocked from accessing Admin's EmployeeDetailView by ID/PK
        self.client.login(email='staff1@test.com', password=self.password)
        
        # Attempt to access user 2's detail view under employees app (Admin view)
        url = reverse('employees:employee_detail', kwargs={'pk': self.profile2.pk})
        response = self.client.get(url)
        
        # Staff does not have admin permissions, should redirect to /staff/home/
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, '/staff/home/')
