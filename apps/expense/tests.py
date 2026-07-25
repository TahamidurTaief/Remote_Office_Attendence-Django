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

        # Setup Categories
        from .models import ExpenseCategory
        self.travel_category = ExpenseCategory.objects.create(
            name='Travel', code='travel', is_active=True
        )
        self.food_category = ExpenseCategory.objects.create(
            name='Food', code='food', is_active=True
        )
        self.accommodation_category = ExpenseCategory.objects.create(
            name='Accommodation', code='accommodation', is_active=True
        )

    def test_expense_create_and_idempotency_ajax(self):
        self.client.login(username='+8801700000010', password='password123')
        sync_uuid_str = 'a8b8c8d8-e8f8-48a8-b8c8-d8e8f8a8b8c8'
        
        post_data = {
            'amount': 1500.00,
            'category': self.travel_category.pk,
            'description': 'Taxi fare for client meeting',
            'project': self.project.pk,
            'sync_uuid': sync_uuid_str,
            'action': 'submit'
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
            'category': self.food_category.pk,
            'description': 'Dinner with client',
            'project': self.project.pk,
            'sync_uuid': sync_uuid_str,
            'client_event_time': client_time.isoformat(),
            'action': 'submit'
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

    def test_draft_workflow(self):
        self.client.login(username='+8801700000010', password='password123')
        sync_uuid_str = 'c8b8c8d8-e8f8-48a8-b8c8-d8e8f8a8b8c8'
        
        post_data = {
            'amount': 300.00,
            'category': self.food_category.pk,
            'description': 'Coffee with client',
            'project': self.project.pk,
            'sync_uuid': sync_uuid_str,
            'action': 'draft'
        }

        response = self.client.post(
            reverse('expense:staff_expense_create'),
            data=json.dumps(post_data),
            content_type='application/json',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(response.status_code, 200)
        
        expense = Expense.objects.get(sync_uuid=sync_uuid_str)
        self.assertEqual(expense.status, 'draft')

        # Now submit the draft
        response = self.client.post(reverse('expense:submit_draft', kwargs={'pk': expense.pk}))
        self.assertEqual(response.status_code, 302)
        
        expense.refresh_from_db()
        self.assertEqual(expense.status, 'pending_manager')

    def test_attachment_validation(self):
        from .forms import ExpenseForm
        # Invalid extension
        bad_file = SimpleUploadedFile("test.exe", b"binary content", content_type="application/octet-stream")
        form = ExpenseForm(data={
            'project': self.project.pk,
            'amount': 250.00,
            'category': self.travel_category.pk,
            'description': 'Test'
        }, files={'attachment': bad_file})
        self.assertFalse(form.is_valid())
        self.assertIn('attachment', form.errors)

        # Huge file (>5MB)
        huge_file = SimpleUploadedFile("test.pdf", b"a" * (6 * 1024 * 1024), content_type="application/pdf")
        form = ExpenseForm(data={
            'project': self.project.pk,
            'amount': 250.00,
            'category': self.travel_category.pk,
            'description': 'Test'
        }, files={'attachment': huge_file})
        self.assertFalse(form.is_valid())
        self.assertIn('attachment', form.errors)

    def test_multi_stage_approval_flow_and_permissions(self):
        # Setup Master Employees and Users
        from apps.employees.models import Employee
        
        manager_emp = Employee.objects.create(
            employee_number='EMP-MGR-001',
            first_name='Manager',
            last_name='One',
            phone='+8801700000030'
        )
        manager_user = User.objects.create_user(
            phone='+8801700000030',
            password='password123',
            role='manager'
        )
        manager_emp.user = manager_user
        manager_emp.save()

        emp_master = Employee.objects.create(
            employee_number='EMP-2026-555',
            first_name='Staff',
            last_name='Ten',
            phone='+8801700000010',
            reporting_manager=manager_emp
        )
        emp_master.user = self.staff_user
        emp_master.save()

        self.employee.master_employee = emp_master
        self.employee.save()

        finance_user = User.objects.create_user(
            phone='+8801700000040',
            password='password123',
            role='finance'
        )
        accounts_user = User.objects.create_user(
            phone='+8801700000050',
            password='password123',
            role='accounts'
        )
        unrelated_manager = User.objects.create_user(
            phone='+8801700000060',
            password='password123',
            role='manager'
        )

        # Create expense (starts at pending_manager)
        expense = Expense.objects.create(
            employee=self.employee,
            amount=2000.00,
            category=self.accommodation_category,
            description='Hotel stay',
            status='pending_manager'
        )

        # 1. Manager Stage checks
        # Unrelated manager tries to approve -> 403 Forbidden
        self.client.login(username='+8801700000060', password='password123')
        response = self.client.post(reverse('expense:approve_expense', kwargs={'pk': expense.pk}))
        self.assertEqual(response.status_code, 403)

        # Direct manager tries to approve -> 302 Success
        self.client.login(username='+8801700000030', password='password123')
        response = self.client.post(reverse('expense:approve_expense', kwargs={'pk': expense.pk}))
        self.assertEqual(response.status_code, 302)
        
        expense.refresh_from_db()
        self.assertEqual(expense.status, 'pending_finance')

        # 2. Finance Stage checks
        # Direct manager tries to approve finance stage -> 403 Forbidden
        response = self.client.post(reverse('expense:approve_expense', kwargs={'pk': expense.pk}))
        self.assertEqual(response.status_code, 403)

        # Finance user approves -> 302 Success
        self.client.login(username='+8801700000040', password='password123')
        response = self.client.post(reverse('expense:approve_expense', kwargs={'pk': expense.pk}))
        self.assertEqual(response.status_code, 302)

        expense.refresh_from_db()
        self.assertEqual(expense.status, 'pending_accounts')

        # 3. Accounts Stage checks
        # Finance user tries to approve accounts stage -> 403 Forbidden
        response = self.client.post(reverse('expense:approve_expense', kwargs={'pk': expense.pk}))
        self.assertEqual(response.status_code, 403)

        # Accounts user approves -> 302 Success
        self.client.login(username='+8801700000050', password='password123')
        response = self.client.post(reverse('expense:approve_expense', kwargs={'pk': expense.pk}))
        self.assertEqual(response.status_code, 302)

        expense.refresh_from_db()
        self.assertEqual(expense.status, 'approved')
        self.assertEqual(expense.reviewed_by, accounts_user)
        self.assertIsNotNone(expense.reviewed_at)

    def test_multi_stage_rejection(self):
        # Create expense
        expense = Expense.objects.create(
            employee=self.employee,
            amount=1500.00,
            category=self.food_category,
            description='Client lunch',
            status='pending_manager'
        )

        admin_user = User.objects.create_user(
            phone='+8801700000099',
            password='password123',
            role='admin'
        )
        self.client.login(username='+8801700000099', password='password123')

        # Admin rejects at Manager stage -> Rejected immediately
        response = self.client.post(
            reverse('expense:reject_expense', kwargs={'pk': expense.pk}),
            data={'rejection_reason': 'Invalid receipt'}
        )
        self.assertEqual(response.status_code, 302)
        
        expense.refresh_from_db()
        self.assertEqual(expense.status, 'rejected')
        self.assertEqual(expense.rejection_reason, 'Invalid receipt')
        self.assertEqual(expense.reviewed_by, admin_user)

    def test_manager_cannot_approve_own_expense(self):
        # Setup manager profile for direct employee
        from apps.employees.models import Employee
        manager_emp = Employee.objects.create(
            employee_number='EMP-2026-666',
            first_name='Manager',
            last_name='One',
            phone='+8801700000030'
        )
        manager_user = User.objects.create_user(
            phone='+8801700000030',
            password='password123',
            role='manager'
        )
        manager_emp.user = manager_user
        manager_emp.save()

        # Manager's own employee profile
        manager_profile = EmployeeProfile.objects.create(
            user=manager_user,
            employee_id='EMP-2026-666',
            full_name='Manager One',
            phone='+8801700000030',
            joined_date=datetime.date(2026, 1, 1),
            branch=self.branch,
            is_active=True
        )

        expense = Expense.objects.create(
            employee=manager_profile,
            amount=500.00,
            category=self.food_category,
            description='My own dinner',
            status='pending_manager'
        )

        # Login as manager and try to approve own expense
        self.client.login(username='+8801700000030', password='password123')
        response = self.client.post(reverse('expense:approve_expense', kwargs={'pk': expense.pk}))
        self.assertEqual(response.status_code, 403)

    def test_return_and_resubmit_flow(self):
        # Setup manager
        from apps.employees.models import Employee
        manager_emp = Employee.objects.create(
            employee_number='EMP-2026-666',
            first_name='Manager',
            last_name='One',
            phone='+8801700000030'
        )
        manager_user = User.objects.create_user(
            phone='+8801700000030',
            password='password123',
            role='manager'
        )
        manager_emp.user = manager_user
        manager_emp.save()

        emp_master = Employee.objects.create(
            employee_number='EMP-2026-555',
            first_name='Staff',
            last_name='Ten',
            phone='+8801700000010',
            reporting_manager=manager_emp
        )
        emp_master.user = self.staff_user
        emp_master.save()

        self.employee.master_employee = emp_master
        self.employee.save()

        expense = Expense.objects.create(
            employee=self.employee,
            amount=800.00,
            category=self.food_category,
            description='Client meal',
            status='pending_manager'
        )

        # Manager returns the expense
        self.client.login(username='+8801700000030', password='password123')
        response = self.client.post(
            reverse('expense:return_expense', kwargs={'pk': expense.pk}),
            data={'reason': 'Receipt is blurry', 'fields_to_correct': ['attachment']}
        )
        self.assertEqual(response.status_code, 302)
        expense.refresh_from_db()
        self.assertEqual(expense.status, 'returned_by_manager')
        self.assertEqual(expense.return_events.count(), 1)
        self.assertEqual(expense.return_events.first().reason, 'Receipt is blurry')

        # Employee edits and resubmits
        self.client.login(username='+8801700000010', password='password123')
        # Simulate update view submit action
        update_data = {
            'amount': 800.00,
            'category': self.food_category.pk,
            'description': 'Client meal - updated description',
            'action': 'submit'
        }
        response = self.client.post(
            reverse('expense:staff_expense_edit', kwargs={'pk': expense.pk}),
            data=update_data
        )
        self.assertEqual(response.status_code, 302)
        expense.refresh_from_db()
        self.assertEqual(expense.status, 'pending_manager')
        self.assertEqual(expense.history.count(), 1)
        self.assertEqual(expense.history.first().description, 'Client meal')

