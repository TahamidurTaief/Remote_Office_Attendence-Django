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


class ProjectOwnershipTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.password = 'managerpass123'
        
        # Create a branch
        self.branch = Branch.objects.create(
            name='Test Branch',
            latitude=23.8103,
            longitude=90.4125,
            radius_meters=100
        )
        
        # Manager A
        self.user_a = User.objects.create_user(
            email='manager_a@test.com',
            password=self.password,
            role='manager'
        )
        self.profile_a = EmployeeProfile.objects.create(
            user=self.user_a,
            employee_id='EMP-M01',
            full_name='Manager A',
            phone='+8801711111111',
            joined_date=datetime.date(2026, 1, 1),
            branch=self.branch,
            is_active=True
        )

        # Manager B
        self.user_b = User.objects.create_user(
            email='manager_b@test.com',
            password=self.password,
            role='manager'
        )
        self.profile_b = EmployeeProfile.objects.create(
            user=self.user_b,
            employee_id='EMP-M02',
            full_name='Manager B',
            phone='+8801722222222',
            joined_date=datetime.date(2026, 1, 1),
            branch=self.branch,
            is_active=True
        )

        # Project type
        self.project_type = ProjectType.objects.create(name='Project Ownership Test Type')

        # Project X owned by Manager A
        self.project_x = Project.objects.create(
            name='Project X',
            client_name='Client X',
            location='Location X',
            project_type=self.project_type,
            start_date=datetime.date(2026, 1, 1),
            status='In Progress',
            branch=self.branch
        )
        self.project_x.project_managers.add(self.profile_a)

        # Project Y owned by Manager B
        self.project_y = Project.objects.create(
            name='Project Y',
            client_name='Client Y',
            location='Location Y',
            project_type=self.project_type,
            start_date=datetime.date(2026, 1, 1),
            status='In Progress',
            branch=self.branch
        )
        self.project_y.project_managers.add(self.profile_b)

    def test_manager_a_cannot_view_project_y(self):
        # Log in as Manager A
        self.client.login(email='manager_a@test.com', password=self.password)
        # Attempt to GET /staff/my-projects/<Project Y's id>/
        url = reverse('staff:my_project_detail', kwargs={'project_id': self.project_y.pk})
        response = self.client.get(url)
        # Confirm returns 403 (PermissionDenied)
        self.assertEqual(response.status_code, 403)

    def test_manager_a_cannot_add_task_to_project_y(self):
        # Log in as Manager A
        self.client.login(email='manager_a@test.com', password=self.password)
        # Attempt to POST to my_project_add_task with Project Y's id
        url = reverse('staff:my_project_add_task', kwargs={'project_id': self.project_y.pk})
        data = {
            'activity': 'Illegal Task Creation',
            'points': '10',
        }
        response = self.client.post(url, data=data)
        # Confirm returns 403 (PermissionDenied)
        self.assertEqual(response.status_code, 403)
        # Confirm no task was created on project Y
        self.assertEqual(self.project_y.tasks.count(), 0)

    def test_manager_a_can_view_project_x(self):
        self.client.login(email='manager_a@test.com', password=self.password)
        url = reverse('staff:my_project_detail', kwargs={'project_id': self.project_x.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_manager_a_can_add_task_to_project_x(self):
        self.client.login(email='manager_a@test.com', password=self.password)
        url = reverse('staff:my_project_add_task', kwargs={'project_id': self.project_x.pk})
        data = {
            'activity': 'Legal Task Creation',
            'points': '10',
        }
        response = self.client.post(url, data=data)
        self.assertEqual(response.status_code, 302) # redirect on success
        self.assertEqual(self.project_x.tasks.count(), 1)

    def test_manager_can_edit_task_on_owned_project(self):
        from apps.projects.models import ProjectTask
        task = ProjectTask.objects.create(
            project=self.project_x,
            order=1,
            activity='Task to Edit',
            points=10,
            status='Not Started'
        )
        self.client.login(email='manager_a@test.com', password=self.password)
        url = reverse('staff:my_project_edit_task', kwargs={'task_id': task.pk})
        data = {
            'activity': 'Task Edited Successfully',
            'points': '20',
            'status': 'In Progress',
            'remarks': 'Manager Feedback Note',
        }
        response = self.client.post(url, data=data)
        self.assertEqual(response.status_code, 302)
        task.refresh_from_db()
        self.assertEqual(task.activity, 'Task Edited Successfully')
        self.assertEqual(task.points, 20)
        self.assertEqual(task.status, 'In Progress')
        self.assertEqual(task.remarks, 'Manager Feedback Note')

    def test_manager_cannot_edit_task_on_unowned_project(self):
        from apps.projects.models import ProjectTask
        task = ProjectTask.objects.create(
            project=self.project_y,
            order=1,
            activity='Task on Project Y',
            points=10,
            status='Not Started'
        )
        self.client.login(email='manager_a@test.com', password=self.password)
        url = reverse('staff:my_project_edit_task', kwargs={'task_id': task.pk})
        data = {
            'activity': 'Illegal Edit Try',
            'points': '20',
            'status': 'In Progress',
        }
        response = self.client.post(url, data=data)
        self.assertEqual(response.status_code, 403)
        task.refresh_from_db()
        self.assertEqual(task.activity, 'Task on Project Y')

    def test_manager_can_delete_task_on_owned_project(self):
        from apps.projects.models import ProjectTask
        task = ProjectTask.objects.create(
            project=self.project_x,
            order=1,
            activity='Task to Delete',
            points=10,
            status='Not Started'
        )
        self.client.login(email='manager_a@test.com', password=self.password)
        url = reverse('staff:my_project_delete_task', kwargs={'task_id': task.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.project_x.tasks.count(), 0)

    def test_manager_cannot_delete_task_on_unowned_project(self):
        from apps.projects.models import ProjectTask
        task = ProjectTask.objects.create(
            project=self.project_y,
            order=1,
            activity='Task to Delete on Project Y',
            points=10,
            status='Not Started'
        )
        self.client.login(email='manager_a@test.com', password=self.password)
        url = reverse('staff:my_project_delete_task', kwargs={'task_id': task.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.project_y.tasks.count(), 1)


class StaffTasksViewTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from apps.branches.models import Branch
        from apps.employees.models import EmployeeProfile
        from apps.projects.models import Project, ProjectTask, ProjectType

        User = get_user_model()
        self.password = 'pass123'
        
        self.branch = Branch.objects.create(
            name='Dhaka Branch',
            latitude=23.8118,
            longitude=90.4125,
            radius_meters=100
        )
        
        self.user = User.objects.create_user(
            email='staff_tasks@test.com',
            phone='+8801700000333',
            role='staff',
            password=self.password
        )
        
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            full_name='Staff Tasks Member',
            branch=self.branch,
            joined_date=datetime.date.today(),
            phone='+8801700000333',
            employee_id='EMP999'
        )

        self.project_type = ProjectType.objects.create(name='HVAC Type')
        self.project = Project.objects.create(
            name='Tasks Project',
            client_name='Client Tasks',
            location='Loc Tasks',
            project_type=self.project_type,
            start_date=datetime.date.today(),
            branch=self.branch
        )

        self.pending_task = ProjectTask.objects.create(
            project=self.project,
            order=1,
            activity='Pending Project Task',
            status='In Progress',
            responsible_person=self.profile
        )

        self.completed_task = ProjectTask.objects.create(
            project=self.project,
            order=2,
            activity='Completed Project Task',
            status='Completed',
            responsible_person=self.profile
        )

    def test_my_tasks_page_unauthenticated_redirects(self):
        url = reverse('staff:my_tasks')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_my_tasks_page_authenticated_succeeds(self):
        self.client.login(username='+8801700000333', password=self.password)
        url = reverse('staff:my_tasks')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Pending Project Task')

    def test_my_tasks_page_filter_completed(self):
        self.client.login(username='+8801700000333', password=self.password)
        url = reverse('staff:my_tasks') + '?status=completed'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Completed Project Task')
        self.assertNotContains(response, 'Pending Project Task')

