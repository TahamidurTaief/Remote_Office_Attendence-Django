from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from apps.leave.models import LeaveRequest, LeaveBalance, LeaveType
from apps.attendance.models import Attendance
from apps.employees.models import EmployeeProfile
from django.utils import timezone
import datetime

User = get_user_model()

class AdminLeaveAttendanceCRUDTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.password = 'Password123!'
        self.admin = User.objects.create_superuser(email='admin_crud@example.com', password=self.password, role='admin')
        self.client.login(email='admin_crud@example.com', password=self.password)
        
        from apps.employees.models import Employee, EmployeeProfile
        user = User.objects.create_user(email='emp_crud@example.com', password=self.password)
        emp_master = Employee.objects.create(
            user=user,
            employee_number='EMP-MASTER-CRUD-123',
            first_name='Crud',
            last_name='Employee'
        )
        self.employee = EmployeeProfile.objects.create(
            user=user,
            master_employee=emp_master,
            full_name='Crud Employee',
            joined_date=timezone.now().date(),
            employee_id='EMP-CRUD-123',
            phone='1234567890'
        )
        
        self.leave_type = LeaveType.objects.create(
            name='Casual Leave',
            category='casual',
            default_days_per_year=15,
            is_default=True
        )
        
        # Create a balance
        self.balance = LeaveBalance.objects.create(
            employee=self.employee,
            leave_type=self.leave_type,
            year=2026,
            total_days=15,
            used_days=0
        )
        
        self.leave_req = LeaveRequest.objects.create(
            employee=self.employee,
            leave_type=self.leave_type,
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + datetime.timedelta(days=2),
            reason='Test reason',
            status='pending'
        )

        self.attendance = Attendance.objects.create(
            employee=self.employee,
            date=timezone.now().date(),
            check_in_time=timezone.now(),
            type='office',
            status='on_time'
        )

    def test_leave_request_create_post(self):
        url = reverse('admin_panel:leave_request_create')
        data = {
            'employee': self.employee.id,
            'leave_type': self.leave_type.id,
            'start_date': (timezone.now().date() + datetime.timedelta(days=10)).isoformat(),
            'end_date': (timezone.now().date() + datetime.timedelta(days=12)).isoformat(),
            'reason': 'Vacation',
            'status': 'pending'
        }
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(LeaveRequest.objects.filter(reason='Vacation').count(), 1)

    def test_leave_request_edit_post(self):
        url = reverse('admin_panel:leave_request_edit', kwargs={'pk': self.leave_req.pk})
        data = {
            'employee': self.employee.id,
            'leave_type': self.leave_type.id,
            'start_date': self.leave_req.start_date.isoformat(),
            'end_date': self.leave_req.end_date.isoformat(),
            'reason': 'Updated Reason',
            'status': 'approved'
        }
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 302)
        self.leave_req.refresh_from_db()
        self.assertEqual(self.leave_req.reason, 'Updated Reason')
        self.assertEqual(self.leave_req.status, 'approved')

    def test_leave_request_delete_post(self):
        url = reverse('admin_panel:leave_request_delete', kwargs={'pk': self.leave_req.pk})
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(LeaveRequest.objects.filter(pk=self.leave_req.pk).exists())

    def test_leave_balance_edit_post(self):
        url = reverse('admin_panel:leave_balance_edit', kwargs={'pk': self.balance.pk})
        data = {
            'total_days': 20,
            'used_days': 2
        }
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 302)
        self.balance.refresh_from_db()
        self.assertEqual(self.balance.total_days, 20)
        self.assertEqual(self.balance.used_days, 2)

    def test_attendance_create_post(self):
        url = reverse('admin_panel:attendance_create')
        tomorrow = timezone.now() + datetime.timedelta(days=1)
        data = {
            'employee': self.employee.id,
            'date': tomorrow.date().isoformat(),
            'check_in_time': tomorrow.replace(hour=9, minute=0, second=0).strftime('%Y-%m-%dT%H:%M'),
            'check_out_time': tomorrow.replace(hour=17, minute=0, second=0).strftime('%Y-%m-%dT%H:%M'),
            'type': 'office',
            'status': 'on_time',
            'ot_status': 'none'
        }
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Attendance.objects.filter(employee=self.employee).count(), 2)

    def test_attendance_edit_post(self):
        url = reverse('admin_panel:attendance_edit', kwargs={'pk': self.attendance.pk})
        dt = timezone.now()
        data = {
            'employee': self.employee.id,
            'date': self.attendance.date.isoformat(),
            'check_in_time': dt.replace(hour=10, minute=0, second=0).strftime('%Y-%m-%dT%H:%M'),
            'check_out_time': dt.replace(hour=18, minute=0, second=0).strftime('%Y-%m-%dT%H:%M'),
            'type': 'field',
            'status': 'late',
            'ot_status': 'none'
        }
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 302)
        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.type, 'field')
        self.assertEqual(self.attendance.status, 'late')

    def test_attendance_delete_post(self):
        url = reverse('admin_panel:attendance_delete', kwargs={'pk': self.attendance.pk})
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Attendance.objects.filter(pk=self.attendance.pk).exists())
