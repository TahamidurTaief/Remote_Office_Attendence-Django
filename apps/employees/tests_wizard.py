import time
from unittest.mock import patch
from django.test import TestCase, Client
from django.urls import reverse
from django.core.exceptions import PermissionDenied
from django.contrib.auth import get_user_model
from apps.employees.models import Employee, EmployeeStatus, EmployeeProfile, Department, Designation
from apps.branches.models import Branch
from apps.accounts.rbac_models import Role, UserRoleAssignment
from apps.employees.wizard_service import WizardDraftManager, DRAFT_SESSION_KEY

User = get_user_model()


class EmployeeWizardFullTestSuite(TestCase):
    """
    Rigorously tests the employee create/edit wizard:
    - Direct step clicks
    - No-reload HTMX navigation
    - Current-step saving
    - Data restoration
    - Partial-step navigation
    - Final full validation
    - Create and edit workflows
    - Back/forward/refresh
    - Duplicate submission
    - Expired draft
    - Unauthorized draft access
    - Atomic rollback
    """

    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            email='admin_wiz@example.com',
            password='Password123!'
        )
        self.client.force_login(self.admin_user)

        self.branch = Branch.objects.create(name='Main Branch', address='123 Test St', latitude=23.7, longitude=90.3)
        self.dept = Department.objects.create(name='Engineering', code='ENG')
        self.desig = Designation.objects.create(name='Software Engineer', department=self.dept)
        self.role = Role.objects.create(name='Staff Role', code='staff', is_active=True)

    def test_direct_step_clicks_htmx_no_reload(self):
        """Clicking any step counter directly performs HTMX partial swap without reload."""
        url = reverse('employees:employee_wizard')
        data = {
            'employee_number': 'EMP-HTMX-001',
            'first_name': 'Htmx',
            'last_name': 'Navigator',
            'personal_email': 'htmx@example.com',
            'phone': '+8801711000001',
            'target_step': '3'  # User clicked Step 3 directly
        }
        response = self.client.post(url, data, HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'employees/wizard/wizard_content.html')
        self.assertIn('HX-Push-Url', response.headers)
        self.assertIn('/step/3/', response.headers['HX-Push-Url'])

        emp = Employee.objects.get(employee_number='EMP-HTMX-001')
        self.assertEqual(emp.first_name, 'Htmx')

    def test_current_step_saving_and_data_restoration(self):
        """Current step's submitted data is saved and restored when navigating back."""
        # 1. Start wizard, fill Step 1, navigate to Step 2
        url_s1 = reverse('employees:employee_wizard')
        self.client.post(url_s1, {
            'employee_number': 'EMP-RESTORE-001',
            'first_name': 'Restore',
            'last_name': 'User',
            'personal_email': 'restore@example.com',
            'phone': '+8801711000002',
            'target_step': '2'
        }, HTTP_HX_REQUEST='true')

        emp = Employee.objects.get(employee_number='EMP-RESTORE-001')

        # 2. In Step 2, enter shift info and navigate to Step 3
        url_s2 = reverse('employees:employee_wizard_step', kwargs={'uuid': emp.uuid, 'step': 2})
        self.client.post(url_s2, {
            'branch': self.branch.pk,
            'department': self.dept.pk,
            'designation': self.desig.pk,
            'employment_type': 'full_time',
            'joined_date': '2026-01-15',
            'shift': 'Morning Shift (8AM - 4PM)',
            'weekly_holiday_policy': 'Friday, Saturday',
            'target_step': '3'
        }, HTTP_HX_REQUEST='true')

        # 3. Now navigate back to Step 2 via GET
        response = self.client.get(url_s2, HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertEqual(str(form.initial.get('shift')), 'Morning Shift (8AM - 4PM)')
        self.assertEqual(str(form.initial.get('weekly_holiday_policy')), 'Friday, Saturday')

    def test_partial_step_navigation_retains_incomplete_state(self):
        """Allows navigation when step is partially completed, retaining incomplete state."""
        url = reverse('employees:employee_wizard')
        # Enter only first_name (missing required last_name and employee_number)
        response = self.client.post(url, {
            'first_name': 'PartialOnly',
            'target_step': '2'
        }, HTTP_HX_REQUEST='true')

        self.assertEqual(response.status_code, 200)
        # Verify step 1 has incomplete or error status
        statuses = response.context['step_statuses']
        self.assertIn(statuses.get(1), ('incomplete', 'error'))

        # Check that the draft safely preserved the entered partial first_name
        draft = WizardDraftManager.get_draft(self.client.session, self.admin_user.id)
        self.assertIsNotNone(draft)
        self.assertEqual(draft['step_data'].get('1', {}).get('first_name'), 'PartialOnly')

    def test_final_full_validation_blocks_incomplete_records(self):
        """Final submission at Step 8 validates entire wizard and blocks incomplete records."""
        emp = Employee.objects.create(
            employee_number='EMP-INCOMPLETE-001',
            first_name='Incomplete',
            last_name='OnlyLast',
            status=EmployeeStatus.DRAFT
        )

        session = self.client.session
        session[DRAFT_SESSION_KEY] = {
            f"emp_{emp.uuid}": {
                'user_id': self.admin_user.id,
                'updated_at': time.time(),
                'step_data': {'1': {'first_name': 'Incomplete'}},
                'step_statuses': {'1': 'incomplete'},
                'step_errors': {'1': {'personal_email': ['This field is required.']}}
            }
        }
        session.save()

        url_s8 = reverse('employees:employee_wizard_step', kwargs={'uuid': emp.uuid, 'step': 8})
        response = self.client.post(url_s8, {'action': 'approve'}, HTTP_HX_REQUEST='true')
        # Must return 200 with error summary, not 302 redirect
        self.assertEqual(response.status_code, 200)
        self.assertIn('validation_errors', response.context)
        self.assertTrue(len(response.context['validation_errors']) > 0)

        emp.refresh_from_db()
        self.assertEqual(emp.status, EmployeeStatus.DRAFT)  # Must NOT be Active

    def test_create_workflow_complete_to_active(self):
        """Complete create workflow from Step 1 to Step 8 resulting in Active employee."""
        # Step 1
        r1 = self.client.post(reverse('employees:employee_wizard'), {
            'employee_number': 'EMP-FLOW-001',
            'first_name': 'Full',
            'last_name': 'Lifecycle',
            'personal_email': 'fullflow@example.com',
            'phone': '+8801711000003',
            'target_step': '2'
        }, HTTP_HX_REQUEST='true')
        self.assertEqual(r1.status_code, 200)

        emp = Employee.objects.get(employee_number='EMP-FLOW-001')

        # Step 2
        r2 = self.client.post(reverse('employees:employee_wizard_step', kwargs={'uuid': emp.uuid, 'step': 2}), {
            'branch': self.branch.pk,
            'department': self.dept.pk,
            'designation': self.desig.pk,
            'employment_type': 'full_time',
            'joined_date': '2026-02-01',
            'shift': 'Day Shift',
            'target_step': '3'
        }, HTTP_HX_REQUEST='true')
        self.assertEqual(r2.status_code, 200)

        # Step 3
        r3 = self.client.post(reverse('employees:employee_wizard_step', kwargs={'uuid': emp.uuid, 'step': 3}), {
            'basic_salary': '55000.00',
            'payment_method': 'bank',
            'bank_name': 'City Bank Ltd',
            'bank_account': '123456789',
            'target_step': '4'
        }, HTTP_HX_REQUEST='true')
        self.assertEqual(r3.status_code, 200)

        # Step 4
        r4 = self.client.post(reverse('employees:employee_wizard_step', kwargs={'uuid': emp.uuid, 'step': 4}), {
            'login_email': 'fullflow_user@example.com',
            'password1': 'StrongPass123!',
            'password2': 'StrongPass123!',
            'roles': [self.role.pk],
            'data_scope': 'branch',
            'target_step': '8'
        }, HTTP_HX_REQUEST='true')
        self.assertEqual(r4.status_code, 200)

        # Step 8: Approve
        r8 = self.client.post(reverse('employees:employee_wizard_step', kwargs={'uuid': emp.uuid, 'step': 8}), {
            'action': 'approve'
        })
        self.assertEqual(r8.status_code, 302)

        emp.refresh_from_db()
        self.assertEqual(emp.status, EmployeeStatus.ACTIVE)
        self.assertIsNotNone(emp.user)
        self.assertEqual(emp.user.email, 'fullflow_user@example.com')
        self.assertTrue(UserRoleAssignment.objects.filter(user=emp.user, role=self.role).exists())

    def test_edit_workflow_preserves_and_updates_data(self):
        """Edit workflow on existing employee updates fields smoothly."""
        emp = Employee.objects.create(
            employee_number='EMP-EDIT-001',
            first_name='InitialName',
            last_name='InitialLast',
            personal_email='edit_initial@example.com',
            phone='+8801711000004',
            status=EmployeeStatus.DRAFT
        )

        url_s1 = reverse('employees:employee_wizard_step', kwargs={'uuid': emp.uuid, 'step': 1})
        response = self.client.post(url_s1, {
            'employee_number': 'EMP-EDIT-001',
            'first_name': 'UpdatedName',
            'last_name': 'UpdatedLast',
            'personal_email': 'edit_updated@example.com',
            'phone': '+8801711000004',
            'target_step': '2'
        }, HTTP_HX_REQUEST='true')

        self.assertEqual(response.status_code, 200)
        emp.refresh_from_db()
        self.assertEqual(emp.first_name, 'UpdatedName')
        self.assertEqual(emp.personal_email, 'edit_updated@example.com')

    def test_back_forward_refresh_direct_step_urls(self):
        """Direct step URLs return 200 for full page on browser refresh, partial on HTMX."""
        emp = Employee.objects.create(
            employee_number='EMP-URLS-001',
            first_name='Browser',
            last_name='Nav',
            status=EmployeeStatus.DRAFT
        )

        for s in range(1, 9):
            url = reverse('employees:employee_wizard_step', kwargs={'uuid': emp.uuid, 'step': s})
            # 1. Full page browser refresh (non-HTMX)
            res_full = self.client.get(url)
            self.assertEqual(res_full.status_code, 200)
            self.assertTemplateUsed(res_full, 'employees/employee_wizard.html')

            # 2. HTMX partial request (back/forward or tab click)
            res_htmx = self.client.get(url, HTTP_HX_REQUEST='true')
            self.assertEqual(res_htmx.status_code, 200)
            self.assertTemplateUsed(res_htmx, 'employees/wizard/wizard_content.html')

    def test_duplicate_employee_submission_prevention(self):
        """Prevent duplicate employee creation when email/phone already belongs to another employee."""
        Employee.objects.create(
            employee_number='EMP-EXISTING-001',
            first_name='Existing',
            last_name='User',
            personal_email='conflict@example.com',
            phone='+8801711999999'
        )

        # Attempt to create new employee with the same email
        url = reverse('employees:employee_wizard')
        response = self.client.post(url, {
            'employee_number': 'EMP-NEW-002',
            'first_name': 'Duplicate',
            'last_name': 'Attempt',
            'personal_email': 'conflict@example.com',
            'phone': '+8801711888888',
            'target_step': '2'
        }, HTTP_HX_REQUEST='true')

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Employee.objects.filter(employee_number='EMP-NEW-002').exists())
        self.assertIn(response.context['step_statuses'].get(1), ('incomplete', 'error'))

    def test_expired_draft_fails_safely(self):
        """Expired draft (older than 24 hours) is purged without overwriting database records."""
        # Create session with expired draft
        session = self.client.session
        expired_time = time.time() - 90000  # 25 hours ago
        session[DRAFT_SESSION_KEY] = {
            f"new_{self.admin_user.id}": {
                'user_id': self.admin_user.id,
                'updated_at': expired_time,
                'step_data': {'1': {'first_name': 'OldStaleData'}}
            }
        }
        session.save()

        # Check retrieval returns expired and purges
        draft = WizardDraftManager.get_draft(session, self.admin_user.id)
        self.assertTrue(draft.get('expired'))
        self.assertNotIn(f"new_{self.admin_user.id}", session.get(DRAFT_SESSION_KEY, {}))

    def test_unauthorized_cross_user_draft_access(self):
        """Draft belonging to User A cannot be accessed or modified by User B."""
        session = self.client.session
        session[DRAFT_SESSION_KEY] = {
            'emp_test_key': {
                'user_id': 99999,  # Different user
                'updated_at': time.time(),
                'step_data': {'1': {'first_name': 'SecretData'}}
            }
        }
        session.save()

        # Current user is self.admin_user (id != 99999)
        with self.assertRaises(PermissionDenied):
            WizardDraftManager.get_draft(session, self.admin_user.id, 'test_key')

    def test_atomic_rollback_on_activation_failure(self):
        """If any operation fails during Step 8 approval, entire activation is rolled back."""
        emp = Employee.objects.create(
            employee_number='EMP-ATOMIC-001',
            first_name='Atomic',
            last_name='Test',
            status=EmployeeStatus.DRAFT
        )

        url_s8 = reverse('employees:employee_wizard_step', kwargs={'uuid': emp.uuid, 'step': 8})

        # Mock log_audit to simulate database failure during final atomic block
        with patch('apps.employees.wizard_service.log_audit', side_effect=RuntimeError("Simulated audit DB error")):
            with self.assertRaises(RuntimeError):
                self.client.post(url_s8, {'action': 'approve'})

        # Verify rollback: status must STILL be DRAFT
        emp.refresh_from_db()
        self.assertEqual(emp.status, EmployeeStatus.DRAFT)
