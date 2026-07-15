import datetime
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile
from apps.branches.models import Branch
from apps.employees.models import EmployeeProfile
from apps.leave.models import LeaveType, LeaveBalance
from apps.attendance.models import Attendance, AttendanceLocation, AttendanceAbsentLog, get_default_deduction_leave_type

User = get_user_model()

class AttendanceModelAndHelperTests(TestCase):
    def setUp(self):
        # Create Branch
        self.branch = Branch.objects.create(
            name='Uttara Branch',
            address='Sector 3, Uttara',
            latitude=23.8759,
            longitude=90.3795,
            radius_meters=100,
            wifi_ip='192.168.10.1',
            is_active=True
        )
        
        # Create User & Employee
        self.user = User.objects.create_user(
            phone='+8801711112222',
            password='testpassword123',
            role='staff'
        )
        self.employee = EmployeeProfile.objects.create(
            user=self.user,
            employee_id='EMP-2026-101',
            full_name='Uttara Worker',
            phone='+8801711112222',
            joined_date=datetime.date(2026, 1, 1),
            branch=self.branch,
            is_active=True
        )

    def test_attendance_model_properties_and_string(self):
        # 1. Create a Check-in Session
        attendance = Attendance.objects.create(
            employee=self.employee,
            date=datetime.date(2026, 7, 16),
            check_in_time=timezone.now(),
            attendance_type='check_in',
            status='on_time',
            type='office'
        )

        self.assertEqual(str(attendance), 'Uttara Worker - 2026-07-16 (check_in)')
        self.assertTrue(attendance.is_active_session)

        # 2. Check out
        attendance.check_out_time = timezone.now()
        attendance.save()
        self.assertFalse(attendance.is_active_session)

    def test_get_default_deduction_leave_type_scenarios(self):
        # Scenario A: No LeaveTypes exist
        self.assertIsNone(get_default_deduction_leave_type())

        # Scenario B: Only a non-matching leave type exists
        other_leave = LeaveType.objects.create(name='Other Leave', category='other')
        self.assertEqual(get_default_deduction_leave_type(), other_leave)

        # Scenario C: A sick leave category exists
        sick_leave = LeaveType.objects.create(name='Sick Leave', category='sick')
        self.assertEqual(get_default_deduction_leave_type(), sick_leave)

        # Scenario D: A casual leave category exists
        casual_leave = LeaveType.objects.create(name='Casual Leave', category='casual')
        self.assertEqual(get_default_deduction_leave_type(), casual_leave)

        # Scenario E: A leave type is explicitly set as default
        other_leave.is_default = True
        other_leave.save()
        self.assertEqual(get_default_deduction_leave_type(), other_leave)


class AttendanceViewTests(TestCase):
    def setUp(self):
        # Setup Branch & Schedule
        self.branch = Branch.objects.create(
            name='HQ',
            address='Dhaka',
            latitude=23.7925,
            longitude=90.4078,
            radius_meters=150,
            wifi_ip='192.168.1.1',
            is_active=True
        )
        
        # Setup users
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

        # Create dummy image for uploads
        from io import BytesIO
        from PIL import Image
        file_obj = BytesIO()
        image = Image.new("RGBA", size=(10, 10), color=(0, 0, 0, 0))
        image.save(file_obj, "png")
        file_obj.seek(0)
        self.dummy_photo = SimpleUploadedFile("selfie.png", file_obj.read(), content_type="image/png")

    def test_check_in_unauthorized_role(self):
        # Admin is not allowed to check-in/out
        admin_user = User.objects.create_user(
            phone='+8801700000011',
            password='password123',
            role='admin'
        )
        logged_in = self.client.login(username='+8801700000011', password='password123')
        self.assertTrue(logged_in)
        
        response = self.client.post(reverse('attendance:check_in'))
        self.assertEqual(response.status_code, 403)

    def test_check_in_missing_location(self):
        logged_in = self.client.login(username='+8801700000010', password='password123')
        self.assertTrue(logged_in)
        
        # Missing coordinates
        post_data = {
            'accuracy': 10,
            'note': 'At branch lobby',
            'photo': self.dummy_photo
        }
        response = self.client.post(reverse('attendance:check_in'), data=post_data)
        self.assertEqual(response.status_code, 400)
        self.assertJSONEqual(response.content.decode(), {'success': False, 'error': 'Location is required for attendance.'})

    def test_successful_check_in_and_check_out_flow(self):
        logged_in = self.client.login(username='+8801700000010', password='password123')
        self.assertTrue(logged_in)
        
        # 1. Perform Check-in
        post_data = {
            'latitude': 23.7925,
            'longitude': 90.4078,
            'accuracy': 15.5,
            'note': 'Lobby',
            'address': 'Gulshan-2, Dhaka',
            'type': 'office',
            'photo': self.dummy_photo
        }
        
        response = self.client.post(reverse('attendance:check_in'), data=post_data)
        self.assertEqual(response.status_code, 200)
        
        data = response.json()
        self.assertTrue(data['success'])
        session_id = data['session_id']
        self.assertEqual(data['type'], 'office')

        # Verify Attendance is logged
        attendance = Attendance.objects.get(pk=session_id)
        self.assertEqual(attendance.employee, self.employee)
        self.assertTrue(attendance.is_active_session)

        # Verify Location is recorded
        self.assertTrue(AttendanceLocation.objects.filter(attendance=attendance, event='check_in').exists())

        # 2. Prevent checking-in twice simultaneously
        self.dummy_photo.seek(0)
        response_dup = self.client.post(reverse('attendance:check_in'), data=post_data)
        self.assertEqual(response_dup.status_code, 400)
        self.assertIn('already checked in', response_dup.json()['error'])

        # 3. Perform Check-out
        self.dummy_photo.seek(0)
        checkout_data = {
            'latitude': 23.7925,
            'longitude': 90.4078,
            'accuracy': 10.0,
            'address': 'Gulshan-2, Dhaka',
            'photo': self.dummy_photo
        }
        response_out = self.client.post(reverse('attendance:check_out'), data=checkout_data)
        self.assertEqual(response_out.status_code, 200)
        self.assertTrue(response_out.json()['success'])

        # Verify active session is closed
        attendance.refresh_from_db()
        self.assertFalse(attendance.is_active_session)
        self.assertIsNotNone(attendance.check_out_time)
