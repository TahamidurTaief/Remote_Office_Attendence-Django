from datetime import date
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from apps.employees.models import EmployeeProfile
from apps.projects.models import Project, ProjectType, ProjectTask
from apps.notifications.models import ActivityLog, Notification
from apps.notifications.dispatch import log_activity

User = get_user_model()


class ActivityLogTaskAssignmentTest(TestCase):
    def setUp(self):
        self.actor = User.objects.create_user(email='actor@example.com', password='password123')
        self.assignee_user = User.objects.create_user(email='assignee@example.com', password='password123')
        self.assignee_emp = EmployeeProfile.objects.create(
            user=self.assignee_user,
            full_name='Assignee Employee',
            phone='1234567890',
            employee_id='EMP_ASG_01',
            joined_date=date.today()
        )
        self.project_type = ProjectType.objects.create(name='HVAC')
        self.project = Project.objects.create(
            name='Test Project',
            client_name='Client',
            location='Location',
            project_type=self.project_type,
            start_date=date.today(),
            completion_date=date.today()
        )

    def test_task_assignment_creates_activity_log_and_notification(self):
        task = ProjectTask.objects.create(
            project=self.project,
            order=1,
            activity='Test Task Assignment',
            responsible_person=self.assignee_emp,
            status='In Progress'
        )

        # Dispatch task assignment activity log & notification
        log_activity(
            actor=self.actor,
            verb='task_assigned',
            target=task,
            metadata={'title': 'New Task Assigned: Test Task Assignment'},
            notify_users=[self.assignee_user]
        )

        # Verify exactly one ActivityLog and one Notification created
        self.assertEqual(ActivityLog.objects.count(), 1)
        log = ActivityLog.objects.get()
        self.assertEqual(log.actor, self.actor)
        self.assertEqual(log.verb, 'task_assigned')
        self.assertEqual(log.target, task)

        self.assertEqual(Notification.objects.count(), 1)
        notif = Notification.objects.get()
        self.assertEqual(notif.recipient, self.assignee_user)
        self.assertEqual(notif.employee, self.assignee_emp)


class NotificationViewPermissionsTest(TestCase):
    def setUp(self):
        self.staff_user = User.objects.create_user(
            email='staff@example.com',
            password='password123',
            role='employee'
        )
        self.notif = Notification.objects.create(
            recipient=self.staff_user,
            title='Staff Notif',
            message='Test message',
            notif_type='task_assigned'
        )

    def test_staff_user_can_access_notification_list_and_mark_all_read(self):
        self.client.login(email='staff@example.com', password='password123')
        
        response = self.client.get(reverse('notifications:list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Staff Notif')

        response = self.client.get(reverse('notifications:count'))
        self.assertEqual(response.status_code, 200)

        response = self.client.post(reverse('notifications:mark_all_read'))
        self.assertEqual(response.status_code, 302)
        self.notif.refresh_from_db()
        self.assertTrue(self.notif.is_read)


class ActivityTimelineViewsTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(email='admin@example.com', password='password123', role='admin')
        self.manager_user = User.objects.create_user(email='manager@example.com', password='password123', role='manager')
        self.manager_emp = EmployeeProfile.objects.create(
            user=self.manager_user,
            full_name='Manager One',
            phone='1112223334',
            employee_id='EMP_MGR_01',
            joined_date=date.today()
        )
        self.staff_user = User.objects.create_user(email='staff1@example.com', password='password123', role='staff')
        self.staff_emp = EmployeeProfile.objects.create(
            user=self.staff_user,
            full_name='Staff One',
            phone='5556667778',
            employee_id='EMP_STF_01',
            joined_date=date.today()
        )
        self.project_type = ProjectType.objects.create(name='HVAC')
        self.project = Project.objects.create(
            name='Timeline Project',
            client_name='Client',
            location='Dhaka',
            project_type=self.project_type,
            project_manager=self.manager_emp,
            start_date=date.today()
        )
        self.task = ProjectTask.objects.create(
            project=self.project,
            order=1,
            activity='Install Ducting',
            responsible_person=self.staff_emp,
            status='Not Started'
        )

    def test_activity_timeline_on_all_three_pages(self):
        # 1. Assign task
        log_activity(
            actor=self.manager_user,
            verb='task_assigned',
            target=self.task,
            notify_users=[self.staff_user]
        )

        # 2. Complete task
        self.task.status = 'Completed'
        self.task.save()  # Triggers task_completed via ProjectTask.save()

        # Admin project detail page
        self.client.login(email='admin@example.com', password='password123')
        resp_admin = self.client.get(reverse('projects:project_detail', kwargs={'pk': self.project.pk}))
        self.assertEqual(resp_admin.status_code, 200)
        self.assertIn('activities', resp_admin.context)
        activities_admin = list(resp_admin.context['activities'])
        self.assertEqual(len(activities_admin), 2)
        self.assertEqual(activities_admin[0].verb, 'task_completed')
        self.assertEqual(activities_admin[1].verb, 'task_assigned')

        # Manager project detail page
        self.client.login(email='manager@example.com', password='password123')
        resp_mgr = self.client.get(reverse('staff:my_project_detail', kwargs={'project_id': self.project.pk}))
        self.assertEqual(resp_mgr.status_code, 200)
        self.assertIn('activities', resp_mgr.context)
        activities_mgr = list(resp_mgr.context['activities'])
        self.assertEqual(len(activities_mgr), 2)

        # Staff profile page
        self.client.login(email='staff1@example.com', password='password123')
        resp_staff = self.client.get(reverse('staff:profile'))
        self.assertEqual(resp_staff.status_code, 200)
        self.assertIn('activities', resp_staff.context)
        activities_staff = list(resp_staff.context['activities'])
        self.assertEqual(len(activities_staff), 2)


