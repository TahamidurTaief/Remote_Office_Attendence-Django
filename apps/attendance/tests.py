import datetime
import json
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

    def test_multi_session_total_hours(self):
        # Create first session (closed, 4 hours worked)
        att1 = Attendance.objects.create(
            employee=self.employee,
            date=datetime.date(2026, 7, 25),
            check_in_time=timezone.make_aware(datetime.datetime(2026, 7, 25, 9, 0)),
            check_out_time=timezone.make_aware(datetime.datetime(2026, 7, 25, 13, 0)),
            attendance_type='check_in',
            total_hours=4.00,
            status='on_time',
            type='office'
        )
        # Create second session (closed, 3.5 hours worked)
        att2 = Attendance.objects.create(
            employee=self.employee,
            date=datetime.date(2026, 7, 25),
            check_in_time=timezone.make_aware(datetime.datetime(2026, 7, 25, 14, 0)),
            check_out_time=timezone.make_aware(datetime.datetime(2026, 7, 25, 17, 30)),
            attendance_type='check_in',
            total_hours=3.50,
            status='on_time',
            type='office'
        )
        self.assertEqual(float(att1.total_daily_hours), 7.50)


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


class AttendancePhase2Tests(TestCase):
    def setUp(self):
        # Setup Branch
        self.branch = Branch.objects.create(
            name='HQ',
            address='Dhaka',
            latitude=23.7925,
            longitude=90.4078,
            radius_meters=100,
            wifi_ip='192.168.1.1',
            is_active=True
        )
        
        # Setup Manager
        self.manager_user = User.objects.create_user(
            phone='+8801700000001',
            password='password123',
            role='manager'
        )
        self.manager_profile = EmployeeProfile.objects.create(
            user=self.manager_user,
            employee_id='MGR-001',
            full_name='Manager One',
            phone='+8801700000001',
            joined_date=datetime.date(2026, 1, 1),
            is_active=True
        )
        from apps.employees.models import Employee
        self.manager_master = Employee.objects.create(
            employee_number='EMP-MGR-001',
            first_name='Manager',
            last_name='One',
            phone='+8801700000001',
            user=self.manager_user,
            joined_date=datetime.date(2026, 1, 1),
            status='active'
        )
        self.manager_profile.master_employee = self.manager_master
        self.manager_profile.save()

        # Setup Employee
        self.employee_user = User.objects.create_user(
            phone='+8801700000002',
            password='password123',
            role='staff'
        )
        self.employee = EmployeeProfile.objects.create(
            user=self.employee_user,
            employee_id='EMP-002',
            full_name='Staff Two',
            phone='+8801700000002',
            joined_date=datetime.date(2026, 1, 1),
            branch=self.branch,
            is_active=True
        )
        self.employee_master = Employee.objects.create(
            employee_number='EMP-STF-002',
            first_name='Staff',
            last_name='Two',
            phone='+8801700000002',
            user=self.employee_user,
            reporting_manager=self.manager_master,
            joined_date=datetime.date(2026, 1, 1),
            shift='Day Shift',
            weekly_holiday_policy='Friday, Saturday',
            status='active'
        )
        self.employee.master_employee = self.employee_master
        self.employee.save()

        # Setup HR/Admin
        self.hr_user = User.objects.create_user(
            phone='+8801700000003',
            password='password123',
            role='admin'
        )
        
        self.photo_file = SimpleUploadedFile("test_photo.jpg", b"file_content", content_type="image/jpeg")

    def test_photo_and_geofencing_policies(self):
        from apps.attendance.models import AttendancePolicy
        
        # Scenario A: Policy requires photo, but none sent. Check-in should fail.
        policy = AttendancePolicy.objects.create(branch=self.branch, photo_required=True, geofencing_policy='block')
        
        self.client.login(username='+8801700000002', password='password123')
        
        # Check-in without photo
        response = self.client.post(reverse('attendance:check_in'), data={
            'latitude': 23.7925,
            'longitude': 90.4078,
            'accuracy': 10
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn('Photo is required', response.json()['error'])
        
        # Scenario B: Policy photo_required=False. Check-in without photo should succeed.
        policy.photo_required = False
        policy.save()
        
        response = self.client.post(reverse('attendance:check_in'), data={
            'latitude': 23.7925,
            'longitude': 90.4078,
            'accuracy': 10
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['success'])
        
        # Check-out
        policy.photo_required = True
        policy.save()
        
        response = self.client.post(reverse('attendance:check_out'), data={
            'latitude': 23.7925,
            'longitude': 90.4078,
            'accuracy': 10
        })
        self.assertEqual(response.status_code, 400)
        
        # Scenario D: Geofencing block. Trying to check in 10km away should fail.
        policy.photo_required = False
        policy.geofencing_policy = 'block'
        policy.save()
        
        Attendance.objects.all().delete()
        
        response = self.client.post(reverse('attendance:check_in'), data={
            'latitude': 24.7925,
            'longitude': 91.4078,
            'accuracy': 10
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn('Geofence validation failed', response.json()['error'])
        
        # Scenario E: Geofencing warning. Should succeed, but append warning to note.
        policy.geofencing_policy = 'warning'
        policy.save()
        
        response = self.client.post(reverse('attendance:check_in'), data={
            'latitude': 24.7925,
            'longitude': 91.4078,
            'accuracy': 10,
            'note': 'Working remotely'
        })
        self.assertEqual(response.status_code, 200)
        attendance = Attendance.objects.latest('id')
        self.assertIn('GEOFENCE WARNING', attendance.note)

    def test_forgot_checkout_workflow(self):
        from apps.attendance.models import ForgotCheckoutRequest, AttendanceAuditLog
        
        # Create unclosed session
        att = Attendance.objects.create(
            employee=self.employee,
            date=timezone.localdate() - datetime.timedelta(days=1),
            check_in_time=timezone.now() - datetime.timedelta(hours=24),
            attendance_type='check_in',
            status='on_time'
        )
        
        self.client.login(username='+8801700000002', password='password123')
        
        # Submit forgot checkout request
        proposed_co = (timezone.now() - datetime.timedelta(hours=16)).isoformat()
        response = self.client.post(reverse('attendance:submit_forgot_checkout'), data={
            'attendance_id': att.id,
            'reason': 'Forgot to punch out yesterday',
            'check_out_time': proposed_co
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(ForgotCheckoutRequest.objects.filter(attendance=att).exists())
        
        req = ForgotCheckoutRequest.objects.get(attendance=att)
        self.assertEqual(req.status, 'pending_manager')
        
        # Manager approves
        self.client.login(username='+8801700000001', password='password123')
        response = self.client.post(reverse('attendance:process_forgot_checkout', args=[req.pk]), data={
            'action': 'approve'
        })
        self.assertEqual(response.status_code, 200)
        req.refresh_from_db()
        self.assertEqual(req.status, 'pending_hr')
        self.assertEqual(req.reviewed_by_manager, self.manager_user)
        
        # HR approves
        self.client.login(username='+8801700000003', password='password123')
        response = self.client.post(reverse('attendance:process_forgot_checkout', args=[req.pk]), data={
            'action': 'approve'
        })
        self.assertEqual(response.status_code, 200)
        req.refresh_from_db()
        self.assertEqual(req.status, 'approved')
        self.assertEqual(req.reviewed_by_hr, self.hr_user)
        
        att.refresh_from_db()
        self.assertIsNotNone(att.check_out_time)
        self.assertTrue(AttendanceAuditLog.objects.filter(attendance=att, action='forgot_checkout').exists())

    def test_attendance_correction_workflow(self):
        from apps.attendance.models import AttendanceCorrectionRequest, AttendanceAuditLog
        
        # Create completed session
        att = Attendance.objects.create(
            employee=self.employee,
            date=timezone.localdate(),
            check_in_time=timezone.now() - datetime.timedelta(hours=9),
            check_out_time=timezone.now() - datetime.timedelta(hours=1),
            total_hours=8.0,
            attendance_type='check_in',
            status='on_time'
        )
        
        self.client.login(username='+8801700000002', password='password123')
        
        # Submit correction request
        proposed_ci = (timezone.now() - datetime.timedelta(hours=8)).isoformat()
        proposed_co = (timezone.now() - datetime.timedelta(hours=1)).isoformat()
        response = self.client.post(reverse('attendance:submit_attendance_correction'), data={
            'attendance_id': att.id,
            'reason': 'Adjusted time',
            'check_in_time': proposed_ci,
            'check_out_time': proposed_co
        })
        self.assertEqual(response.status_code, 200)
        
        req = AttendanceCorrectionRequest.objects.get(attendance=att)
        self.assertEqual(req.status, 'pending')
        
        # Manager approves
        self.client.login(username='+8801700000001', password='password123')
        response = self.client.post(reverse('attendance:process_attendance_correction', args=[req.pk]), data={
            'action': 'approve'
        })
        self.assertEqual(response.status_code, 200)
        req.refresh_from_db()
        self.assertEqual(req.status, 'approved')
        self.assertTrue(AttendanceAuditLog.objects.filter(attendance=att, action='correction').exists())

    def test_overtime_approval_workflow(self):
        # Create completed session with pending overtime
        att = Attendance.objects.create(
            employee=self.employee,
            date=timezone.localdate(),
            check_in_time=timezone.now() - datetime.timedelta(hours=12),
            check_out_time=timezone.now(),
            total_hours=12.0,
            overtime_minutes=120,
            ot_status='pending',
            attendance_type='check_in',
            status='on_time'
        )
        
        # Manager rejects OT
        self.client.login(username='+8801700000001', password='password123')
        response = self.client.post(reverse('attendance:process_overtime', args=[att.pk]), data={
            'action': 'reject'
        })
        self.assertEqual(response.status_code, 200)
        att.refresh_from_db()
        self.assertEqual(att.ot_status, 'rejected')
        
        # Reset OT to pending for approve test
        att.ot_status = 'pending'
        att.save()
        
        # Manager approves OT
        response = self.client.post(reverse('attendance:process_overtime', args=[att.pk]), data={
            'action': 'approve'
        })
        self.assertEqual(response.status_code, 200)
        att.refresh_from_db()
        self.assertEqual(att.ot_status, 'approved')

    def test_bulk_sync_view(self):
        import uuid
        from apps.attendance.models import Attendance, AttendanceLocation, AttendanceActivityLog
        self.client.login(username='+8801700000002', password='password123')
        
        check_in_uuid = str(uuid.uuid4())
        check_out_uuid = str(uuid.uuid4())
        client_time = (timezone.now() - datetime.timedelta(minutes=30)).isoformat()
        
        sync_payload = {
            'actions': [
                {
                    'action': 'check_in',
                    'latitude': 23.7925,
                    'longitude': 90.4078,
                    'accuracy': 15.0,
                    'address': 'HQ Office Entrance',
                    'note': 'Arrived via bus',
                    'sync_uuid': check_in_uuid,
                    'client_event_time': client_time
                },
                {
                    'action': 'check_out',
                    'latitude': 23.7926,
                    'longitude': 90.4079,
                    'accuracy': 10.0,
                    'address': 'HQ Office Exit',
                    'sync_uuid': check_out_uuid,
                    'client_event_time': timezone.now().isoformat()
                }
            ]
        }
        
        response = self.client.post(
            reverse('attendance:bulk_sync'),
            data=json.dumps(sync_payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['synced'], 2)
        
        # Verify attendance record created
        att = Attendance.objects.get(sync_uuid=check_in_uuid)
        self.assertEqual(att.employee, self.employee)
        self.assertIsNotNone(att.check_out_time)
        
        # Verify activity logs
        self.assertTrue(AttendanceActivityLog.objects.filter(employee=self.employee, action='check_in').exists())
        self.assertTrue(AttendanceActivityLog.objects.filter(employee=self.employee, action='check_out').exists())

    def test_employee_timeline_view(self):
        from apps.attendance.models import AttendanceLocation
        # Create attendance and location
        att = Attendance.objects.create(
            employee=self.employee,
            date=timezone.localdate(),
            check_in_time=timezone.now(),
            attendance_type='check_in'
        )
        loc = AttendanceLocation.objects.create(
            attendance=att,
            event='check_in',
            latitude=23.7925,
            longitude=90.4078,
            address='HQ Test Office',
            accuracy=12.0,
            timestamp=timezone.now()
        )
        
        self.client.login(username='+8801700000002', password='password123')
        response = self.client.get(reverse('attendance:employee_timeline'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'HQ Test Office')


class AttendancePolicyTestCase(TestCase):
    def setUp(self):
        self.branch1 = Branch.objects.create(
            name='Uttara Branch',
            address='Sector 3, Uttara',
            latitude=23.8759,
            longitude=90.3795,
            radius_meters=100,
            wifi_ip='192.168.10.1',
            is_active=True
        )
        self.branch2 = Branch.objects.create(
            name='Dhanmondi Branch',
            address='Dhanmondi, Dhaka',
            latitude=23.7561,
            longitude=90.3729,
            radius_meters=100,
            wifi_ip='192.168.20.1',
            is_active=True
        )

    def test_policy_scoping_resolution(self):
        from apps.attendance.models import AttendancePolicy
        from apps.attendance.views import get_attendance_policy
        from apps.employees.models import EmployeeProfile
        from django.contrib.auth import get_user_model
        User = get_user_model()

        # Create global default policy
        global_policy = AttendancePolicy.objects.create(
            branch=None,
            photo_required=True,
            gps_required='required',
            max_gps_accuracy_meters=50,
            allow_holiday_attendance=True,
            allow_outside_geofence=True,
            late_grace_minutes=10
        )

        # Create branch 1 specific policy
        branch_policy = AttendancePolicy.objects.create(
            branch=self.branch1,
            photo_required=False,
            gps_required='warn_only',
            max_gps_accuracy_meters=200,
            allow_holiday_attendance=False,
            allow_outside_geofence=False,
            late_grace_minutes=5
        )

        user1 = User.objects.create_user(phone='+8801711119999', password='pass', role='staff')
        emp1 = EmployeeProfile.objects.create(
            user=user1,
            employee_id='EMP-P1',
            full_name='Emp Branch 1',
            phone='+8801711119999',
            joined_date=datetime.date(2026, 1, 1),
            branch=self.branch1,
            is_active=True
        )

        user2 = User.objects.create_user(phone='+8801711118888', password='pass', role='staff')
        emp2 = EmployeeProfile.objects.create(
            user=user2,
            employee_id='EMP-P2',
            full_name='Emp Branch 2',
            phone='+8801711118888',
            joined_date=datetime.date(2026, 1, 1),
            branch=self.branch2,
            is_active=True
        )

        # Resolve policy for employee in Branch 1 (should override global with branch 1 policy)
        p1 = get_attendance_policy(emp1)
        self.assertEqual(p1.photo_required, False)
        self.assertEqual(p1.gps_required, 'warn_only')
        self.assertEqual(p1.max_gps_accuracy_meters, 200)
        self.assertEqual(p1.late_grace_minutes, 5)

        # Resolve policy for employee in Branch 2 (no policy exists, should fall back to global default policy)
        p2 = get_attendance_policy(emp2)
        self.assertEqual(p2.photo_required, True)
        self.assertEqual(p2.gps_required, 'required')
        self.assertEqual(p2.max_gps_accuracy_meters, 50)
        self.assertEqual(p2.late_grace_minutes, 10)

    def test_dynamic_shift_grace_minutes(self):
        from apps.attendance.models import AttendancePolicy
        from apps.attendance.schedule_utils import get_branch_schedule, calculate_attendance_status
        from apps.branches.models import OfficeSchedule
        from apps.employees.models import EmployeeProfile
        from django.contrib.auth import get_user_model
        User = get_user_model()

        # Branch schedule setup (start time 09:00, default late after 15 mins)
        OfficeSchedule.objects.get_or_create(
            branch=self.branch1,
            defaults={
                'office_start_time': datetime.time(9, 0),
                'office_end_time': datetime.time(18, 0),
                'late_after_minutes': 15
            }
        )

        user = User.objects.create_user(phone='+8801711110000', password='pass', role='staff')
        emp = EmployeeProfile.objects.create(
            user=user,
            employee_id='EMP-DS1',
            full_name='Emp Shift',
            phone='+8801711110000',
            joined_date=datetime.date(2026, 1, 1),
            branch=self.branch1,
            is_active=True
        )

        # Policy sets grace minutes to 5 mins
        AttendancePolicy.objects.create(
            branch=self.branch1,
            late_grace_minutes=5
        )

        schedule = get_branch_schedule(emp)
        # Verify schedule.late_after_minutes matches policy (5) instead of branch schedule (15)
        self.assertEqual(schedule.late_after_minutes, 5)

        # Check-in at 09:04 -> should be On Time (grace is 5 mins, so late threshold is 09:05)
        t_on_time = timezone.make_aware(datetime.datetime(2026, 7, 25, 9, 4), timezone.get_current_timezone())
        self.assertEqual(calculate_attendance_status(t_on_time, schedule), 'on_time')

        # Check-in at 09:06 -> should be Late (grace is 5 mins, late threshold is 09:05)
        t_late = timezone.make_aware(datetime.datetime(2026, 7, 25, 9, 6), timezone.get_current_timezone())
        self.assertEqual(calculate_attendance_status(t_late, schedule), 'late')

    def test_dynamic_holiday_policy(self):
        from apps.branches.models import Holiday
        from apps.attendance.models import AttendancePolicy
        from apps.employees.models import EmployeeProfile
        from django.contrib.auth import get_user_model
        User = get_user_model()

        user = User.objects.create_user(phone='+8801711110001', password='pass', role='staff')
        emp = EmployeeProfile.objects.create(
            user=user,
            employee_id='EMP-H1',
            full_name='Holiday Tester',
            phone='+8801711110001',
            joined_date=datetime.date(2026, 1, 1),
            branch=self.branch1,
            is_active=True
        )

        # Create holiday for today
        today = timezone.localdate()
        Holiday.objects.create(name='Test Holiday', date=today, branch=self.branch1)

        # Policy: allow holiday attendance is True
        policy = AttendancePolicy.objects.create(
            branch=self.branch1,
            allow_holiday_attendance=True,
            photo_required=False,
            gps_required='optional'
        )

        self.client.login(username='+8801711110001', password='pass')
        response = self.client.post(
            reverse('attendance:check_in'),
            data={'latitude': 23.8759, 'longitude': 90.3795, 'accuracy': 10.0, 'type': 'office'}
        )
        self.assertEqual(response.status_code, 200)
        # Check status is holiday_attendance and not policy exception
        att = Attendance.objects.filter(employee=emp, date=today).first()
        self.assertEqual(att.status, 'holiday_attendance')
        self.assertFalse(att.is_policy_exception)

        # Policy: allow holiday attendance is False
        policy.allow_holiday_attendance = False
        policy.save()
        att.delete()

        response = self.client.post(
            reverse('attendance:check_in'),
            data={'latitude': 23.8759, 'longitude': 90.3795, 'accuracy': 10.0, 'type': 'office'}
        )
        self.assertEqual(response.status_code, 200)
        # Check status is holiday_attendance and is policy exception
        att2 = Attendance.objects.filter(employee=emp, date=today).first()
        self.assertEqual(att2.status, 'holiday_attendance')
        self.assertTrue(att2.is_policy_exception)






