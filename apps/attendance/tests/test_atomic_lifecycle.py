import uuid
from datetime import date, timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction

from apps.branches.models import Branch
from apps.employees.models import Department, Designation, EmployeeProfile, Employee
from apps.attendance.models import Attendance, AttendanceLocation, AttendancePolicy
from apps.attendance.transaction_service import AttendanceTransactionService, AttendanceTransactionError

User = get_user_model()


class AtomicAttendanceLifecycleTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.branch = Branch.objects.create(name="Gulshan Branch", latitude=23.79, longitude=90.41)
        self.department = Department.objects.create(name="Engineering", code="ENG")
        self.designation = Designation.objects.create(name="Software Engineer", code="SWE")

        self.user = User.objects.create_user(
            email="emp-lifecycle@example.com",
            password="Password123!",
            role="staff"
        )
        self.master = Employee.objects.create(
            employee_number="EMP-LIFECYCLE-001",
            first_name="Lifecycle",
            last_name="Tester",
            user=self.user,
            branch=self.branch,
            department=self.department,
            designation=self.designation,
        )
        self.employee = EmployeeProfile.objects.create(
            master_employee=self.master,
            user=self.user,
            employee_id="EMP-LIFECYCLE-001",
            full_name="Lifecycle Tester",
            branch=self.branch,
            department="Engineering",
            designation="Software Engineer",
            joined_date=date(2026, 1, 1),
            is_active=True,
        )
        self.policy, _ = AttendancePolicy.objects.get_or_create(
            branch=self.branch,
            defaults={
                'gps_required': 'optional',
                'photo_required': False,
                'allow_outside_geofence': True,
                'geofencing_policy': 'disabled',
            }
        )
        self.policy.gps_required = 'optional'
        self.policy.photo_required = False
        self.policy.allow_outside_geofence = True
        self.policy.geofencing_policy = 'disabled'
        self.policy.save()
        self.dummy_image = SimpleUploadedFile(
            name='test_photo.jpg',
            content=b'\xff\xd8\xff\xe0\x00\x10JFIF',
            content_type='image/jpeg'
        )

    def test_normal_check_in_and_check_out(self):
        # 1. Normal Check In
        data_in = {
            'latitude': 23.79,
            'longitude': 90.41,
            'accuracy': 10,
            'address': 'Gulshan 2, Dhaka'
        }
        res_in = AttendanceTransactionService.check_in(self.user, data_in)
        self.assertTrue(res_in['success'])
        session_id = res_in['session_id']

        attendance = Attendance.objects.get(pk=session_id)
        self.assertIsNone(attendance.check_out_time)

        # 2. Normal Check Out
        data_out = {
            'latitude': 23.79,
            'longitude': 90.41,
            'accuracy': 10,
            'address': 'Gulshan 2, Dhaka'
        }
        res_out = AttendanceTransactionService.check_out(self.user, data_out)
        self.assertTrue(res_out['success'])

        attendance.refresh_from_db()
        self.assertIsNotNone(attendance.check_out_time)
        self.assertGreaterEqual(attendance.total_hours, 0)

    def test_duplicate_online_check_in(self):
        data = {'latitude': 23.79, 'longitude': 90.41}
        AttendanceTransactionService.check_in(self.user, data)

        with self.assertRaises(AttendanceTransactionError) as cm:
            AttendanceTransactionService.check_in(self.user, data)
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("already checked in", str(cm.exception))

    def test_duplicate_online_check_out(self):
        data = {'latitude': 23.79, 'longitude': 90.41}
        AttendanceTransactionService.check_in(self.user, data)
        AttendanceTransactionService.check_out(self.user, data)

        with self.assertRaises(AttendanceTransactionError) as cm:
            AttendanceTransactionService.check_out(self.user, data)
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("No active check-in session found", str(cm.exception))

    def test_offline_duplicate_check_in_idempotency(self):
        sync_id = str(uuid.uuid4())
        data = {
            'latitude': 23.79,
            'longitude': 90.41,
            'sync_uuid': sync_id,
            'client_event_time': timezone.now().isoformat()
        }

        res1 = AttendanceTransactionService.check_in(self.user, data)
        self.assertTrue(res1['success'])

        res2 = AttendanceTransactionService.check_in(self.user, data)
        self.assertTrue(res2['success'])
        self.assertEqual(res1['session_id'], res2['session_id'])

    def test_offline_duplicate_check_out_idempotency(self):
        sync_in = str(uuid.uuid4())
        sync_out = str(uuid.uuid4())

        data_in = {'latitude': 23.79, 'longitude': 90.41, 'sync_uuid': sync_in}
        data_out = {'latitude': 23.79, 'longitude': 90.41, 'sync_uuid': sync_out}

        AttendanceTransactionService.check_in(self.user, data_in)
        res_out1 = AttendanceTransactionService.check_out(self.user, data_out)
        self.assertTrue(res_out1['success'])

        res_out2 = AttendanceTransactionService.check_out(self.user, data_out)
        self.assertTrue(res_out2['success'])
        self.assertEqual(res_out1['total_hours'], res_out2['total_hours'])

    def test_inactive_employee_blocked(self):
        self.employee.is_active = False
        self.employee.save()

        data = {'latitude': 23.79, 'longitude': 90.41}
        with self.assertRaises(AttendanceTransactionError) as cm:
            AttendanceTransactionService.check_in(self.user, data)
        self.assertEqual(cm.exception.status_code, 403)
        self.assertIn("inactive", str(cm.exception))

    def test_user_without_employee_profile(self):
        no_emp_user = User.objects.create_user(
            email="noemp@example.com",
            password="Password123!",
            role="staff"
        )
        data = {'latitude': 23.79, 'longitude': 90.41}
        with self.assertRaises(AttendanceTransactionError) as cm:
            AttendanceTransactionService.check_in(no_emp_user, data)
        self.assertEqual(cm.exception.status_code, 403)

    def test_multiple_completed_sessions_same_day(self):
        data = {'latitude': 23.79, 'longitude': 90.41}

        # Session 1
        res1 = AttendanceTransactionService.check_in(self.user, data)
        AttendanceTransactionService.check_out(self.user, data)

        # Session 2
        res2 = AttendanceTransactionService.check_in(self.user, data)
        AttendanceTransactionService.check_out(self.user, data)

        self.assertNotEqual(res1['session_id'], res2['session_id'])
        self.assertEqual(Attendance.objects.filter(employee=self.employee).count(), 2)

    def test_one_active_session_maximum_across_days(self):
        # Active session created yesterday
        yesterday = timezone.localtime() - timedelta(days=1)
        Attendance.objects.create(
            employee=self.employee,
            date=yesterday.date(),
            check_in_time=yesterday,
            type='office',
            attendance_type='check_in',
            status='on_time'
        )

        data = {'latitude': 23.79, 'longitude': 90.41}
        with self.assertRaises(AttendanceTransactionError) as cm:
            AttendanceTransactionService.check_in(self.user, data)
        self.assertEqual(cm.exception.status_code, 400)

    def test_rollback_on_failure(self):
        self.policy.photo_required = True
        self.policy.save()

        initial_count = Attendance.objects.count()
        data = {'latitude': 23.79, 'longitude': 90.41}

        # Missing required photo should fail and rollback transaction
        with self.assertRaises(AttendanceTransactionError):
            AttendanceTransactionService.check_in(self.user, data, photo=None)

        self.assertEqual(Attendance.objects.count(), initial_count)
