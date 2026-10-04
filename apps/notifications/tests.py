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
            is_project_manager=True,
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
            start_date=date.today()
        )
        self.project.project_managers.add(self.manager_emp)
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


from unittest.mock import patch, MagicMock
from apps.tenants.models import Tenant
from apps.notifications.models import WebPushSubscription
from apps.notifications.web_push import send_web_push, deliver_notification_web_push


class WebPushDeliveryTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name='Test Tenant', slug='delivery-tenant', status='active')
        self.user = User.objects.create_user(email='push_user@example.com', password='password123')
        self.sub = WebPushSubscription.objects.create(
            tenant=self.tenant,
            user=self.user,
            endpoint='https://fcm.googleapis.com/fcm/send/test-token-123',
            endpoint_hash='hash123',
            p256dh='BP8xNeM-52elJG2P7sevn68tJNzHbLrLh5YO-Aixae99zqCPpWgIB5q2OMB2hsFZSJHpfBH799hX4TPoFugiFi8',
            auth='SWtRNrFW2_SMqOlBpM7byw',
            is_active=True
        )

    @patch('apps.notifications.web_push.requests.post')
    def test_send_web_push_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_post.return_value = mock_resp

        result = send_web_push(self.sub, {'title': 'Hello', 'body': 'World'})
        self.assertTrue(result)
        self.sub.refresh_from_db()
        self.assertTrue(self.sub.is_active)
        self.assertIsNotNone(self.sub.last_seen_at)

    @patch('apps.notifications.web_push.requests.post')
    def test_send_web_push_expired_deactivates_subscription(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 410
        mock_post.return_value = mock_resp

        result = send_web_push(self.sub, {'title': 'Expired'})
        self.assertFalse(result)
        self.sub.refresh_from_db()
        self.assertFalse(self.sub.is_active)

    @patch('apps.notifications.web_push.send_web_push')
    def test_deliver_notification_web_push(self, mock_send):
        mock_send.return_value = True
        notif = Notification.objects.create(
            recipient=self.user,
            title='Schedule Alert',
            message='Shift starts soon',
            notif_type='schedule_event'
        )
        count = deliver_notification_web_push(notif)
        self.assertEqual(count, 1)
        mock_send.assert_called_once()
        args, kwargs = mock_send.call_args
        self.assertEqual(args[0], self.sub)
        self.assertEqual(args[1]['title'], 'Schedule Alert')
        self.assertEqual(args[1]['redirect_url'], reverse('schedule:month_view'))
