from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from datetime import date, time
from .models import ScheduleEvent
from apps.notifications.models import Notification

# Aggregation test imports
from apps.projects.models import Project, ProjectType, ProjectTask, DailyProgressLog
from apps.leave.models import LeaveRequest, LeaveType
from apps.branches.models import Branch, Holiday
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


class CalendarHolidayAndPermissionScopingTests(TestCase):
    def setUp(self):
        from apps.branches.models import Holiday, Branch
        from apps.employees.models import EmployeeProfile, Employee, EmployeeStatus

        self.password = 'UserSecret123!'
        self.admin = User.objects.create_superuser(
            email='cal_admin@test.com',
            phone='+8801700000021',
            password=self.password,
            role='admin'
        )

        self.branch_dhaka = Branch.objects.create(name='Dhaka Branch', latitude=23.8, longitude=90.4, radius_meters=100)
        self.branch_ctg = Branch.objects.create(name='Chittagong Branch', latitude=22.3, longitude=91.8, radius_meters=100)

        # Employee 1 in Dhaka
        self.emp1_user = User.objects.create_user(email='emp_dhaka@test.com', phone='+8801700000022', password=self.password, role='employee')
        self.emp1_master = Employee.objects.create(
            user=self.emp1_user,
            first_name='Dhaka',
            last_name='Emp',
            employee_number='EMP-DHK-001',
            branch=self.branch_dhaka,
            status=EmployeeStatus.ACTIVE
        )
        self.emp1_profile = EmployeeProfile.objects.create(
            user=self.emp1_user,
            master_employee=self.emp1_master,
            branch=self.branch_dhaka,
            employee_id='EMP-DHK-001',
            full_name='Dhaka Emp',
            phone='+8801700000022',
            joined_date=date(2026, 1, 1),
            is_active=True
        )

        # Employee 2 in Chittagong
        self.emp2_user = User.objects.create_user(email='emp_ctg@test.com', phone='+8801700000023', password=self.password, role='employee')
        self.emp2_master = Employee.objects.create(
            user=self.emp2_user,
            first_name='Ctg',
            last_name='Emp',
            employee_number='EMP-CTG-001',
            branch=self.branch_ctg,
            status=EmployeeStatus.ACTIVE
        )
        self.emp2_profile = EmployeeProfile.objects.create(
            user=self.emp2_user,
            master_employee=self.emp2_master,
            branch=self.branch_ctg,
            employee_id='EMP-CTG-001',
            full_name='Ctg Emp',
            phone='+8801700000023',
            joined_date=date(2026, 1, 1),
            is_active=True
        )

        # Employee 3 with NO profile / no branch assigned
        self.emp_no_profile = User.objects.create_user(email='no_profile@test.com', phone='+8801700000024', password=self.password, role='employee')

        # Manager in Dhaka branch
        self.mgr_user = User.objects.create_user(email='mgr_dhaka@test.com', phone='+8801700000025', password=self.password, role='manager')
        self.mgr_master = Employee.objects.create(
            user=self.mgr_user,
            first_name='Dhaka',
            last_name='Manager',
            employee_number='EMP-MGR-001',
            branch=self.branch_dhaka,
            status=EmployeeStatus.ACTIVE
        )
        self.mgr_profile = EmployeeProfile.objects.create(
            user=self.mgr_user,
            master_employee=self.mgr_master,
            branch=self.branch_dhaka,
            employee_id='EMP-MGR-001',
            full_name='Dhaka Manager',
            phone='+8801700000025',
            joined_date=date(2026, 1, 1),
            is_active=True
        )

        # 1. Government Holiday: branch is None
        self.gov_holiday = Holiday.objects.create(name='Victory Day', date=date(2026, 12, 16), branch=None)

        # 2. Office Holiday: branch is Dhaka
        self.dhaka_holiday = Holiday.objects.create(name='Dhaka Office Anniversary', date=date(2026, 12, 16), branch=self.branch_dhaka)

        # 3. Office Holiday: branch is Chittagong
        self.ctg_holiday = Holiday.objects.create(name='Ctg Port Day', date=date(2026, 12, 16), branch=self.branch_ctg)

    def test_government_holiday_visible_to_all(self):
        url = reverse('schedule:month_view') + '?year=2026&month=12'

        # Admin sees govt holiday and both office holidays
        self.client.force_login(self.admin)
        res_admin = self.client.get(url)
        self.assertEqual(res_admin.status_code, 200)
        day_events = self._get_day_events(res_admin.context['weeks_data'], date(2026, 12, 16))
        titles = [e['raw_title'] for e in day_events]
        self.assertIn('Victory Day', titles)
        self.assertIn('Dhaka Office Anniversary', titles)
        self.assertIn('Ctg Port Day', titles)

        # Dhaka employee sees govt holiday + Dhaka office holiday, but NOT Chittagong office holiday
        self.client.force_login(self.emp1_user)
        res_dhaka = self.client.get(url)
        self.assertEqual(res_dhaka.status_code, 200)
        day_events_dhk = self._get_day_events(res_dhaka.context['weeks_data'], date(2026, 12, 16))
        titles_dhk = [e['raw_title'] for e in day_events_dhk]
        self.assertIn('Victory Day', titles_dhk)
        self.assertIn('Dhaka Office Anniversary', titles_dhk)
        self.assertNotIn('Ctg Port Day', titles_dhk)

        # Ctg employee sees govt holiday + Ctg office holiday, but NOT Dhaka office holiday
        self.client.force_login(self.emp2_user)
        res_ctg = self.client.get(url)
        self.assertEqual(res_ctg.status_code, 200)
        day_events_ctg = self._get_day_events(res_ctg.context['weeks_data'], date(2026, 12, 16))
        titles_ctg = [e['raw_title'] for e in day_events_ctg]
        self.assertIn('Victory Day', titles_ctg)
        self.assertIn('Ctg Port Day', titles_ctg)
        self.assertNotIn('Dhaka Office Anniversary', titles_ctg)

    def test_missing_profile_safe_fallback_no_data_leak(self):
        # User without profile should see ONLY government holiday, zero branch holidays
        self.client.force_login(self.emp_no_profile)
        url = reverse('schedule:month_view') + '?year=2026&month=12'
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)

        day_events = self._get_day_events(res.context['weeks_data'], date(2026, 12, 16))
        titles = [e['raw_title'] for e in day_events]
        self.assertIn('Victory Day', titles)
        self.assertNotIn('Dhaka Office Anniversary', titles)
        self.assertNotIn('Ctg Port Day', titles)

    def test_invalid_date_parameters_fall_back_gracefully(self):
        self.client.force_login(self.emp1_user)
        url = reverse('schedule:month_view') + '?year=notayear&month=99'
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertIn('weeks_data', res.context)

    def test_htmx_partial_request(self):
        self.client.force_login(self.emp1_user)
        url = reverse('schedule:month_view') + '?year=2026&month=12&partial=true'
        res = self.client.get(url, HTTP_HX_REQUEST='true')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Victory Day')

    def test_full_day_holiday_cell_styling_and_banner_prominence(self):
        url = reverse('schedule:month_view') + '?year=2026&month=12'
        self.client.force_login(self.admin)
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)

        # Look up Dec 16th week and day
        weeks_data = res.context['weeks_data']
        target_day = None
        for week in weeks_data:
            for day in week:
                if day['date'] == date(2026, 12, 16):
                    target_day = day
                    break

        self.assertIsNotNone(target_day)
        self.assertTrue(target_day['has_holiday'])
        self.assertTrue(target_day['has_gov_holiday'])
        self.assertIn('bg-rose-50', target_day['day_tint_class'])
        self.assertIn('bg-rose-600', target_day['day_badge_class'])
        self.assertContains(res, 'group/holiday')
        self.assertContains(res, 'Govt Holiday')

    def test_shift_schedule_route_cross_branch_isolation(self):
        from apps.branches.models import OfficeSchedule
        sched_dhaka, _ = OfficeSchedule.objects.get_or_create(
            branch=self.branch_dhaka,
            defaults={'office_start_time': '09:00', 'office_end_time': '17:00', 'working_days': ['sunday', 'monday']}
        )
        sched_ctg, _ = OfficeSchedule.objects.get_or_create(
            branch=self.branch_ctg,
            defaults={'office_start_time': '10:00', 'office_end_time': '18:00', 'working_days': ['saturday', 'sunday']}
        )

        shifts_url = reverse('schedule:shift_schedule')

        # 1. Dhaka Employee sees Dhaka schedule ONLY
        self.client.force_login(self.emp1_user)
        res_dhk = self.client.get(shifts_url)
        self.assertEqual(res_dhk.status_code, 200)
        self.assertEqual(res_dhk.context['selected_branch'], self.branch_dhaka)
        self.assertContains(res_dhk, 'Dhaka Branch')
        self.assertNotContains(res_dhk, 'Chittagong Branch')
        self.assertFalse(res_dhk.context['is_admin'])

        # 2. Ctg Employee sees Ctg schedule ONLY
        self.client.force_login(self.emp2_user)
        res_ctg = self.client.get(shifts_url)
        self.assertEqual(res_ctg.status_code, 200)
        self.assertEqual(res_ctg.context['selected_branch'], self.branch_ctg)
        self.assertContains(res_ctg, 'Chittagong Branch')
        self.assertNotContains(res_ctg, 'Dhaka Branch')

        # 3. Employee with NO profile gets safe empty state without crashing
        self.client.force_login(self.emp_no_profile)
        res_no_prof = self.client.get(shifts_url)
        self.assertEqual(res_no_prof.status_code, 200)
        self.assertIsNone(res_no_prof.context['selected_branch'])
        self.assertEqual(len(res_no_prof.context['schedules_list']), 0)
        self.assertContains(res_no_prof, 'No Shift Schedule Available')

        # 4. Admin sees branch selector and can inspect authorized branch schedules
        self.client.force_login(self.admin)
        res_admin = self.client.get(shifts_url + f'?branch_id={self.branch_ctg.id}')
        self.assertEqual(res_admin.status_code, 200)
        self.assertEqual(res_admin.context['selected_branch'], self.branch_ctg)
        self.assertTrue(res_admin.context['is_admin'])
        self.assertContains(res_admin, 'Manage Schedule')

    def test_manager_holiday_and_branch_isolation(self):
        """Manager must only see govt holidays and holidays of their own branch, never other branches."""
        url = reverse('schedule:month_view') + '?year=2026&month=12'
        self.client.force_login(self.mgr_user)
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)

        day_events = self._get_day_events(res.context['weeks_data'], date(2026, 12, 16))
        titles = [e['raw_title'] for e in day_events]
        self.assertIn('Victory Day', titles)
        self.assertIn('Dhaka Office Anniversary', titles)
        self.assertNotIn('Ctg Port Day', titles)

    def test_manager_shift_schedule_forbidden_from_browsing_other_branches(self):
        """Manager accessing shift schedule with another branch_id query param must remain strictly scoped to their own branch."""
        from apps.branches.models import OfficeSchedule
        sched_dhaka, _ = OfficeSchedule.objects.get_or_create(
            branch=self.branch_dhaka,
            defaults={'office_start_time': '09:00', 'office_end_time': '17:00', 'working_days': ['sunday', 'monday']}
        )
        sched_ctg, _ = OfficeSchedule.objects.get_or_create(
            branch=self.branch_ctg,
            defaults={'office_start_time': '10:00', 'office_end_time': '18:00', 'working_days': ['saturday', 'sunday']}
        )

        shifts_url = reverse('schedule:shift_schedule')
        self.client.force_login(self.mgr_user)

        # Manager requests other branch via query parameter ?branch_id=...
        res = self.client.get(shifts_url + f'?branch_id={self.branch_ctg.id}')
        self.assertEqual(res.status_code, 200)
        # Must be strictly isolated to Dhaka branch
        self.assertEqual(res.context['selected_branch'], self.branch_dhaka)
        self.assertContains(res, 'Dhaka Branch')
        self.assertNotContains(res, 'Chittagong Branch')
        # Manager selectable branches must only contain user's branch
        self.assertEqual(list(res.context['branches']), [self.branch_dhaka])

    def test_calendar_accessible_cotton_detail_modal(self):
        """Verify calendar content has accessible Cotton modal for day and event details."""
        url = reverse('schedule:month_view') + '?year=2026&month=12'
        self.client.force_login(self.mgr_user)
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)

        content = res.content.decode()
        self.assertIn('openDayList(dayData)', content)
        self.assertIn('openEventDetail(event)', content)
        self.assertIn('id="modal-calendar-detail-modal"', content)
        # Verify standard center accessible modal structure
        self.assertIn('ft-modal', content)

    def test_e2e_holiday_crud_calendar_rendering_and_htmx_flow(self):
        """End-to-end flow:
        - Admin creates global Government Holiday and branch Office Holiday via Holiday forms.
        - Calendar displays full-cell tint, bold day number, and full-width holiday banners.
        - Update operation reflects immediately in calendar.
        - Delete operation reflects immediately in calendar.
        - HTMX month navigation (prev, next, today) returns correct partial view and preserved state.
        - Calendar (/schedule/) and Shift Schedule (/schedule/shifts/) remain separate submenu routes.
        """
        # 1. Admin login
        self.client.force_login(self.admin)

        # 2. Admin creates global Government Holiday via holiday_add view
        create_url = reverse('branches:holiday_add')
        res_create_gov = self.client.post(create_url, {
            'name': 'International Mother Language Day',
            'date': '2026-02-21',
            'branch': ''  # Global / all branches
        })
        self.assertEqual(res_create_gov.status_code, 302)
        gov_hol = Holiday.objects.get(name='International Mother Language Day')
        self.assertIsNone(gov_hol.branch)

        # 3. Admin creates branch Office Holiday via holiday_add view
        res_create_off = self.client.post(create_url, {
            'name': 'Dhaka Founders Day',
            'date': '2026-02-21',
            'branch': str(self.branch_dhaka.id)
        })
        self.assertEqual(res_create_off.status_code, 302)
        off_hol = Holiday.objects.get(name='Dhaka Founders Day')
        self.assertEqual(off_hol.branch, self.branch_dhaka)

        # 4. Open /schedule/ for Feb 2026 and verify rendering
        cal_feb_url = reverse('schedule:month_view') + '?year=2026&month=2'
        res_cal = self.client.get(cal_feb_url)
        self.assertEqual(res_cal.status_code, 200)

        # Verify full-cell tint, bold day number, and banners in context
        weeks_feb = res_cal.context['weeks_data']
        feb_21_day = None
        for w in weeks_feb:
            for d in w:
                if d['date'] == date(2026, 2, 21):
                    feb_21_day = d
                    break
        self.assertIsNotNone(feb_21_day)
        self.assertTrue(feb_21_day['has_holiday'])
        self.assertTrue(feb_21_day['has_gov_holiday'])
        self.assertTrue(feb_21_day['has_office_holiday'])
        self.assertIn('bg-rose-50', feb_21_day['day_tint_class'])
        self.assertIn('bg-rose-600', feb_21_day['day_badge_class'])

        # Check content presence
        cal_html = res_cal.content.decode()
        self.assertIn('International Mother Language Day', cal_html)
        self.assertIn('Dhaka Founders Day', cal_html)
        self.assertIn('Govt Holiday', cal_html)

        # 5. Update operation: rename holiday
        edit_url = reverse('branches:holiday_edit', kwargs={'pk': off_hol.pk})
        res_edit = self.client.post(edit_url, {
            'name': 'Dhaka Innovation Day',
            'date': '2026-02-21',
            'branch': str(self.branch_dhaka.id)
        })
        self.assertEqual(res_edit.status_code, 302)

        res_cal_updated = self.client.get(cal_feb_url)
        self.assertContains(res_cal_updated, 'Dhaka Innovation Day')
        self.assertNotContains(res_cal_updated, 'Dhaka Founders Day')

        # 6. Delete operation: delete branch holiday
        del_url = reverse('branches:holiday_delete', kwargs={'pk': off_hol.pk})
        res_del = self.client.post(del_url, follow=True)
        self.assertEqual(res_del.status_code, 200)

        # Confirm holiday deleted from calendar grid
        res_cal_after_del = self.client.get(cal_feb_url)
        # Verify directly in weeks_data that the holiday no longer exists in Feb 21 events
        feb_events_after_del = self._get_day_events(res_cal_after_del.context['weeks_data'], date(2026, 2, 21))
        titles_after_del = [e['raw_title'] for e in feb_events_after_del]
        self.assertNotIn('Dhaka Innovation Day', titles_after_del)
        self.assertIn('International Mother Language Day', titles_after_del)

        # 7. HTMX navigation: Previous month (Jan 2026) and Next month (Mar 2026)
        cal_prev_url = reverse('schedule:month_view') + '?year=2026&month=1&partial=true'
        res_prev = self.client.get(cal_prev_url, HTTP_HX_REQUEST='true')
        self.assertEqual(res_prev.status_code, 200)
        self.assertEqual(res_prev.context['current_month'], 1)
        self.assertContains(res_prev, 'January 2026')

        cal_next_url = reverse('schedule:month_view') + '?year=2026&month=3&partial=true'
        res_next = self.client.get(cal_next_url, HTTP_HX_REQUEST='true')
        self.assertEqual(res_next.status_code, 200)
        self.assertEqual(res_next.context['current_month'], 3)
        self.assertContains(res_next, 'March 2026')

        # 8. Verify Shift Schedule route is distinct and does not share calendar content
        shifts_url = reverse('schedule:shift_schedule')
        res_shifts = self.client.get(shifts_url)
        self.assertEqual(res_shifts.status_code, 200)
        self.assertContains(res_shifts, 'Shift Schedule')
        self.assertContains(res_shifts, 'Work Hours')
        self.assertNotContains(res_shifts, 'International Mother Language Day')

    def test_role_matrix_calendar_and_shift_schedule_access(self):
        """Verify role-based branch isolation across Admin, Manager, Staff, and Employee."""
        # 1. Admin: global access across all branches
        self.client.force_login(self.admin)
        res_admin = self.client.get(reverse('schedule:shift_schedule') + f'?branch_id={self.branch_ctg.id}')
        self.assertEqual(res_admin.status_code, 200)
        self.assertEqual(res_admin.context['selected_branch'], self.branch_ctg)
        self.assertTrue(res_admin.context['is_admin'])

        # 2. Manager: isolated strictly to assigned branch (Dhaka)
        self.client.force_login(self.mgr_user)
        res_mgr = self.client.get(reverse('schedule:shift_schedule') + f'?branch_id={self.branch_ctg.id}')
        self.assertEqual(res_mgr.status_code, 200)
        self.assertEqual(res_mgr.context['selected_branch'], self.branch_dhaka)
        self.assertFalse(res_mgr.context['is_admin'])

        # 3. Staff: isolated strictly to assigned branch (Dhaka)
        self.client.force_login(self.emp1_user)
        res_staff = self.client.get(reverse('schedule:shift_schedule') + f'?branch_id={self.branch_ctg.id}')
        self.assertEqual(res_staff.status_code, 200)
        self.assertEqual(res_staff.context['selected_branch'], self.branch_dhaka)
        self.assertFalse(res_staff.context['is_admin'])

        # 4. Employee: isolated strictly to assigned branch (Ctg)
        self.client.force_login(self.emp2_user)
        res_emp = self.client.get(reverse('schedule:shift_schedule') + f'?branch_id={self.branch_dhaka.id}')
        self.assertEqual(res_emp.status_code, 200)
        self.assertEqual(res_emp.context['selected_branch'], self.branch_ctg)
        self.assertFalse(res_emp.context['is_admin'])

    def _get_day_events(self, weeks_data, target_date):
        for week in weeks_data:
            for day in week:
                if day['date'] == target_date:
                    return day['all_events']
        return []
