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


class IdempotencyAndClientTimestampTests(TestCase):
    def setUp(self):
        # Setup Branch
        self.branch = Branch.objects.create(
            name='HQ',
            address='Dhaka',
            latitude=23.7925,
            longitude=90.4078,
            radius_meters=150,
            wifi_ip='192.168.1.1',
            is_active=True
        )
        # Setup User & Employee
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

    def test_check_in_idempotency(self):
        self.client.login(username='+8801700000010', password='password123')
        sync_uuid_str = 'a8b8c8d8-e8f8-48a8-b8c8-d8e8f8a8b8c8'
        
        post_data = {
            'latitude': 23.7925,
            'longitude': 90.4078,
            'accuracy': 15.5,
            'note': 'Idempotent Checkin',
            'address': 'Gulshan-2, Dhaka',
            'type': 'office',
            'photo': self.dummy_photo,
            'sync_uuid': sync_uuid_str
        }

        # First call
        response1 = self.client.post(reverse('attendance:check_in'), data=post_data)
        self.assertEqual(response1.status_code, 200)
        data1 = response1.json()
        self.assertTrue(data1['success'])
        session_id = data1['session_id']

        # Verify record count
        self.assertEqual(Attendance.objects.filter(sync_uuid=sync_uuid_str).count(), 1)

        # Second call with same sync_uuid (must be idempotent)
        self.dummy_photo.seek(0)
        response2 = self.client.post(reverse('attendance:check_in'), data=post_data)
        self.assertEqual(response2.status_code, 200)
        data2 = response2.json()
        self.assertTrue(data2['success'])
        self.assertEqual(data2['session_id'], session_id)
        
        # Verify no duplicate record was created
        self.assertEqual(Attendance.objects.filter(sync_uuid=sync_uuid_str).count(), 1)

    def test_check_in_client_timestamp_trust(self):
        self.client.login(username='+8801700000010', password='password123')
        sync_uuid_str = 'b8b8c8d8-e8f8-48a8-b8c8-d8e8f8a8b8c8'
        
        # Valid client event time (e.g., 5 hours ago)
        client_time = timezone.now() - datetime.timedelta(hours=5)
        client_time_str = client_time.isoformat()

        post_data = {
            'latitude': 23.7925,
            'longitude': 90.4078,
            'accuracy': 15.5,
            'note': 'Trusted Time Checkin',
            'address': 'Gulshan-2, Dhaka',
            'type': 'office',
            'photo': self.dummy_photo,
            'sync_uuid': sync_uuid_str,
            'client_event_time': client_time_str
        }

        response = self.client.post(reverse('attendance:check_in'), data=post_data)
        self.assertEqual(response.status_code, 200)
        
        attendance = Attendance.objects.get(sync_uuid=sync_uuid_str)
        # Check that check_in_time is the client_event_time
        self.assertAlmostEqual(attendance.check_in_time.timestamp(), client_time.timestamp(), delta=1)
        self.assertIsNotNone(attendance.client_event_time)
        self.assertIsNotNone(attendance.synced_at)
        self.assertAlmostEqual(attendance.synced_at.timestamp(), timezone.now().timestamp(), delta=5)

    def test_check_in_invalid_client_timestamp_fallback(self):
        self.client.login(username='+8801700000010', password='password123')
        sync_uuid_str = 'c8b8c8d8-e8f8-48a8-b8c8-d8e8f8a8b8c8'
        
        # Invalid client event time (e.g., 30 hours ago, exceeding the 24h limit)
        client_time = timezone.now() - datetime.timedelta(hours=30)
        client_time_str = client_time.isoformat()

        post_data = {
            'latitude': 23.7925,
            'longitude': 90.4078,
            'accuracy': 15.5,
            'note': 'Invalid Time Fallback',
            'address': 'Gulshan-2, Dhaka',
            'type': 'office',
            'photo': self.dummy_photo,
            'sync_uuid': sync_uuid_str,
            'client_event_time': client_time_str
        }

        response = self.client.post(reverse('attendance:check_in'), data=post_data)
        self.assertEqual(response.status_code, 200)
        
        attendance = Attendance.objects.get(sync_uuid=sync_uuid_str)
        # Check that check_in_time falls back to server time (current time) and client_event_time is None
        self.assertAlmostEqual(attendance.check_in_time.timestamp(), timezone.now().timestamp(), delta=5)
        self.assertIsNone(attendance.client_event_time)
        self.assertIsNone(attendance.synced_at)

    def test_check_out_idempotency(self):
        self.client.login(username='+8801700000010', password='password123')
        
        # 1. First check-in
        checkin_uuid = 'd8b8c8d8-e8f8-48a8-b8c8-d8e8f8a8b8c8'
        post_data = {
            'latitude': 23.7925,
            'longitude': 90.4078,
            'accuracy': 15.5,
            'note': 'Checkin',
            'address': 'Gulshan-2, Dhaka',
            'type': 'office',
            'photo': self.dummy_photo,
            'sync_uuid': checkin_uuid
        }
        self.client.post(reverse('attendance:check_in'), data=post_data)
        
        # 2. Checkout
        checkout_uuid = 'e8b8c8d8-e8f8-48a8-b8c8-d8e8f8a8b8c8'
        self.dummy_photo.seek(0)
        checkout_data = {
            'latitude': 23.7925,
            'longitude': 90.4078,
            'accuracy': 10.0,
            'address': 'Gulshan-2, Dhaka',
            'photo': self.dummy_photo,
            'sync_uuid': checkout_uuid
        }
        
        response1 = self.client.post(reverse('attendance:check_out'), data=checkout_data)
        self.assertEqual(response1.status_code, 200)
        data1 = response1.json()
        self.assertTrue(data1['success'])
        
        self.assertEqual(AttendanceLocation.objects.filter(sync_uuid=checkout_uuid, event='check_out').count(), 1)
        
        # 3. Second checkout call with same sync_uuid (must be idempotent)
        self.dummy_photo.seek(0)
        response2 = self.client.post(reverse('attendance:check_out'), data=checkout_data)
        self.assertEqual(response2.status_code, 200)
        data2 = response2.json()
        self.assertTrue(data2['success'])
        self.assertEqual(data2['total_hours'], data1['total_hours'])
        
        # Verify no duplicate location record
        self.assertEqual(AttendanceLocation.objects.filter(sync_uuid=checkout_uuid, event='check_out').count(), 1)


from django.test import TransactionTestCase
import threading
import time

class SQLiteConcurrencyTests(TransactionTestCase):
    def test_concurrent_writes(self):
        from django.db import connection
        exceptions = []
        
        def perform_write(name_str, delay):
            try:
                connection.ensure_connection()
                from apps.branches.models import Branch
                time.sleep(delay)
                Branch.objects.create(
                    name=f"Branch {name_str}",
                    address="Dhaka",
                    latitude=23.7,
                    longitude=90.3,
                    radius_meters=100
                )
            except Exception as e:
                exceptions.append(e)

        t1 = threading.Thread(target=perform_write, args=("Thread A", 0.0))
        t2 = threading.Thread(target=perform_write, args=("Thread B", 0.02))
        
        t1.start()
        t2.start()
        
        t1.join()
        t2.join()

        # Assert no database is locked exceptions occurred
        for exc in exceptions:
            self.assertNotIn("database is locked", str(exc).lower())


class SQLiteSyncBoundariesAndAdditionalIdempotencyTests(TestCase):
    def setUp(self):
        # Setup Branch
        self.branch = Branch.objects.create(
            name='HQ',
            address='Dhaka',
            latitude=23.7925,
            longitude=90.4078,
            radius_meters=150,
            wifi_ip='192.168.1.1',
            is_active=True
        )
        # Setup User & Employee
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
        # Create dummy image
        from io import BytesIO
        from PIL import Image
        file_obj = BytesIO()
        image = Image.new("RGBA", size=(10, 10), color=(0, 0, 0, 0))
        image.save(file_obj, "png")
        file_obj.seek(0)
        self.dummy_photo = SimpleUploadedFile("selfie.png", file_obj.read(), content_type="image/png")

    def test_client_event_time_exact_24h_limit(self):
        self.client.login(username='+8801700000010', password='password123')
        sync_uuid_str = 'e8b8c8d8-e8f8-48a8-b8c8-d8e8f8a8b8cc'
        
        # 23 hours, 59 minutes, 50 seconds ago (safely within the 24-hour limit including HTTP processing latency)
        now = timezone.now()
        client_time = now - datetime.timedelta(hours=23, minutes=59, seconds=50)
        client_time_str = client_time.isoformat()

        post_data = {
            'latitude': 23.7925,
            'longitude': 90.4078,
            'accuracy': 15.5,
            'note': 'Boundary 24h Checkin',
            'address': 'Gulshan-2, Dhaka',
            'type': 'office',
            'photo': self.dummy_photo,
            'sync_uuid': sync_uuid_str,
            'client_event_time': client_time_str
        }

        response = self.client.post(reverse('attendance:check_in'), data=post_data)
        self.assertEqual(response.status_code, 200)
        
        attendance = Attendance.objects.get(sync_uuid=sync_uuid_str)
        self.assertAlmostEqual(attendance.check_in_time.timestamp(), client_time.timestamp(), delta=5)
        self.assertIsNotNone(attendance.client_event_time)

    def test_client_event_time_exceeds_24h_fallback(self):
        self.client.login(username='+8801700000010', password='password123')
        sync_uuid_str = 'e8b8c8d8-e8f8-48a8-b8c8-d8e8f8a8b8cd'
        
        # 24 hours and 5 minutes ago (exceeds the 24-hour limit)
        now = timezone.now()
        client_time = now - datetime.timedelta(hours=24, minutes=5)
        client_time_str = client_time.isoformat()

        post_data = {
            'latitude': 23.7925,
            'longitude': 90.4078,
            'accuracy': 15.5,
            'note': 'Boundary Exceeded 24h Checkin',
            'address': 'Gulshan-2, Dhaka',
            'type': 'office',
            'photo': self.dummy_photo,
            'sync_uuid': sync_uuid_str,
            'client_event_time': client_time_str
        }

        response = self.client.post(reverse('attendance:check_in'), data=post_data)
        self.assertEqual(response.status_code, 200)
        
        attendance = Attendance.objects.get(sync_uuid=sync_uuid_str)
        # Should fall back to server time
        self.assertAlmostEqual(attendance.check_in_time.timestamp(), timezone.now().timestamp(), delta=5)
        self.assertIsNone(attendance.client_event_time)

    def test_client_event_time_future_timestamp_fallback(self):
        self.client.login(username='+8801700000010', password='password123')
        sync_uuid_str = 'e8b8c8d8-e8f8-48a8-b8c8-d8e8f8a8b8cf'
        
        # 1 hour in the future
        client_time = timezone.now() + datetime.timedelta(hours=1)
        client_time_str = client_time.isoformat()

        post_data = {
            'latitude': 23.7925,
            'longitude': 90.4078,
            'accuracy': 15.5,
            'note': 'Future Time Checkin',
            'address': 'Gulshan-2, Dhaka',
            'type': 'office',
            'photo': self.dummy_photo,
            'sync_uuid': sync_uuid_str,
            'client_event_time': client_time_str
        }

        response = self.client.post(reverse('attendance:check_in'), data=post_data)
        self.assertEqual(response.status_code, 200)
        
        attendance = Attendance.objects.get(sync_uuid=sync_uuid_str)
        # Should fall back to server time
        self.assertAlmostEqual(attendance.check_in_time.timestamp(), timezone.now().timestamp(), delta=5)
        self.assertIsNone(attendance.client_event_time)

    def test_client_event_time_malformed_iso8601_fallback(self):
        self.client.login(username='+8801700000010', password='password123')
        sync_uuid_str = 'e8b8c8d8-e8f8-48a8-b8c8-d8e8f8a8b8ca'
        
        post_data = {
            'latitude': 23.7925,
            'longitude': 90.4078,
            'accuracy': 15.5,
            'note': 'Malformed ISO Checkin',
            'address': 'Gulshan-2, Dhaka',
            'type': 'office',
            'photo': self.dummy_photo,
            'sync_uuid': sync_uuid_str,
            'client_event_time': 'invalid-timestamp-format'
        }

        response = self.client.post(reverse('attendance:check_in'), data=post_data)
        self.assertEqual(response.status_code, 200)
        
        attendance = Attendance.objects.get(sync_uuid=sync_uuid_str)
        # Should fall back to server time and NOT cause 500 error
        self.assertAlmostEqual(attendance.check_in_time.timestamp(), timezone.now().timestamp(), delta=5)
        self.assertIsNone(attendance.client_event_time)

    def test_client_event_time_missing_fallback(self):
        self.client.login(username='+8801700000010', password='password123')
        sync_uuid_str = 'e8b8c8d8-e8f8-48a8-b8c8-d8e8f8a8b8cb'
        
        post_data = {
            'latitude': 23.7925,
            'longitude': 90.4078,
            'accuracy': 15.5,
            'note': 'Missing ISO Checkin',
            'address': 'Gulshan-2, Dhaka',
            'type': 'office',
            'photo': self.dummy_photo,
            'sync_uuid': sync_uuid_str
            # client_event_time missing
        }

        response = self.client.post(reverse('attendance:check_in'), data=post_data)
        self.assertEqual(response.status_code, 200)
        
        attendance = Attendance.objects.get(sync_uuid=sync_uuid_str)
        self.assertAlmostEqual(attendance.check_in_time.timestamp(), timezone.now().timestamp(), delta=5)
        self.assertIsNone(attendance.client_event_time)

    def test_field_visit_idempotency(self):
        self.client.login(username='+8801700000010', password='password123')
        sync_uuid_str = 'f8b8c8d8-e8f8-48a8-b8c8-d8e8f8a8b8cf'
        
        post_data = {
            'latitude': 23.7925,
            'longitude': 90.4078,
            'accuracy': 15.5,
            'note': 'Field Visit A',
            'address': 'Gulshan-2, Dhaka',
            'photo': self.dummy_photo,
            'sync_uuid': sync_uuid_str
        }

        # First call
        response1 = self.client.post(reverse('attendance:field_visit_submit'), data=post_data)
        self.assertEqual(response1.status_code, 200)
        data1 = response1.json()
        self.assertTrue(data1['success'])

        # Second call with same sync_uuid
        self.dummy_photo.seek(0)
        response2 = self.client.post(reverse('attendance:field_visit_submit'), data=post_data)
        self.assertEqual(response2.status_code, 200)
        data2 = response2.json()
        self.assertTrue(data2['success'])
        
        self.assertEqual(Attendance.objects.filter(sync_uuid=sync_uuid_str).count(), 1)

    def test_location_sync_idempotency(self):
        self.client.login(username='+8801700000010', password='password123')
        sync_uuid_str = '08b8c8d8-e8f8-48a8-b8c8-d8e8f8a8b8cf'
        
        # We need an active checkin shift for today
        Attendance.objects.create(
            employee=self.employee,
            date=timezone.localdate(),
            check_in_time=timezone.now(),
            attendance_type='check_in',
            status='on_time',
            type='office'
        )

        post_data = {
            'latitude': 23.7925,
            'longitude': 90.4078,
            'accuracy': 15.5,
            'sync_uuid': sync_uuid_str
        }

        # First call
        response1 = self.client.post(reverse('attendance:location_sync'), data=post_data)
        self.assertEqual(response1.status_code, 200)
        data1 = response1.json()
        self.assertTrue(data1['success'])

        # Second call with same sync_uuid
        response2 = self.client.post(reverse('attendance:location_sync'), data=post_data)
        self.assertEqual(response2.status_code, 200)
        data2 = response2.json()
        self.assertTrue(data2['success'])
        
        from apps.employees.models import EmployeeLocationSync
        self.assertEqual(EmployeeLocationSync.objects.filter(sync_uuid=sync_uuid_str).count(), 1)

    def test_save_location_idempotency(self):
        self.client.login(username='+8801700000010', password='password123')
        sync_uuid_str = '18b8c8d8-e8f8-48a8-b8c8-d8e8f8a8b8cf'
        
        # We need an active attendance session to save location
        Attendance.objects.create(
            employee=self.employee,
            date=timezone.localdate(),
            check_in_time=timezone.now(),
            attendance_type='check_in',
            status='on_time',
            type='office'
        )

        post_data = {
            'latitude': 23.7925,
            'longitude': 90.4078,
            'accuracy': 15.5,
            'address': 'Gulshan-2, Dhaka',
            'sync_uuid': sync_uuid_str
        }

        # First call
        response1 = self.client.post(reverse('attendance:save_location'), data=post_data)
        self.assertEqual(response1.status_code, 200)
        data1 = response1.json()
        self.assertTrue(data1['success'])

        # Second call with same sync_uuid
        response2 = self.client.post(reverse('attendance:save_location'), data=post_data)
        self.assertEqual(response2.status_code, 200)
        data2 = response2.json()
        self.assertTrue(data2['success'])
        
        self.assertEqual(AttendanceLocation.objects.filter(sync_uuid=sync_uuid_str, event='auto_track').count(), 1)



