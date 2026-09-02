import datetime
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.branches.models import Branch
from apps.employees.models import EmployeeProfile
from apps.attendance.models import Attendance

User = get_user_model()


class AttendanceUserScopingTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(
            name='Test Branch',
            address='Test Address',
            latitude=23.7925,
            longitude=90.4078,
            radius_meters=100,
            is_active=True
        )

        # User A & Employee A
        self.user_a = User.objects.create_user(
            phone='+8801700000001',
            password='password123',
            role='staff'
        )
        self.employee_a = EmployeeProfile.objects.create(
            user=self.user_a,
            employee_id='EMP-AAA-001',
            full_name='Employee A',
            phone='+8801700000001',
            joined_date=datetime.date(2026, 1, 1),
            branch=self.branch,
            is_active=True
        )

        # User B & Employee B
        self.user_b = User.objects.create_user(
            phone='+8801700000002',
            password='password123',
            role='staff'
        )
        self.employee_b = EmployeeProfile.objects.create(
            user=self.user_b,
            employee_id='EMP-BBB-002',
            full_name='Employee B',
            phone='+8801700000002',
            joined_date=datetime.date(2026, 1, 1),
            branch=self.branch,
            is_active=True
        )

        # User C (authenticated, NO Employee profile)
        self.user_c = User.objects.create_user(
            phone='+8801700000003',
            password='password123',
            role='staff'
        )

        # User D & Employee D (inactive)
        self.user_d = User.objects.create_user(
            phone='+8801700000004',
            password='password123',
            role='staff'
        )
        self.employee_d = EmployeeProfile.objects.create(
            user=self.user_d,
            employee_id='EMP-DDD-004',
            full_name='Employee D Inactive',
            phone='+8801700000004',
            joined_date=datetime.date(2026, 1, 1),
            branch=self.branch,
            is_active=False
        )

        today = timezone.localdate()
        # Session for Employee A
        self.session_a = Attendance.objects.create(
            employee=self.employee_a,
            date=today,
            check_in_time=timezone.now(),
            attendance_type='check_in',
            status='on_time',
            type='office'
        )

        # Session for Employee B
        self.session_b = Attendance.objects.create(
            employee=self.employee_b,
            date=today,
            check_in_time=timezone.now(),
            attendance_type='check_in',
            status='on_time',
            type='office'
        )

    def test_employee_a_cannot_receive_employee_b_attendance_data(self):
        self.client.login(username='+8801700000001', password='password123')
        response = self.client.get(reverse('attendance:status'), HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertTrue(data['has_active_session'])
        self.assertEqual(data['active_session_id'], self.session_a.id)
        self.assertNotEqual(data['active_session_id'], self.session_b.id)

    def test_user_without_employee_profile_receives_no_attendance_data(self):
        self.client.login(username='+8801700000003', password='password123')
        response = self.client.get(reverse('attendance:status'), HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 404)
        data = response.json()
        self.assertFalse(data['success'])
        self.assertEqual(data['error'], 'Employee profile not found.')

    def test_anonymous_access_denied(self):
        response = self.client.get(reverse('attendance:status'), HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 302)

    def test_inactive_employee_access_denied(self):
        self.client.login(username='+8801700000004', password='password123')
        response = self.client.get(reverse('attendance:status'), HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 403)
        data = response.json()
        self.assertFalse(data['success'])
        self.assertEqual(data['error'], 'Employee profile is inactive.')

    def test_valid_employee_status_still_works(self):
        self.client.login(username='+8801700000001', password='password123')
        response = self.client.get(reverse('attendance:status'), HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['active_session_id'], self.session_a.id)

    def test_no_fallback_employee_query_used(self):
        self.client.login(username='+8801700000003', password='password123')
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse('attendance:status'), HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 404)
        sql_statements = [q['sql'] for q in queries.captured_queries]
        for sql in sql_statements:
            self.assertNotIn('ORDER BY "employees_employeeprofile"."id" ASC LIMIT 1', sql)
            self.assertNotIn('ORDER BY "employees_employee"."id" ASC LIMIT 1', sql)

