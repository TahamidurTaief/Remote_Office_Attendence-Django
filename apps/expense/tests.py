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


from apps.expense.models import ExpenseCategory
from apps.workflow.models import WorkflowDefinition, WorkflowInstance, WorkflowStep, WorkflowAction
from apps.admin_panel.dashboard_services import get_admin_dashboard_data

class ExpenseWorkflowIntegrationTests(TestCase):
    def setUp(self):
        from django.core.management import call_command
        call_command('seed_workflow_definitions')
        
        self.branch = Branch.objects.create(
            name='HQ', latitude=23.8, longitude=90.4
        )
        self.staff_user = User.objects.create_user(phone='+8801700000081', password='password123', role='staff')
        self.manager_user = User.objects.create_user(phone='+8801700000082', password='password123', role='manager')
        self.finance_user = User.objects.create_user(phone='+8801700000083', password='password123', role='finance')
        self.accounts_user = User.objects.create_user(phone='+8801700000084', password='password123', role='accounts')
        
        self.employee = EmployeeProfile.objects.create(
            user=self.staff_user,
            employee_id='EMP-EXP-888',
            full_name='Staff Expense',
            phone='+8801700000081',
            joined_date=datetime.date(2026, 1, 1),
            branch=self.branch,
            is_active=True
        )
        self.category = ExpenseCategory.objects.create(name='Travel', code='TRV')

    def test_expense_creation_creates_workflow_instance(self):
        expense = Expense.objects.create(
            employee=self.employee,
            amount=500.00,
            category=self.category,
            description='Client visit',
            status='pending_manager'
        )
        wf_instance = expense.workflow_instance
        self.assertIsNotNone(wf_instance)
        self.assertEqual(wf_instance.current_step, 1)
        self.assertEqual(wf_instance.current_status, 'pending_manager')

    def test_full_approval_flow_and_dashboard_counts(self):
        expense = Expense.objects.create(
            employee=self.employee,
            amount=500.00,
            category=self.category,
            description='Client visit',
            status='pending_manager'
        )
        wf_instance = expense.workflow_instance
        self.assertIsNotNone(wf_instance)

        # Dashboard pending check
        data = get_admin_dashboard_data(self.staff_user)
        self.assertEqual(data['pending_approvals_count'], 1)

        # 1. Manager approves (Step 1 -> Step 2)
        from apps.workflow.services import record_action
        record_action(wf_instance, self.manager_user, 'approve')
        expense.refresh_from_db()
        self.assertEqual(expense.status, 'pending_finance')
        self.assertEqual(wf_instance.current_step, 2)

        # 2. Finance approves (Step 2 -> Step 3)
        record_action(wf_instance, self.finance_user, 'approve')
        expense.refresh_from_db()
        self.assertEqual(expense.status, 'pending_accounts')
        self.assertEqual(wf_instance.current_step, 3)

        # 3. Accounts approves (Step 3 -> Approved)
        record_action(wf_instance, self.accounts_user, 'approve')
        expense.refresh_from_db()
        self.assertEqual(expense.status, 'approved')
        self.assertIsNotNone(wf_instance.completed_at)
        
        self.assertEqual(expense.reviewed_by, self.accounts_user)

        # Dashboard count should decrease
        data = get_admin_dashboard_data(self.staff_user)
        self.assertEqual(data['pending_approvals_count'], 0)

    def test_return_directly_to_employee(self):
        expense = Expense.objects.create(
            employee=self.employee,
            amount=500.00,
            category=self.category,
            description='Client visit',
            status='pending_manager'
        )
        wf_instance = expense.workflow_instance

        from apps.workflow.services import record_action
        record_action(wf_instance, self.manager_user, 'approve')
        expense.refresh_from_db()
        self.assertEqual(expense.status, 'pending_finance')

        record_action(wf_instance, self.finance_user, 'return', return_to_initiator=True)
        expense.refresh_from_db()
        self.assertEqual(expense.status, 'returned_by_finance')
        self.assertEqual(wf_instance.current_status, 'returned')


class ExpenseHardeningTests(TestCase):
    def setUp(self):
        from django.core.management import call_command
        call_command('seed_workflow_definitions')
        
        from django.contrib.auth import get_user_model
        from apps.branches.models import Branch
        from apps.employees.models import Employee, EmployeeProfile
        from apps.projects.models import Project, ProjectType
        from apps.expense.models import ExpenseCategory
        
        User = get_user_model()
        self.branch = Branch.objects.create(name='Test Branch', latitude=23.0, longitude=90.0)
        
        self.staff_user = User.objects.create_user(phone='+8801700000010', email='staff@example.com', password='password123', role='staff')
        self.manager_user = User.objects.create_user(phone='+8801700000011', email='manager@example.com', password='password123', role='manager')
        
        self.manager_master = Employee.objects.create(
            employee_number='MGR-E-01', first_name='Manager', last_name='One', branch=self.branch, status='active', user=self.manager_user
        )
        self.staff_master = Employee.objects.create(
            employee_number='EMP-E-01', first_name='Staff', last_name='One', branch=self.branch, status='active', reporting_manager=self.manager_master, user=self.staff_user
        )
        
        self.profile = EmployeeProfile.objects.create(
            user=self.staff_user, employee_id='EMP-E-01', full_name='Staff One', phone='+8801700000010', branch=self.branch, master_employee=self.staff_master, joined_date='2026-01-01', is_active=True
        )
        self.manager_profile = EmployeeProfile.objects.create(
            user=self.manager_user, employee_id='MGR-E-01', full_name='Manager One', phone='+8801700000011', branch=self.branch, master_employee=self.manager_master, joined_date='2026-01-01', is_active=True
        )
        
        self.category = ExpenseCategory.objects.create(name='Travel', code='TRV', is_active=True)
        self.proj_type = ProjectType.objects.create(name='HVAC Install')
        self.project = Project.objects.create(name='HVAC Project 1', client_name='Client A', location='Dhaka', project_type=self.proj_type, start_date=timezone.localdate(), status='In Progress')

    def test_completed_project_validation(self):
        from apps.expense.forms import ExpenseForm
        
        # Mark project as completed
        self.project.status = 'Completed'
        self.project.save()
        
        data = {
            'project': self.project.pk,
            'amount': '150.00',
            'category': self.category.pk,
            'description': 'Completed project expense test'
        }
        form = ExpenseForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertIn('Cannot link expenses to a completed project', str(form.errors))

    def test_idempotent_approval_with_locks(self):
        from django.urls import reverse
        from apps.expense.models import Expense
        from apps.workflow.services import record_action
        
        expense = Expense.objects.create(
            employee=self.profile,
            amount=200.00,
            category=self.category,
            description='Travel allowance',
            status='pending_manager',
            project=self.project
        )
        
        self.client.force_login(self.manager_user)
        approve_url = reverse('expense:approve_expense', kwargs={'pk': expense.pk})
        
        # First post - approves expense to next stage
        response1 = self.client.post(approve_url)
        self.assertEqual(response1.status_code, 302)
        expense.refresh_from_db()
        self.assertEqual(expense.status, 'pending_finance')
        
        # Second post - attempt to approve same stage again (should return forbidden status)
        response2 = self.client.post(approve_url)
        self.assertEqual(response2.status_code, 403)

    def test_edit_return_history_logging(self):
        from django.urls import reverse
        from apps.expense.models import Expense, ExpenseHistory
        
        from apps.workflow.services import record_action
        expense = Expense.objects.create(
            employee=self.profile,
            amount=300.00,
            category=self.category,
            description='Old description',
            status='pending_manager',
            project=self.project
        )
        wf_instance = expense.workflow_instance
        record_action(wf_instance, self.manager_user, 'return', return_to_initiator=True)
        expense.refresh_from_db()
        self.assertEqual(expense.status, 'returned_by_manager')
        
        self.client.force_login(self.staff_user)
        update_url = reverse('expense:staff_expense_edit', kwargs={'pk': expense.pk})
        
        # Perform form update POST
        data = {
            'project': self.project.pk,
            'amount': '350.00',
            'category': self.category.pk,
            'description': 'New description'
        }
        response = self.client.post(update_url, data=data)
        self.assertEqual(response.status_code, 302)
        
        # Verify old values saved in history
        history = ExpenseHistory.objects.filter(expense=expense).first()
        self.assertIsNotNone(history)
        self.assertEqual(history.amount, 300.00)
        self.assertEqual(history.description, 'Old description')
        
        # Verify current values updated
        expense.refresh_from_db()
        self.assertEqual(expense.amount, 350.00)
        self.assertEqual(expense.description, 'New description')
        self.assertEqual(expense.status, 'pending_manager')
        
        # Verify workflow reset completed_at to None
        self.assertIsNone(expense.workflow_instance.completed_at)


class AdminExpenseDetailAndEditTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(
            name='HQ Branch',
            address='Dhaka',
            latitude=23.7925,
            longitude=90.4078,
            radius_meters=150,
            wifi_ip='192.168.1.1',
            is_active=True
        )
        self.admin_user = User.objects.create_superuser(
            email='admin_test_exp@fieldtrack.com',
            phone='+8801700000099',
            password='adminpassword123',
            role='admin'
        )
        self.staff_user = User.objects.create_user(
            email='staff_test_exp@fieldtrack.com',
            phone='+8801700000098',
            password='staffpassword123',
            role='staff'
        )
        self.employee = EmployeeProfile.objects.create(
            user=self.staff_user,
            employee_id='EMP-ADM-TEST',
            full_name='Test Staff Member',
            phone='+8801700000098',
            joined_date=datetime.date(2026, 1, 1),
            branch=self.branch,
            is_active=True
        )
        from .models import ExpenseCategory
        self.category = ExpenseCategory.objects.create(
            name='Office Supplies',
            code='supplies',
            is_active=True
        )
        self.project_type = ProjectType.objects.create(name='Internal')
        self.project = Project.objects.create(
            name='Internal Renovation',
            branch=self.branch,
            project_type=self.project_type,
            start_date=datetime.date(2026, 1, 1)
        )
        self.expense = Expense.objects.create(
            employee=self.employee,
            amount=250.00,
            category=self.category,
            project=self.project,
            description='Original description of expense claim',
            status='pending_manager'
        )

    def test_expense_detail_api(self):
        self.client.force_login(self.admin_user)
        url = reverse('expense:admin_expense_detail_api', kwargs={'pk': self.expense.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['id'], self.expense.pk)
        self.assertEqual(data['employee']['name'], 'Test Staff Member')
        self.assertEqual(data['employee']['employee_id'], 'EMP-ADM-TEST')
        self.assertEqual(data['amount'], '250.00')
        self.assertEqual(data['category']['name'], 'Office Supplies')
        self.assertEqual(data['project']['name'], 'Internal Renovation')
        self.assertEqual(data['description'], 'Original description of expense claim')
        self.assertTrue(data['can_approve'])
        self.assertTrue(data['can_return'])
        self.assertTrue(data['can_reject'])

    def test_admin_expense_update_view(self):
        self.client.force_login(self.admin_user)
        url = reverse('expense:admin_expense_edit', kwargs={'pk': self.expense.pk})
        post_data = {
            'amount': '420.50',
            'category': self.category.pk,
            'project': self.project.pk,
            'description': 'Updated description by admin',
            'status': 'approved'
        }
        response = self.client.post(url, data=post_data)
        self.assertEqual(response.status_code, 302)

        self.expense.refresh_from_db()
        self.assertEqual(float(self.expense.amount), 420.50)
        self.assertEqual(self.expense.description, 'Updated description by admin')
        self.assertEqual(self.expense.status, 'approved')

        from .models import ExpenseHistory
        hist = ExpenseHistory.objects.filter(expense=self.expense).first()
        self.assertIsNotNone(hist)
        self.assertEqual(float(hist.amount), 250.00)
        self.assertEqual(hist.description, 'Original description of expense claim')



