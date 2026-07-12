import datetime
from django.test import TestCase
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
