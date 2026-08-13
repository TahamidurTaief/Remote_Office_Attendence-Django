from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from datetime import date, time
from .models import ScheduleEvent
from apps.notifications.models import Notification

# Aggregation test imports
from apps.projects.models import Project, ProjectType, ProjectTask, DailyProgressLog
from apps.leave.models import LeaveRequest, LeaveType
from apps.branches.models import Branch
from apps.employees.models import EmployeeProfile

User = get_user_model()

class ScheduleCalendarTests(TestCase):
    def setUp(self):
        self.password = 'supersecure123'
        self.user = User.objects.create_user(
            email='manager@test.com',
            password=self.password,
            role='manager'
        )
        self.client.login(email='manager@test.com', password=self.password)

        # Setup base objects for aggregation tests
        self.branch = Branch.objects.create(
            name='Test Branch',
            latitude=23.8103,
            longitude=90.4125,
            radius_meters=100
        )
        self.employee_user = User.objects.create_user(
            email='employee@test.com',
            password=self.password,
            role='staff'
        )
        self.employee = EmployeeProfile.objects.create(
            user=self.employee_user,
            branch=self.branch,
            employee_id='EMP-2026-001',
            full_name='Test Employee',
            phone='+8801700000001',
            joined_date=date(2026, 1, 1),
            is_active=True
        )
        self.proj_type = ProjectType.objects.create(name='HVAC')
        self.project = Project.objects.create(
            name='Test Project',
            client_name='Test Client',
            location='Dhaka',
            project_type=self.proj_type,
            branch=self.branch,
            start_date=date(2026, 1, 1),
            progress_percent=0,
            status='In Progress'
        )
        self.leave_type = LeaveType.objects.create(
            name='Sick Leave',
            default_days_per_year=10,
            category='sick'
        )

    def test_calendar_month_grid_structure(self):
        url = reverse('schedule:month_view') + '?year=2026&month=7'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        weeks_data = response.context['weeks_data']
        self.assertTrue(len(weeks_data) >= 5)

        # Check first day of first week: June 28th, 2026 (Sunday)
        first_day = weeks_data[0][0]
        self.assertEqual(first_day['date'], date(2026, 6, 28))
        self.assertFalse(first_day['is_current_month'])
        # Verify date_str and date_formatted are present for Alpine
        self.assertEqual(first_day['date_str'], '2026-06-28')
        self.assertEqual(first_day['date_formatted'], 'June 28, 2026')

    def test_event_appears_on_correct_date_cell(self):
        event = ScheduleEvent.objects.create(
            title='Site Visit HVAC',
            date=date(2026, 7, 15),
            start_time=time(10, 0),
            event_type='Site Visit',
            created_by=self.user
        )

        url = reverse('schedule:month_view') + '?year=2026&month=7'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        weeks_data = response.context['weeks_data']
        target_day = weeks_data[2][3]
        self.assertEqual(target_day['date'], date(2026, 7, 15))
        self.assertEqual(len(target_day['all_events']), 1)
        
        event_data = target_day['all_events'][0]
        self.assertEqual(event_data['title'], 'Site Visit HVAC')
        self.assertEqual(event_data['source_type'], 'manual_event')
        self.assertEqual(event_data['edit_url'], reverse('schedule:edit', args=[event.pk]))

    def test_aggregation_from_all_sources(self):
        # 1. Create a Project Task planned on July 15th
        ProjectTask.objects.create(
            project=self.project,
            order=1,
            activity='Duct Installation',
            planned_start=date(2026, 7, 15),
            planned_finish=date(2026, 7, 15),
            status='In Progress'
        )

        # 2. Create an approved LeaveRequest overlapping July 15th
        LeaveRequest.objects.create(
            employee=self.employee,
            leave_type=self.leave_type,
            start_date=date(2026, 7, 14),
            end_date=date(2026, 7, 16),
            status='approved',
            reason='Flu'
        )

        # 3. Create a DailyProgressLog on July 15th
        DailyProgressLog.objects.create(
            project=self.project,
            date=date(2026, 7, 15),
            planned_work='Planned work',
            completed_work='Completed work',
            manpower_count=4,
            supervisor_name='John Supervisor',
            logged_by=self.user
        )

        # 4. Create a manual ScheduleEvent on July 15th
        ScheduleEvent.objects.create(
            title='Coordination Meeting',
            date=date(2026, 7, 15),
            event_type='Meeting',
            created_by=self.user
        )

        url = reverse('schedule:month_view') + '?year=2026&month=7'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        weeks_data = response.context['weeks_data']
        target_day = weeks_data[2][3]
        self.assertEqual(target_day['date'], date(2026, 7, 15))

        # Check all 4 items are in all_events
        all_events = target_day['all_events']
        self.assertEqual(len(all_events), 4)

        # Verify tagging and read-only edit_url presence
        for item in all_events:
            if item['source_type'] == 'manual_event':
                self.assertNotEqual(item['edit_url'], '')
                self.assertTrue(item['edit_url'].startswith('/schedule/event/'))
            else:
                self.assertEqual(item['edit_url'], '')

    def test_notification_sent_on_event_creation(self):
        # Clear existing notifications
        Notification.objects.all().delete()

        # Create a new event with employee assigned
        create_url = reverse('schedule:create')
        form_data = {
            'title': 'Safety Drill',
            'description': 'Mandatory safety drill.',
            'date': '2026-07-15',
            'event_type': 'Meeting',
            'assigned_to': [self.employee.pk],
        }
        
        response = self.client.post(create_url, data=form_data)
        # Check redirect on success
        self.assertEqual(response.status_code, 302)

        # Check database notification was created for assigned employee
        notifs = Notification.objects.filter(recipient=self.employee_user)
        self.assertEqual(notifs.count(), 1)
        self.assertEqual(notifs.first().title, 'New Event: Safety Drill')
        self.assertEqual(notifs.first().notif_type, 'field_visit')

    def test_staff_role_scoping(self):
        # Create an event assigned to the staff user
        event_assigned = ScheduleEvent.objects.create(
            title='Staff Duty Event',
            date=date(2026, 7, 15),
            event_type='Meeting',
            created_by=self.user
        )
        event_assigned.assigned_to.add(self.employee)

        # Create an event NOT assigned to the staff user
        event_unassigned = ScheduleEvent.objects.create(
            title='Secret Meeting',
            date=date(2026, 7, 15),
            event_type='Meeting',
            created_by=self.user
        )

        # Log in as the staff user
        self.client.login(email='employee@test.com', password=self.password)

        url = reverse('schedule:month_view') + '?year=2026&month=7'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        weeks_data = response.context['weeks_data']
        target_day = weeks_data[2][3] # July 15th
        all_events = target_day['all_events']

        # Staff user should only see the event assigned to them
        self.assertEqual(len(all_events), 1)
        self.assertEqual(all_events[0]['title'], 'Staff Duty Event')
        self.assertEqual(all_events[0]['id'], f"event_{event_assigned.pk}")


class ScheduleEventValidationAndConcurrencyTests(TestCase):
    def setUp(self):
        self.password = 'supersecure123'
        self.user = User.objects.create_user(
            email='manager_gantt@test.com',
            password=self.password,
            role='manager'
        )
        self.client.login(email='manager_gantt@test.com', password=self.password)

        self.branch = Branch.objects.create(
            name='Gantt Branch',
            latitude=23.8,
            longitude=90.4,
            radius_meters=100
        )
        self.employee_user = User.objects.create_user(
            email='emp_gantt@test.com',
            password=self.password,
            role='staff'
        )
        self.employee = EmployeeProfile.objects.create(
            user=self.employee_user,
            branch=self.branch,
            employee_id='EMP-GANTT-01',
            full_name='Gantt Emp',
            phone='+8801700000009',
            joined_date=date(2026, 1, 1),
            is_active=True
        )
        self.proj_type = ProjectType.objects.create(name='HVAC Design')
        self.project = Project.objects.create(
            name='Active Project',
            client_name='Test Client',
            location='Dhaka',
            project_type=self.proj_type,
            branch=self.branch,
            start_date=date(2026, 1, 1),
            progress_percent=0,
            status='In Progress'
        )
        self.completed_project = Project.objects.create(
            name='Completed Project',
            client_name='Test Client',
            location='Dhaka',
            project_type=self.proj_type,
            branch=self.branch,
            start_date=date(2026, 1, 1),
            progress_percent=100,
            status='Completed'
        )

    def test_overnight_event_property(self):
        event = ScheduleEvent(
            title='Night shift',
            date=date(2026, 7, 15),
            start_time=time(22, 0),
            end_time=time(2, 0),
            created_by=self.user
        )
        self.assertTrue(event.is_overnight)

    def test_completed_project_assignment_rejected(self):
        from django.core.exceptions import ValidationError
        event = ScheduleEvent(
            title='Site visit completed proj',
            date=date(2026, 7, 15),
            project=self.completed_project,
            created_by=self.user
        )
        with self.assertRaises(ValidationError):
            event.full_clean()

    def test_overlapping_schedule_raises_validation_error(self):
        from django.core.exceptions import ValidationError
        # Create first event
        e1 = ScheduleEvent.objects.create(
            title='First meeting',
            date=date(2026, 7, 15),
            start_time=time(10, 0),
            end_time=time(12, 0),
            created_by=self.user
        )
        e1.assigned_to.add(self.employee)

        # Build duplicate/overlapping event form data
        from apps.schedule.forms import ScheduleEventForm
        form_data = {
            'title': 'Overlapping meeting',
            'date': '2026-07-15',
            'start_time': '10:30',
            'end_time': '11:30',
            'event_type': 'Meeting',
            'assigned_to': [self.employee.pk],
        }
        form = ScheduleEventForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn("Scheduling conflict", form.errors.as_text())

    def test_optimistic_concurrency_collision_detection(self):
        event = ScheduleEvent.objects.create(
            title='Concurrent meeting',
            date=date(2026, 7, 15),
            created_by=self.user
        )
        # Attempt edit with mismatching version parameter
        edit_url = reverse('schedule:edit', args=[event.pk])
        form_data = {
            'title': 'Updated by User A',
            'date': '2026-07-15',
            'event_type': 'Meeting',
            'version': event.version - 1, # outdated version
        }
        response = self.client.post(edit_url, data=form_data)
        self.assertEqual(response.status_code, 200) # Form re-rendered due to error
        self.assertIn("The event was modified by another user concurrently", response.content.decode())

