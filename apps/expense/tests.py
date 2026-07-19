import datetime
import json
import uuid
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.branches.models import Branch
from apps.employees.models import EmployeeProfile
from apps.projects.models import Project, ProjectType
from .models import Expense

User = get_user_model()

class ExpenseTests(TestCase):
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
        # Setup ProjectType
        self.project_type = ProjectType.objects.create(name='HVAC')
        # Setup Project
        self.project = Project.objects.create(
            name='Test Project',
            branch=self.branch,
            project_type=self.project_type,
            start_date=datetime.date(2026, 1, 1)
        )
        # Create dummy file for attachment
        self.attachment = SimpleUploadedFile("receipt.pdf", b"pdf content", content_type="application/pdf")

    def test_expense_create_and_idempotency_ajax(self):
        self.client.login(username='+8801700000010', password='password123')
        sync_uuid_str = 'a8b8c8d8-e8f8-48a8-b8c8-d8e8f8a8b8c8'
        
        post_data = {
            'amount': 1500.00,
            'category': 'travel',
            'description': 'Taxi fare for client meeting',
            'project': self.project.pk,
            'sync_uuid': sync_uuid_str
        }

        # First call (AJAX/JSON)
        response1 = self.client.post(
            reverse('expense:staff_expense_create'),
            data=json.dumps(post_data),
            content_type='application/json',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(response1.status_code, 200)
        data1 = response1.json()
        self.assertTrue(data1['success'])
        expense_id = data1['id']

        self.assertEqual(Expense.objects.filter(sync_uuid=sync_uuid_str).count(), 1)

        # Second call with same sync_uuid (idempotency check)
        response2 = self.client.post(
            reverse('expense:staff_expense_create'),
            data=json.dumps(post_data),
            content_type='application/json',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(response2.status_code, 200)
        data2 = response2.json()
        self.assertTrue(data2['success'])
        self.assertEqual(data2['id'], expense_id)

        # Verify no duplicate was created
        self.assertEqual(Expense.objects.filter(sync_uuid=sync_uuid_str).count(), 1)

    def test_expense_client_timestamp_trust(self):
        self.client.login(username='+8801700000010', password='password123')
        sync_uuid_str = 'b8b8c8d8-e8f8-48a8-b8c8-d8e8f8a8b8c8'
        client_time = timezone.now() - datetime.timedelta(hours=4)

        post_data = {
            'amount': 500.00,
            'category': 'food',
            'description': 'Dinner with client',
            'project': self.project.pk,
            'sync_uuid': sync_uuid_str,
            'client_event_time': client_time.isoformat()
        }

        response = self.client.post(
            reverse('expense:staff_expense_create'),
            data=json.dumps(post_data),
            content_type='application/json',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(response.status_code, 200)
        
        expense = Expense.objects.get(sync_uuid=sync_uuid_str)
        self.assertAlmostEqual(expense.requested_at.timestamp(), client_time.timestamp(), delta=1)
        self.assertIsNotNone(expense.client_event_time)
        self.assertIsNotNone(expense.synced_at)

    def test_admin_approve_expense(self):
        # Create an expense
        expense = Expense.objects.create(
            employee=self.employee,
            amount=2000.00,
            category='accommodation',
            description='Hotel stay',
            status='pending'
        )

        # Setup Admin User
        admin_user = User.objects.create_user(
            phone='+8801700000020',
            password='password123',
            role='admin'
        )
        self.client.login(username='+8801700000020', password='password123')

        response = self.client.post(reverse('expense:approve_expense', kwargs={'pk': expense.pk}))
        self.assertEqual(response.status_code, 302) # Redirects back

        expense.refresh_from_db()
        self.assertEqual(expense.status, 'approved')
        self.assertEqual(expense.reviewed_by, admin_user)
        self.assertIsNotNone(expense.reviewed_at)

    def test_admin_reject_expense(self):
        # Create an expense
        expense = Expense.objects.create(
            employee=self.employee,
            amount=2000.00,
            category='accommodation',
            description='Hotel stay',
            status='pending'
        )

        # Setup Admin User
        admin_user = User.objects.create_user(
            phone='+8801700000020',
            password='password123',
            role='admin'
        )
        self.client.login(username='+8801700000020', password='password123')

        response = self.client.post(
            reverse('expense:reject_expense', kwargs={'pk': expense.pk}),
            data={'rejection_reason': 'Missing invoice'}
        )
        self.assertEqual(response.status_code, 302)

        expense.refresh_from_db()
        self.assertEqual(expense.status, 'rejected')
        self.assertEqual(expense.reviewed_by, admin_user)
        self.assertEqual(expense.rejection_reason, 'Missing invoice')
