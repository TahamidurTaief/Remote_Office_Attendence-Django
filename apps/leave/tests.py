import datetime
import json
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone
from apps.employees.models import EmployeeProfile
from apps.branches.models import Branch
from apps.leave.models import LeaveType, LeaveBalance, LeaveRequest
from apps.attendance.models import Attendance, AttendanceAbsentLog

User = get_user_model()

class LeaveAuditLogicTests(TestCase):
    def setUp(self):
        # Create Branch
        self.branch = Branch.objects.create(
            name='Test Branch',
            latitude=23.8103,
            longitude=90.4125,
            radius_meters=100
        )
        
        # Create Leave Types
        self.casual_leave = LeaveType.objects.create(
            name='Casual Leave',
            category='casual',
            default_days_per_year=10
        )
        self.sick_leave = LeaveType.objects.create(
            name='Sick Leave',
            category='sick',
            default_days_per_year=15
        )
        
        # Create Employees
        self.user = User.objects.create_user(
            phone='+8801700000001',
            password='testpassword123',
            role='staff'
        )
        self.employee = EmployeeProfile.objects.create(
            user=self.user,
            employee_id='EMP-2026-001',
            full_name='Test Employee',
            phone='+8801700000001',
            joined_date=datetime.date(2026, 1, 1),
            branch=self.branch,
            is_active=True
        )

    def test_pending_request_skipped_by_command_then_deducted_on_approval(self):
        # Target date is 2026-07-06 (Monday - working day)
        target_date = datetime.date(2026, 7, 6)
        
        # 1. Create a pending leave request covering target_date
        leave_request = LeaveRequest.objects.create(
            employee=self.employee,
            leave_type=self.sick_leave, # request for sick leave
            start_date=target_date,
            end_date=target_date,
            reason='Feeling unwell',
            status='pending'
        )
        
        # Verify status is pending
        self.assertEqual(leave_request.status, 'pending')
        
        # 2. Run mark_daily_absences command
        call_command('mark_daily_absences', date='2026-07-06')
        
        # Verify no AttendanceAbsentLog was created and no days were deducted
        self.assertFalse(AttendanceAbsentLog.objects.filter(employee=self.employee, date=target_date).exists())
        
        balance = LeaveBalance.objects.filter(employee=self.employee, leave_type=self.casual_leave, year=2026).first()
        self.assertTrue(balance is None or balance.used_days == 0)
        
        # 3. Now approve the leave request
        leave_request.status = 'approved'
        leave_request.save()
        
        # Verify sick leave balance got deducted by 1 day
        sick_balance = LeaveBalance.objects.get(employee=self.employee, leave_type=self.sick_leave, year=2026)
        self.assertEqual(sick_balance.used_days, 1)
        
        # Run command again and verify no extra logs or deductions occur (idempotency check)
        call_command('mark_daily_absences', date='2026-07-06')
        self.assertFalse(AttendanceAbsentLog.objects.filter(employee=self.employee, date=target_date).exists())
        self.assertEqual(LeaveBalance.objects.get(employee=self.employee, leave_type=self.sick_leave, year=2026).used_days, 1)

    def test_pending_request_skipped_by_command_then_deducted_retroactively_on_rejection(self):
        # Target date is 2026-07-06 (Monday - working day)
        target_date = datetime.date(2026, 7, 6)
        
        # 1. Create a pending leave request
        leave_request = LeaveRequest.objects.create(
            employee=self.employee,
            leave_type=self.sick_leave,
            start_date=target_date,
            end_date=target_date,
            reason='Feeling unwell',
            status='pending'
        )
        
        # Run command - should skip because request is pending
        call_command('mark_daily_absences', date='2026-07-06')
        self.assertFalse(AttendanceAbsentLog.objects.filter(employee=self.employee, date=target_date).exists())
        
        # 2. Reject the request
        leave_request.status = 'rejected'
        leave_request.save()
        
        # Verify that it got deducted retroactively using the default leave type (casual)
        self.assertTrue(AttendanceAbsentLog.objects.filter(employee=self.employee, date=target_date).exists())
        log = AttendanceAbsentLog.objects.get(employee=self.employee, date=target_date)
        self.assertEqual(log.leave_type_deducted, self.casual_leave)
        
        casual_balance = LeaveBalance.objects.get(employee=self.employee, leave_type=self.casual_leave, year=2026)
        self.assertEqual(casual_balance.used_days, 1)
        
        # 3. Verify idempotency of retroactive deduction (saving again as rejected doesn't deduct again)
        leave_request.save()
        self.assertEqual(LeaveBalance.objects.get(employee=self.employee, leave_type=self.casual_leave, year=2026).used_days, 1)


class LeaveOverlapLogCleanupTests(TestCase):
    def setUp(self):
        # Create Branch
        self.branch = Branch.objects.create(
            name='Test Branch 2',
            latitude=23.8103,
            longitude=90.4125,
            radius_meters=100
        )
        
        # Create Leave Types
        self.casual_leave = LeaveType.objects.create(
            name='Casual Leave 2',
            category='casual',
            default_days_per_year=10
        )
        self.sick_leave = LeaveType.objects.create(
            name='Sick Leave 2',
            category='sick',
            default_days_per_year=15
        )
        
        # Create Employees
        self.user = User.objects.create_user(
            phone='+8801700000002',
            password='testpassword123',
            role='staff'
        )
        self.employee = EmployeeProfile.objects.create(
            user=self.user,
            employee_id='EMP-2026-002',
            full_name='Test Employee 2',
            phone='+8801700000002',
            joined_date=datetime.date(2026, 1, 1),
            branch=self.branch,
            is_active=True
        )

        # Initialize balances
        self.casual_balance = LeaveBalance.objects.create(
            employee=self.employee,
            leave_type=self.casual_leave,
            year=2026,
            total_days=10,
            used_days=0
        )

    def test_overlapping_absent_log_deleted_on_leave_approval(self):
        target_date = datetime.date(2026, 7, 7)
        
        # 1. Create a system-logged absence for the employee on target_date
        absent_log = AttendanceAbsentLog.objects.create(
            employee=self.employee,
            date=target_date,
            leave_type_deducted=self.casual_leave
        )
        # Deduct 1 day from balance to simulate daily command execution behavior
        self.casual_balance.used_days = 1
        self.casual_balance.save()

        # 2. Approve a leave request for target_date
        leave_request = LeaveRequest.objects.create(
            employee=self.employee,
            leave_type=self.sick_leave,
            start_date=target_date,
            end_date=target_date,
            reason='Medical checkup',
            status='approved'
        )

        # 3. Verify overlapping AttendanceAbsentLog is deleted
        self.assertFalse(AttendanceAbsentLog.objects.filter(employee=self.employee, date=target_date).exists())

        # 4. Verify casual leave balance is restored (used_days should revert to 0)
        self.casual_balance.refresh_from_db()
        self.assertEqual(self.casual_balance.used_days, 0)

        # 5. Verify sick leave balance is deducted (used_days should be 1)
        sick_balance = LeaveBalance.objects.get(employee=self.employee, leave_type=self.sick_leave, year=2026)
        self.assertEqual(sick_balance.used_days, 1)


class EmployeeLeaveRuleTests(TestCase):
    def setUp(self):
        # Create Branch
        self.branch = Branch.objects.create(
            name='Test Branch 3',
            latitude=23.8103,
            longitude=90.4125,
            radius_meters=100
        )
        
        # Create Leave Types
        self.casual_leave = LeaveType.objects.create(
            name='Casual Leave 3',
            category='casual',
            default_days_per_year=10
        )
        
        # Create Employees
        self.user = User.objects.create_user(
            phone='+8801700000003',
            password='testpassword123',
            role='staff'
        )
        self.employee = EmployeeProfile.objects.create(
            user=self.user,
            employee_id='EMP-2026-003',
            full_name='Test Employee 3',
            phone='+8801700000003',
            joined_date=datetime.date(2026, 1, 1),
            branch=self.branch,
            is_active=True
        )

    def test_override_allowance_limit_respected(self):
        from apps.employees.models import EmployeeLeaveRule
        
        # 1. Initially without rule, remaining should be global default (10)
        self.assertEqual(self.employee.total_leave_left_by_year[2026], 10)

        # 2. Add override rule (e.g. 15 days)
        EmployeeLeaveRule.objects.create(
            employee=self.employee,
            leave_type=self.casual_leave,
            days_per_year=15
        )
        
        # 3. YearLeaveHelper should now yield 15
        self.assertEqual(self.employee.total_leave_left_by_year[2026], 15)

        # 4. Request approved leave - should instantiate LeaveBalance with 15 total days
        target_date = datetime.date(2026, 7, 10)
        leave_request = LeaveRequest.objects.create(
            employee=self.employee,
            leave_type=self.casual_leave,
            start_date=target_date,
            end_date=target_date,
            reason='Personal reasons',
            status='approved'
        )

        # Verify LeaveBalance created with 15 total_days
        balance = LeaveBalance.objects.get(employee=self.employee, leave_type=self.casual_leave, year=2026)
        self.assertEqual(balance.total_days, 15)
        self.assertEqual(balance.used_days, 1)
        self.assertEqual(balance.remaining_days, 14)

        # YearLeaveHelper should now yield 14 remaining
        self.assertEqual(self.employee.total_leave_left_by_year[2026], 14)


class DefaultLeaveTypeAndYearValidationTests(TestCase):
    def test_only_one_leave_type_can_be_default(self):
        # 1. Create a default leave type
        lt1 = LeaveType.objects.create(
            name='Default Leave 1',
            category='casual',
            default_days_per_year=10,
            is_default=True
        )
        self.assertTrue(lt1.is_default)

        # 2. Create another default leave type
        lt2 = LeaveType.objects.create(
            name='Default Leave 2',
            category='sick',
            default_days_per_year=15,
            is_default=True
        )
        self.assertTrue(lt2.is_default)

        # 3. Verify lt1.is_default was unset to False
        lt1.refresh_from_db()
        self.assertFalse(lt1.is_default)

    def test_get_default_deduction_leave_type(self):
        from apps.attendance.models import get_default_deduction_leave_type
        
        # Clear existing defaults if any
        LeaveType.objects.all().delete()

        # Create two types, one is default
        lt_casual = LeaveType.objects.create(name='Casual', category='casual', is_default=False)
        lt_sick = LeaveType.objects.create(name='Sick', category='sick', is_default=True)

        # get_default_deduction_leave_type should return sick because it is marked as default
        self.assertEqual(get_default_deduction_leave_type(), lt_sick)

    def test_leave_request_crosses_year_fails_validation(self):
        # Clear existing
        LeaveType.objects.all().delete()
        lt = LeaveType.objects.create(name='Casual', category='casual')
        
        branch = Branch.objects.create(name='Test Branch 4', latitude=23.8, longitude=90.4)
        user = User.objects.create_user(phone='+8801700000004', password='password123', role='staff')
        employee = EmployeeProfile.objects.create(
            user=user, employee_id='EMP-2026-004', full_name='Emp 4',
            phone='+8801700000004', joined_date=datetime.date(2026, 1, 1),
            branch=branch
        )

        from apps.leave.forms import LeaveRequestForm
        
        # Leave request within same year should succeed
        form_valid = LeaveRequestForm(
            data={
                'leave_type': lt.pk,
                'start_date': '2026-12-01',
                'end_date': '2026-12-05',
                'reason': 'Test'
            },
            employee=employee
        )
        self.assertTrue(form_valid.is_valid())

        # Leave request crossing year should fail validation
        form_invalid = LeaveRequestForm(
            data={
                'leave_type': lt.pk,
                'start_date': '2026-12-30',
                'end_date': '2027-01-02',
                'reason': 'Test'
            },
            employee=employee
        )
        self.assertFalse(form_invalid.is_valid())
        self.assertIn("Leave request cannot span across multiple calendar years", form_invalid.errors['__all__'][0])


class LeaveIdempotencyAndClientTimestampTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(
            name='HQ',
            address='Dhaka',
            latitude=23.7925,
            longitude=90.4078,
            radius_meters=150,
            wifi_ip='192.168.1.1',
            is_active=True
        )
        self.staff_user = User.objects.create_user(
            phone='+8801700000010',
            password='password123',
            role='staff'
        )
        self.employee = EmployeeProfile.objects.create(
            user=self.staff_user,
            employee_id='EMP-2026-555',
            full_name='Staff Ten',
            phone='+8801700000010',
            joined_date=datetime.date(2026, 1, 1),
            branch=self.branch,
            is_active=True
        )
        self.leave_type = LeaveType.objects.create(
            name='Casual Leave',
            category='casual',
            default_days_per_year=10
        )
        LeaveBalance.objects.create(
            employee=self.employee,
            leave_type=self.leave_type,
            year=2026,
            total_days=10,
            used_days=0
        )

    def test_leave_request_idempotency_ajax(self):
        self.client.login(username='+8801700000010', password='password123')
        sync_uuid_str = 'f8b8c8d8-e8f8-48a8-b8c8-d8e8f8a8b8c8'
        
        post_data = {
            'leave_type': self.leave_type.pk,
            'start_date': '2026-08-01',
            'end_date': '2026-08-03',
            'reason': 'Trip to Sylhet',
            'sync_uuid': sync_uuid_str
        }

        # First call (AJAX/JSON)
        response1 = self.client.post(
            reverse('leave:staff_request_create'),
            data=json.dumps(post_data),
            content_type='application/json',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(response1.status_code, 200)
        data1 = response1.json()
        self.assertTrue(data1['success'])
        req_id = data1['id']

        self.assertEqual(LeaveRequest.objects.filter(sync_uuid=sync_uuid_str).count(), 1)

        # Second call (AJAX/JSON) with same sync_uuid
        response2 = self.client.post(
            reverse('leave:staff_request_create'),
            data=json.dumps(post_data),
            content_type='application/json',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(response2.status_code, 200)
        data2 = response2.json()
        self.assertTrue(data2['success'])
        self.assertEqual(data2['id'], req_id)

        # Verify no duplicates
        self.assertEqual(LeaveRequest.objects.filter(sync_uuid=sync_uuid_str).count(), 1)

    def test_leave_request_client_timestamp_trust(self):
        self.client.login(username='+8801700000010', password='password123')
        sync_uuid_str = '08b8c8d8-e8f8-48a8-b8c8-d8e8f8a8b8c8'
        client_time = timezone.now() - datetime.timedelta(hours=3)

        post_data = {
            'leave_type': self.leave_type.pk,
            'start_date': '2026-08-05',
            'end_date': '2026-08-06',
            'reason': 'Personal work',
            'sync_uuid': sync_uuid_str,
            'client_event_time': client_time.isoformat()
        }

        response = self.client.post(
            reverse('leave:staff_request_create'),
            data=json.dumps(post_data),
            content_type='application/json',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(response.status_code, 200)
        
        req = LeaveRequest.objects.get(sync_uuid=sync_uuid_str)
        self.assertAlmostEqual(req.requested_at.timestamp(), client_time.timestamp(), delta=1)
        self.assertIsNotNone(req.client_event_time)
        self.assertIsNotNone(req.synced_at)


from apps.workflow.models import WorkflowDefinition, WorkflowInstance, WorkflowStep, WorkflowAction

class LeaveWorkflowIntegrationTests(TestCase):
    def setUp(self):
        # Ensure leave_approval definition is seeded
        from django.core.management import call_command
        call_command('seed_workflow_definitions')
        
        self.branch = Branch.objects.create(
            name='HQ Branch',
            latitude=23.8103,
            longitude=90.4125,
            radius_meters=100
        )
        self.staff_user = User.objects.create_user(
            phone='+8801700000021',
            password='password123',
            role='staff'
        )
        self.manager_user = User.objects.create_user(
            phone='+8801700000022',
            password='password123',
            role='manager'
        )
        self.hr_user = User.objects.create_user(
            phone='+8801700000023',
            password='password123',
            role='hr'
        )
        self.employee = EmployeeProfile.objects.create(
            user=self.staff_user,
            employee_id='EMP-2026-999',
            full_name='Staff Workflow',
            phone='+8801700000021',
            joined_date=datetime.date(2026, 1, 1),
            branch=self.branch,
            is_active=True
        )
        self.leave_type = LeaveType.objects.create(
            name='Casual Leave WF',
            category='casual',
            default_days_per_year=10
        )
        LeaveBalance.objects.create(
            employee=self.employee,
            leave_type=self.leave_type,
            year=2026,
            total_days=10,
            used_days=0
        )

    def test_leave_creation_creates_workflow_instance(self):
        # 1. Create a LeaveRequest
        leave_request = LeaveRequest.objects.create(
            employee=self.employee,
            leave_type=self.leave_type,
            start_date=datetime.date(2026, 7, 20),
            end_date=datetime.date(2026, 7, 21),
            reason='Family event',
            status='pending'
        )
        # Verify workflow instance is automatically created and is at step 1
        wf_instance = leave_request.workflow_instance
        self.assertIsNotNone(wf_instance)
        self.assertEqual(wf_instance.current_step, 1)
        self.assertEqual(wf_instance.current_status, 'pending')
        self.assertEqual(leave_request.status, 'pending')

    def test_workflow_full_approval_flow_and_balance_deduction(self):
        leave_request = LeaveRequest.objects.create(
            employee=self.employee,
            leave_type=self.leave_type,
            start_date=datetime.date(2026, 7, 20),
            end_date=datetime.date(2026, 7, 21), # 2 days
            reason='Family event',
            status='pending'
        )
        wf_instance = leave_request.workflow_instance
        self.assertIsNotNone(wf_instance)

        # 1. Manager approves (Step 1 -> Step 2)
        from apps.workflow.services import record_action
        record_action(wf_instance, self.manager_user, 'approve', 'Approved by Manager')
        
        # Verify leave_request status matches manager_approved
        leave_request.refresh_from_db()
        self.assertEqual(leave_request.status, 'manager_approved')
        self.assertEqual(wf_instance.current_status, 'manager_approved')
        self.assertEqual(wf_instance.current_step, 2)
        
        # Balance shouldn't be deducted yet since it's not fully approved
        balance = LeaveBalance.objects.get(employee=self.employee, leave_type=self.leave_type, year=2026)
        self.assertEqual(balance.used_days, 0)

        # 2. HR approves (Step 2 -> complete)
        record_action(wf_instance, self.hr_user, 'approve', 'Approved by HR')
        
        # Verify leave_request status matches approved
        leave_request.refresh_from_db()
        self.assertEqual(leave_request.status, 'approved')
        self.assertIsNotNone(wf_instance.completed_at)
        
        # Balance should be deducted by 2 days
        balance.refresh_from_db()
        self.assertEqual(balance.used_days, 2)

    def test_workflow_return_flow(self):
        leave_request = LeaveRequest.objects.create(
            employee=self.employee,
            leave_type=self.leave_type,
            start_date=datetime.date(2026, 7, 20),
            end_date=datetime.date(2026, 7, 21),
            reason='Family event',
            status='pending'
        )
        wf_instance = leave_request.workflow_instance
        self.assertIsNotNone(wf_instance)

        # 1. Manager returns request
        from apps.workflow.services import record_action
        record_action(wf_instance, self.manager_user, 'return', 'Needs more explanation')
        
        # Verify state is returned
        leave_request.refresh_from_db()
        self.assertEqual(leave_request.status, 'returned')
        self.assertEqual(wf_instance.current_status, 'returned')

    def test_cancel_pending_request_success(self):
        leave_request = LeaveRequest.objects.create(
            employee=self.employee,
            leave_type=self.leave_type,
            start_date=datetime.date(2026, 7, 20),
            end_date=datetime.date(2026, 7, 21),
            reason='Family event',
            status='pending'
        )
        wf_instance = leave_request.workflow_instance
        self.assertIsNotNone(wf_instance)

        self.client.force_login(self.staff_user)
        response = self.client.post(reverse('leave:cancel_request', kwargs={'pk': leave_request.pk}))
        self.assertEqual(response.status_code, 302)
        
        leave_request.refresh_from_db()
        self.assertEqual(leave_request.status, 'cancelled')
        wf_instance.refresh_from_db()
        self.assertEqual(wf_instance.current_status, 'cancelled')
        self.assertIsNotNone(wf_instance.completed_at)
        
        timeline = wf_instance.actions.all()
        self.assertTrue(any(a.action == 'cancel' for a in timeline))
        
        struct_timeline = leave_request.workflow_timeline
        step_1 = struct_timeline[0]
        self.assertEqual(step_1['status'], 'cancelled')
        cancel_actions = [a for a in step_1['actions'] if a['action_taken'] == 'cancel']
        self.assertEqual(len(cancel_actions), 1)

    def test_cancel_request_unauthorized(self):
        leave_request = LeaveRequest.objects.create(
            employee=self.employee,
            leave_type=self.leave_type,
            start_date=datetime.date(2026, 7, 20),
            end_date=datetime.date(2026, 7, 21),
            reason='Family event',
            status='pending'
        )
        
        self.client.force_login(self.manager_user)
        response = self.client.post(reverse('leave:cancel_request', kwargs={'pk': leave_request.pk}))
        self.assertEqual(response.status_code, 302)
        
        leave_request.refresh_from_db()
        self.assertEqual(leave_request.status, 'pending')

    def test_cancel_approved_request_blocked(self):
        leave_request = LeaveRequest.objects.create(
            employee=self.employee,
            leave_type=self.leave_type,
            start_date=datetime.date(2026, 7, 20),
            end_date=datetime.date(2026, 7, 21),
            reason='Family event',
            status='approved'
        )
        wf_instance = leave_request.workflow_instance
        self.assertIsNotNone(wf_instance)
        wf_instance.completed_at = timezone.now()
        wf_instance.current_status = 'approved'
        wf_instance.save()
        
        self.client.force_login(self.staff_user)
        response = self.client.post(reverse('leave:cancel_request', kwargs={'pk': leave_request.pk}))
        self.assertEqual(response.status_code, 302)
        
        leave_request.refresh_from_db()
        self.assertEqual(leave_request.status, 'approved')


class LeaveHardeningTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from apps.branches.models import Branch, Holiday
        from apps.employees.models import Employee, EmployeeProfile, Department, Designation
        from apps.leave.models import LeaveType, LeaveBalance
        User = get_user_model()
        
        self.branch = Branch.objects.create(name='Leave Branch', latitude=23.0, longitude=90.0)
        self.dept = Department.objects.create(name='IT', code='IT-L')
        self.desig = Designation.objects.create(name='Dev', code='DEV-L')
        
        self.user = User.objects.create_user(phone='+8801555555551', password='password123', role='staff')
        
        self.master = Employee.objects.create(
            employee_number='EMP-L-01',
            first_name='Leave',
            last_name='Tester',
            phone='+8801555555551',
            branch=self.branch,
            department=self.dept,
            designation=self.desig,
            weekly_holiday_policy='Saturday, Sunday',
            status='active',
            user=self.user
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id='EMP-L-01',
            full_name='Leave Tester',
            phone='+8801555555551',
            branch=self.branch,
            master_employee=self.master,
            joined_date='2026-01-01',
            is_active=True
        )
        
        self.leave_type = LeaveType.objects.create(
            name='Annual Hardening Leave',
            default_days_per_year=12,
            category='casual'
        )

    def test_overlap_validation_form(self):
        from apps.leave.models import LeaveRequest
        from apps.leave.forms import LeaveRequestForm
        
        # Create an existing approved leave request
        LeaveRequest.objects.create(
            employee=self.profile,
            leave_type=self.leave_type,
            start_date=datetime.date(2026, 8, 17),  # Monday
            end_date=datetime.date(2026, 8, 19),    # Wednesday
            reason='Trip',
            status='approved'
        )
        
        # Test overlapping request clean validation
        data = {
            'leave_type': self.leave_type.pk,
            'start_date': '2026-08-18',
            'end_date': '2026-08-20',
            'reason': 'Overlap'
        }
        form = LeaveRequestForm(data=data, employee=self.profile)
        self.assertFalse(form.is_valid())
        self.assertIn('overlaps with these dates', str(form.errors))

    def test_deductible_days_spanning_weekend_and_holiday(self):
        from apps.branches.models import Holiday
        from apps.leave.models import LeaveRequest, LeaveBalance
        
        # Monday (Aug 17) to Friday (Aug 21). Aug 22 (Saturday) & Aug 23 (Sunday) are weekends.
        # Create a holiday on Tuesday Aug 18
        Holiday.objects.create(name='National Day', date=datetime.date(2026, 8, 18), branch=self.branch)
        
        req = LeaveRequest.objects.create(
            employee=self.profile,
            leave_type=self.leave_type,
            start_date=datetime.date(2026, 8, 17), # Monday
            end_date=datetime.date(2026, 8, 23),   # Sunday (7 calendar days)
            reason='Test holidays & weekends',
            status='approved'
        )
        
        # Deductible days should be:
        # Aug 17 (Mon) - Work day (1)
        # Aug 18 (Tue) - Holiday (skip)
        # Aug 19 (Wed) - Work day (2)
        # Aug 20 (Thu) - Work day (3)
        # Aug 21 (Fri) - Work day (4)
        # Aug 22 (Sat) - Weekend (skip)
        # Aug 23 (Sun) - Weekend (skip)
        # Total deductible: 4 days!
        
        self.assertEqual(req.number_of_days, 4)
        balance = LeaveBalance.objects.get(employee=self.profile, leave_type=self.leave_type, year=2026)
        self.assertEqual(balance.used_days, 4)

    def test_attendance_interaction_skips_worked_days(self):
        from apps.attendance.models import Attendance
        from apps.leave.models import LeaveRequest
        
        # Create attendance for Aug 17 (Monday)
        Attendance.objects.create(
            employee=self.profile,
            date=datetime.date(2026, 8, 17),
            check_in_time=timezone.make_aware(datetime.datetime(2026, 8, 17, 9, 0))
        )
        
        req = LeaveRequest.objects.create(
            employee=self.profile,
            leave_type=self.leave_type,
            start_date=datetime.date(2026, 8, 17), # Monday (worked)
            end_date=datetime.date(2026, 8, 19),   # Wednesday (not worked)
            reason='Test attendance interaction',
            status='approved'
        )
        
        # Deductible days should be:
        # Aug 17 (Mon) - Has attendance (skip)
        # Aug 18 (Tue) - Work day (1)
        # Aug 19 (Wed) - Work day (2)
        # Total: 2 days!
        self.assertEqual(req.number_of_days, 2)


