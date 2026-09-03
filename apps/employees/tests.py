from django.test import TestCase
from django.contrib.auth import get_user_model, authenticate
from django.urls import reverse
from apps.employees.models import EmployeeProfile
from apps.employees.forms import EmployeeCreateForm, EmployeeEditForm
from apps.branches.models import Branch
from datetime import date

User = get_user_model()

class EmployeeProfileTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(
            name='Test Branch',
            latitude=23.8103,
            longitude=90.4125,
            radius_meters=100
        )
        self.password = 'testpassword123'

    def test_create_employee_without_department_designation(self):
        # Prepare form data leaving department and designation blank
        form_data = {
            'employee_id': 'EMP-2026-999',
            'full_name': 'John Doe',
            'branch': self.branch.id,
            'phone': '+8801799999999',
            'joined_date': date.today(),
            'is_active': True,
            'tracking_interval': 0,
            'role': 'staff',
            'password1': self.password,
            'password2': self.password,
        }
        
        form = EmployeeCreateForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors.as_data())
        
        # Save profile and associated User
        profile = form.save()
        
        # Verify nullable fields in database are None or empty
        self.assertIsNone(profile.department)
        self.assertIsNone(profile.designation)
        
        # Verify custom user is created with matching phone number
        user = profile.user
        self.assertEqual(user.phone, '+8801799999999')
        self.assertIsNone(user.email)
        
        # Verify authentication by phone number
        authenticated_user = authenticate(username='+8801799999999', password=self.password)
        self.assertIsNotNone(authenticated_user)
        self.assertEqual(authenticated_user, user)

    def test_edit_employee_clear_department_designation(self):
        # 1. Create a user and employee profile first
        user = User.objects.create_user(
            phone='+8801788888888',
            password=self.password,
            role='staff'
        )
        profile = EmployeeProfile.objects.create(
            user=user,
            employee_id='EMP-2026-888',
            full_name='Jane Doe',
            department='HR',
            designation='Manager',
            phone='+8801788888888',
            joined_date=date.today(),
            branch=self.branch
        )
        
        # 2. Use edit form to clear department and designation
        form_data = {
            'employee_id': 'EMP-2026-888',
            'full_name': 'Jane Doe',
            'role': 'staff',
            'department': '',  # clear it
            'designation': '', # clear it
            'branch': self.branch.id,
            'phone': '+8801788888888',
            'joined_date': date.today(),
            'is_active': True,
            'tracking_interval': 0,
            'new_password': '',
            'confirm_password': '',
        }
        
        form = EmployeeEditForm(data=form_data, instance=profile)
        self.assertTrue(form.is_valid(), form.errors.as_data())
        
        saved_profile = form.save()
        self.assertIn(saved_profile.department, [None, ''])
        self.assertIn(saved_profile.designation, [None, ''])

    def test_invalid_profile_photo_type(self):
        from io import BytesIO
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        
        # Create a valid BMP image (which is not in our allowed types: jpeg, png, webp)
        file_obj = BytesIO()
        image = Image.new("RGBA", size=(1, 1), color=(0, 0, 0, 0))
        image.save(file_obj, "bmp")
        file_obj.seek(0)
        invalid_file = SimpleUploadedFile("test.bmp", file_obj.read(), content_type="image/bmp")
        
        form_data = {
            'employee_id': 'EMP-2026-999',
            'full_name': 'John Doe',
            'branch': self.branch.id,
            'phone': '+8801799999999',
            'joined_date': date.today(),
            'is_active': True,
            'tracking_interval': 0,
            'role': 'staff',
            'password1': self.password,
            'password2': self.password,
        }
        form = EmployeeCreateForm(data=form_data, files={'profile_photo': invalid_file})
        self.assertFalse(form.is_valid())
        self.assertIn('profile_photo', form.errors)
        self.assertIn('Invalid file type', form.errors['profile_photo'][0])

    def test_large_profile_photo(self):
        from io import BytesIO
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        
        # Create a valid PNG image and pad it to be 6MB
        file_obj = BytesIO()
        image = Image.new("RGBA", size=(1, 1), color=(0, 0, 0, 0))
        image.save(file_obj, "png")
        file_obj.seek(0)
        img_bytes = file_obj.read()
        padded_bytes = img_bytes + b"0" * (6 * 1024 * 1024 - len(img_bytes))
        large_file = SimpleUploadedFile("test.png", padded_bytes, content_type="image/png")
        
        form_data = {
            'employee_id': 'EMP-2026-999',
            'full_name': 'John Doe',
            'branch': self.branch.id,
            'phone': '+8801799999999',
            'joined_date': date.today(),
            'is_active': True,
            'tracking_interval': 0,
            'role': 'staff',
            'password1': self.password,
            'password2': self.password,
        }
        form = EmployeeCreateForm(data=form_data, files={'profile_photo': large_file})
        self.assertFalse(form.is_valid())
        self.assertIn('profile_photo', form.errors)
        self.assertIn('File too large', form.errors['profile_photo'][0])

    def test_is_project_manager_toggle(self):
        # 1. Create with is_project_manager=True
        form_data = {
            'employee_id': 'EMP-2026-777',
            'full_name': 'Project Manager One',
            'branch': self.branch.id,
            'phone': '+8801777777777',
            'joined_date': date.today(),
            'is_active': True,
            'tracking_interval': 0,
            'role': 'staff',
            'password1': self.password,
            'password2': self.password,
            'is_project_manager': True,
        }
        form = EmployeeCreateForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors.as_data())
        profile = form.save()
        self.assertTrue(profile.is_project_manager)

        # 2. Edit with is_project_manager=False
        edit_data = {
            'employee_id': 'EMP-2026-777',
            'full_name': 'Project Manager One',
            'role': 'staff',
            'branch': self.branch.id,
            'phone': '+8801777777777',
            'joined_date': date.today(),
            'is_active': True,
            'tracking_interval': 0,
            'new_password': '',
            'confirm_password': '',
            'is_project_manager': False,
        }
        edit_form = EmployeeEditForm(data=edit_data, instance=profile)
        self.assertTrue(edit_form.is_valid(), edit_form.errors.as_data())
        profile = edit_form.save()
        self.assertFalse(profile.is_project_manager)


from apps.employees.models import EmployeeDocument
from apps.notifications.models import Notification
from django.core.management import call_command
from django.utils import timezone
import datetime

class EmployeeDocumentTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(
            name='Test Branch',
            latitude=23.8103,
            longitude=90.4125,
            radius_meters=100
        )
        # Create an admin user to be notified
        self.admin = User.objects.create_user(
            phone='+8801700000001',
            email='admin@test.com',
            password='adminpassword123',
            role='admin'
        )
        # Create an HR user to be notified
        self.hr = User.objects.create_user(
            phone='+8801700000002',
            email='hr@test.com',
            password='hrpassword123',
            role='hr'
        )
        # Create normal staff employee
        self.staff_user = User.objects.create_user(
            phone='+8801700000003',
            email='staff@test.com',
            password='staffpassword123',
            role='staff'
        )
        self.employee = EmployeeProfile.objects.create(
            user=self.staff_user,
            employee_id='EMP-2026-001',
            full_name='Test Employee',
            phone='+8801700000003',
            joined_date=date.today(),
            branch=self.branch
        )

    def test_document_expiry_alert_cron(self):
        today = timezone.localdate()
        
        # 1. Document expiring in 10 days (should trigger alert)
        doc1 = EmployeeDocument.objects.create(
            employee=self.employee,
            document_type='Visa',
            expiry_date=today + datetime.timedelta(days=10)
        )
        
        # 2. Document expiring in 40 days (should NOT trigger alert)
        doc2 = EmployeeDocument.objects.create(
            employee=self.employee,
            document_type='Certificate',
            expiry_date=today + datetime.timedelta(days=40)
        )

        # 3. Document already expired yesterday (should NOT trigger alert)
        doc3 = EmployeeDocument.objects.create(
            employee=self.employee,
            document_type='Old Visa',
            expiry_date=today - datetime.timedelta(days=2)
        )

        call_command('check_expiring_documents')

        # Check notifications for admin
        admin_notifications = Notification.objects.filter(recipient=self.admin)
        self.assertEqual(admin_notifications.count(), 1)
        self.assertEqual(admin_notifications.first().title, f"Document Expiring: {self.employee.full_name} (Visa)")
        
        # Check notifications for HR
        hr_notifications = Notification.objects.filter(recipient=self.hr)
        self.assertEqual(hr_notifications.count(), 1)
        self.assertEqual(hr_notifications.first().title, f"Document Expiring: {self.employee.full_name} (Visa)")

        # Verify idempotency
        call_command('check_expiring_documents')
        self.assertEqual(Notification.objects.filter(recipient=self.admin).count(), 1)

    def test_document_crud_access_control(self):
        url_add = reverse('employees:document_add', kwargs={'employee_pk': self.employee.pk})
        
        # Staff cannot add document
        self.client.force_login(self.staff_user)
        UserSession.objects.create(user=self.staff_user, session_key=self.client.session.session_key, is_active=True)
        response = self.client.get(url_add)
        self.assertEqual(response.status_code, 302)
        self.client.logout()

        # Admin can add document
        self.client.force_login(self.admin)
        UserSession.objects.create(user=self.admin, session_key=self.client.session.session_key, is_active=True)
        response = self.client.get(url_add)
        self.assertEqual(response.status_code, 200)

        # POST valid data
        post_data = {
            'document_type': 'contract',
            'expiry_date': '2026-12-31',
        }
        response = self.client.post(url_add, data=post_data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(EmployeeDocument.objects.filter(document_type='contract').exists())

    def test_document_expiry_7_days_email_escalation(self):
        import datetime
        from django.core import mail
        from django.core.management import call_command
        from apps.employees.models import EmployeeDocument
        
        # Clear outbox
        mail.outbox = []
        
        # Create a document expiring in 5 days
        EmployeeDocument.objects.create(
            employee=self.employee,
            document_type='Visa',
            expiry_date=date.today() + datetime.timedelta(days=5)
        )
        
        # Call management command
        call_command('check_expiring_documents')
        
        # Should have sent an email to the active admin user
        self.assertGreaterEqual(len(mail.outbox), 1)
        emails_to_admin = [m for m in mail.outbox if self.admin.email in m.to]
        self.assertEqual(len(emails_to_admin), 1)
        self.assertIn("URGENT: Document Expiring in 7 Days", emails_to_admin[0].subject)


from django.core.exceptions import ValidationError
from django.contrib.messages import get_messages
from apps.accounts.models import UserSession
from apps.employees.models import Employee, Department, Designation, EmployeeStatus, EmploymentHistory

class EmployeeMasterTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            email='adminmaster@test.com',
            password='AdminPassword123!'
        )
        self.branch = Branch.objects.create(
            name='Dhaka HQ',
            latitude=23.8103,
            longitude=90.4125
        )
        self.dept = Department.objects.create(name='Engineering', code='ENG')
        self.desig = Designation.objects.create(name='Senior Software Engineer', code='SSE')

    def test_employee_master_creation(self):
        emp = Employee.objects.create(
            employee_number='EMP-MASTER-001',
            first_name='John',
            last_name='Smith',
            dob='1990-05-15',
            gender='male',
            branch=self.branch,
            department=self.dept,
            designation=self.desig,
            status=EmployeeStatus.ACTIVE
        )
        self.assertEqual(emp.get_full_name(), 'John Smith')
        self.assertTrue(emp.is_login_allowed())
        self.assertEqual(str(emp), 'EMP-MASTER-001 - John Smith')

        # Test unique employee_number constraint
        with self.assertRaises(Exception):
            Employee.objects.create(
                employee_number='EMP-MASTER-001',
                first_name='Duplicate',
                last_name='User'
            )

    def test_circular_reporting_manager_rejection(self):
        emp_a = Employee.objects.create(
            employee_number='EMP-A',
            first_name='Alice',
            last_name='One',
            status=EmployeeStatus.ACTIVE
        )
        emp_b = Employee.objects.create(
            employee_number='EMP-B',
            first_name='Bob',
            last_name='Two',
            reporting_manager=emp_a,
            status=EmployeeStatus.ACTIVE
        )

        # 1. Self reporting
        emp_a.reporting_manager = emp_a
        with self.assertRaises(ValidationError):
            emp_a.save()

        # Reset emp_a
        emp_a.reporting_manager = None
        emp_a.save()

        # 2. Direct circular (A reports to B, B reports to A)
        emp_a.reporting_manager = emp_b
        with self.assertRaises(ValidationError):
            emp_a.save()

        # 3. Multi-level circular (A -> B -> C -> A)
        emp_a.reporting_manager = None
        emp_a.save()
        emp_c = Employee.objects.create(
            employee_number='EMP-C',
            first_name='Charlie',
            last_name='Three',
            reporting_manager=emp_b,
            status=EmployeeStatus.ACTIVE
        )
        # Now C reports to B, B reports to A. Making A report to C creates A -> C -> B -> A loop
        emp_a.reporting_manager = emp_c
        with self.assertRaises(ValidationError):
            emp_a.save()

    def test_soft_delete_employee(self):
        emp = Employee.objects.create(
            employee_number='EMP-SOFTDELETE',
            first_name='Soft',
            last_name='Delete',
            status=EmployeeStatus.ACTIVE
        )
        emp_id = emp.pk
        emp.delete()

        # Reload from DB
        reloaded = Employee.objects.get(pk=emp_id)
        self.assertTrue(reloaded.is_trashed)
        self.assertFalse(reloaded.is_login_allowed())

    def test_employment_history_immutability(self):
        emp = Employee.objects.create(
            employee_number='EMP-HIST',
            first_name='Hist',
            last_name='Test',
            status=EmployeeStatus.ACTIVE
        )
        history = EmploymentHistory.objects.create(
            employee=emp,
            field_changed='status',
            old_value='draft',
            new_value='active',
            reason='Initial creation'
        )

        # Updating existing history must raise ValidationError
        history.reason = 'Modified reason'
        with self.assertRaises(ValidationError):
            history.save()

        # Deleting history must raise ValidationError
        with self.assertRaises(ValidationError):
            history.delete()

    def test_customuser_linkage_and_login_status(self):
        user = User.objects.create_user(
            email='emplogin@test.com',
            password='UserPassword123!'
        )
        emp = Employee.objects.create(
            employee_number='EMP-LOGIN-001',
            first_name='Login',
            last_name='User',
            user=user,
            status=EmployeeStatus.DRAFT
        )

        # 1. Draft status -> login blocked
        self.assertFalse(emp.is_login_allowed())

        # 2. Active status -> login allowed
        Employee.objects.filter(pk=emp.pk).update(status=EmployeeStatus.ACTIVE)
        emp.refresh_from_db()
        self.assertTrue(emp.is_login_allowed())

        # 3. Trashed status -> login blocked
        emp.delete()
        emp.refresh_from_db()
        self.assertTrue(emp.is_trashed)
        self.assertFalse(emp.is_login_allowed())

    def test_master_crud_htmx_views(self):
        self.client.force_login(self.admin)
        UserSession.objects.create(user=self.admin, session_key=self.client.session.session_key, is_active=True)

        # 1. List view GET
        url_list = reverse('employees:master_list')
        res = self.client.get(url_list)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Employee Master Directory')

        # 2. Create view POST via HTMX
        url_create = reverse('employees:master_create')
        post_data = {
            'employee_number': 'EMP-HTMX-001',
            'first_name': 'HTMX',
            'last_name': 'User',
            'branch': self.branch.id,
            'department': self.dept.id,
            'designation': self.desig.id
        }
        res_create = self.client.post(url_create, data=post_data, HTTP_HX_REQUEST='true')
        self.assertEqual(res_create.status_code, 200)
        emp = Employee.objects.get(employee_number='EMP-HTMX-001')
        self.assertEqual(emp.get_full_name(), 'HTMX User')

        # 3. Detail view GET
        url_detail = reverse('employees:master_detail', kwargs={'pk': emp.pk})
        res_detail = self.client.get(url_detail)
        self.assertEqual(res_detail.status_code, 200)
        self.assertContains(res_detail, 'EMP-HTMX-001')

        # 4. Edit view POST via HTMX
        url_edit = reverse('employees:master_edit', kwargs={'pk': emp.pk})
        edit_data = {
            'employee_number': 'EMP-HTMX-001',
            'first_name': 'HTMX',
            'last_name': 'User-Updated',
            'branch': self.branch.id,
            'department': self.dept.id,
            'designation': self.desig.id,
            'change_reason': 'Profile Update'
        }
        res_edit = self.client.post(url_edit, data=edit_data, HTTP_HX_REQUEST='true')
        self.assertEqual(res_edit.status_code, 200)
        emp.refresh_from_db()
        self.assertEqual(emp.last_name, 'User-Updated')

        # 5. Archive view POST
        url_archive = reverse('employees:master_archive', kwargs={'pk': emp.pk})
        res_archive = self.client.post(url_archive, HTTP_HX_REQUEST='true')
        self.assertEqual(res_archive.status_code, 200)
        emp.refresh_from_db()
        self.assertEqual(emp.status, 'archived')

    def test_legacy_profile_reconciliation_sync(self):
        user = User.objects.create_user(email='reconcile@test.com', phone='+8801711111111', password='Password123!')
        legacy_prof = EmployeeProfile.objects.create(
            user=user,
            employee_id='EMP-LEGACY-01',
            full_name='Old Name',
            phone='+8801711111111',
            joined_date=date.today(),
            branch=self.branch
        )

        master = Employee.objects.create(
            employee_number='EMP-MASTER-RECON',
            first_name='Reconciled',
            last_name='User',
            phone='+8801711112222',
            branch=self.branch,
            user=user,
            status=EmployeeStatus.ACTIVE
        )

        legacy_prof.refresh_from_db()
        self.assertEqual(legacy_prof.master_employee, master)
        self.assertEqual(legacy_prof.full_name, 'Reconciled User')


from django.core.files.uploadedfile import SimpleUploadedFile
from apps.employees.models import EmployeeDocument, DocumentType, DocumentDownloadLog, Asset, AssetAssignment, AssetType, AssetCondition

class Step3DocumentAndAssetTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email='docassetadmin@test.com', password='Password123!', role='admin')
        self.staff_user1 = User.objects.create_user(email='staff1@test.com', password='Password123!', role='staff')
        self.staff_user2 = User.objects.create_user(email='staff2@test.com', password='Password123!', role='staff')

        self.branch = Branch.objects.create(name='DocBranch', latitude=23.8, longitude=90.4)

        self.emp1 = Employee.objects.create(
            employee_number='EMP-DOC-1',
            first_name='DocOwner',
            last_name='Staff',
            branch=self.branch,
            user=self.staff_user1,
            status=EmployeeStatus.ACTIVE
        )

        self.emp2 = Employee.objects.create(
            employee_number='EMP-DOC-2',
            first_name='OtherStaff',
            last_name='User',
            branch=self.branch,
            user=self.staff_user2,
            status=EmployeeStatus.ACTIVE
        )

    def test_document_versioning_and_deactivation(self):
        test_file = SimpleUploadedFile("nid_v1.pdf", b"file_content_v1", content_type="application/pdf")
        doc1 = EmployeeDocument.objects.create(
            employee_master=self.emp1,
            document_type=DocumentType.NID,
            file=test_file,
            uploaded_by=self.admin
        )
        self.assertEqual(doc1.version, 1)
        self.assertTrue(doc1.is_active)

        test_file2 = SimpleUploadedFile("nid_v2.pdf", b"file_content_v2", content_type="application/pdf")
        doc2 = EmployeeDocument.objects.create(
            employee_master=self.emp1,
            document_type=DocumentType.NID,
            file=test_file2,
            uploaded_by=self.admin
        )
        doc1.refresh_from_db()
        self.assertEqual(doc2.version, 2)
        self.assertTrue(doc2.is_active)
        self.assertFalse(doc1.is_active)

    def test_sensitive_document_rbac_permission(self):
        test_file = SimpleUploadedFile("nid.pdf", b"secret_content", content_type="application/pdf")
        doc = EmployeeDocument.objects.create(
            employee_master=self.emp1,
            document_type=DocumentType.NID,
            file=test_file
        )

        # 1. Staff 2 (unauthorized) tries to download Staff 1's NID -> 403
        self.client.force_login(self.staff_user2)
        UserSession.objects.create(user=self.staff_user2, session_key=self.client.session.session_key, is_active=True)
        res_denied = self.client.get(reverse('employees:document_download', kwargs={'pk': doc.pk}))
        self.assertEqual(res_denied.status_code, 403)

        # 2. Staff 1 (self) downloads -> 200
        self.client.force_login(self.staff_user1)
        UserSession.objects.create(user=self.staff_user1, session_key=self.client.session.session_key, is_active=True)
        res_self = self.client.get(reverse('employees:document_download', kwargs={'pk': doc.pk}))
        self.assertEqual(res_self.status_code, 200)

        # 3. Download log created
        self.assertTrue(DocumentDownloadLog.objects.filter(document=doc, downloaded_by=self.staff_user1).exists())

    def test_asset_double_assignment_prevention_and_return(self):
        asset = Asset.objects.create(
            asset_tag='AST-LAPTOP-01',
            name='MacBook Pro',
            asset_type=AssetType.LAPTOP,
            condition=AssetCondition.NEW
        )

        # 1. Assign to Emp 1
        assign1 = AssetAssignment.objects.create(
            asset=asset,
            employee=self.emp1,
            assigned_date=date.today(),
            condition_at_assignment=AssetCondition.NEW
        )
        self.assertTrue(asset.is_assigned())

        # 2. Attempting second active assignment to Emp 2 raises ValidationError
        assign2 = AssetAssignment(
            asset=asset,
            employee=self.emp2,
            assigned_date=date.today(),
            condition_at_assignment=AssetCondition.GOOD
        )
        with self.assertRaises(ValidationError):
            assign2.save()

        # 3. Return asset
        assign1.returned_date = date.today()
        assign1.condition_at_return = AssetCondition.GOOD
        assign1.save()

        self.assertFalse(asset.is_assigned())

        # 4. Now Emp 2 can be assigned
        assign2.save()
        self.assertTrue(asset.is_assigned())
        self.assertEqual(asset.current_assignment().employee, self.emp2)


from django.core.exceptions import ValidationError
from apps.employees.models import Employee, EmployeeStatus, EmploymentHistory, LifecycleTransitionRequest
from apps.employees.forms import EmployeeMasterForm

class LifecycleStateMachineTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email='admin_life@example.com', password='pass123', role='admin')
        self.staff = User.objects.create_user(email='staff_life@example.com', password='pass123', role='staff')
        self.employee = Employee.objects.create(
            employee_number='EMP-LIFE-001',
            first_name='Life',
            last_name='Cycle',
            status=EmployeeStatus.DRAFT
        )

    def test_status_removed_from_master_form(self):
        form = EmployeeMasterForm()
        self.assertNotIn('status', form.fields)

    def test_invalid_transition_rejected_by_clean(self):
        self.employee.status = EmployeeStatus.CONFIRMED  # Draft -> Confirmed is invalid
        with self.assertRaises(ValidationError) as ctx:
            self.employee.clean()
        self.assertIn('Invalid transition', str(ctx.exception))
        self.assertIn('draft', str(ctx.exception))

    def test_low_risk_transition_applies_immediately(self):
        self.client.force_login(self.admin)
        url = reverse('employees:lifecycle_action', kwargs={'pk': self.employee.pk})
        response = self.client.post(url, {
            'to_status': 'pending_approval',
            'reason': 'Submitting draft for approval',
            'effective_date': '2026-07-24'
        })
        self.assertEqual(response.status_code, 302)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.status, 'pending_approval')
        self.assertTrue(EmploymentHistory.objects.filter(employee=self.employee, new_value='Pending Approval').exists())
        # Logs should be created
        from apps.audit.models import AuditEvent
        self.assertTrue(AuditEvent.objects.filter(object_id=str(self.employee.pk)).exists())

    def test_high_risk_transition_creates_pending_request_and_does_not_change_status(self):
        # Move to ACTIVE first via update to bypass state machine check for setup
        Employee.objects.filter(pk=self.employee.pk).update(status=EmployeeStatus.ACTIVE)
        self.employee.refresh_from_db()

        self.client.force_login(self.admin)
        url = reverse('employees:lifecycle_action', kwargs={'pk': self.employee.pk})
        response = self.client.post(url, {
            'to_status': 'probation',
            'reason': 'Put on probation',
            'effective_date': '2026-07-24'
        })
        self.assertEqual(response.status_code, 302)

        # Status must still be ACTIVE
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.status, EmployeeStatus.ACTIVE)

        # Pending request created
        req = LifecycleTransitionRequest.objects.get(employee=self.employee, to_status='probation')
        self.assertEqual(req.review_status, 'pending')
        self.assertEqual(req.requested_by, self.admin)

    def test_approve_high_risk_transition(self):
        Employee.objects.filter(pk=self.employee.pk).update(status=EmployeeStatus.ACTIVE)
        self.employee.refresh_from_db()

        req = LifecycleTransitionRequest.objects.create(
            employee=self.employee,
            from_status='active',
            to_status='probation',
            reason='Probation test',
            requested_by=self.staff
        )

        self.client.force_login(self.admin)
        review_url = reverse('employees:lifecycle_review', kwargs={'req_pk': req.pk})
        response = self.client.post(review_url, {
            'action': 'approve',
            'review_note': 'Approved!'
        })
        self.assertEqual(response.status_code, 302)

        req.refresh_from_db()
        self.assertEqual(req.review_status, 'approved')
        self.assertEqual(req.reviewed_by, self.admin)

        self.employee.refresh_from_db()
        self.assertEqual(self.employee.status, 'probation')
        self.assertTrue(EmploymentHistory.objects.filter(employee=self.employee, new_value='Probation').exists())

    def test_reject_high_risk_transition(self):
        Employee.objects.filter(pk=self.employee.pk).update(status=EmployeeStatus.ACTIVE)
        self.employee.refresh_from_db()

        req = LifecycleTransitionRequest.objects.create(
            employee=self.employee,
            from_status='active',
            to_status='probation',
            reason='Probation test',
            requested_by=self.staff
        )

        self.client.force_login(self.admin)
        review_url = reverse('employees:lifecycle_review', kwargs={'req_pk': req.pk})
        response = self.client.post(review_url, {
            'action': 'reject',
            'review_note': 'Not eligible'
        })
        self.assertEqual(response.status_code, 302)

        req.refresh_from_db()
        self.assertEqual(req.review_status, 'rejected')
        self.assertEqual(req.reviewed_by, self.admin)

        self.employee.refresh_from_db()
        self.assertEqual(self.employee.status, EmployeeStatus.ACTIVE)

    def test_archived_employee_read_only(self):
        Employee.objects.filter(pk=self.employee.pk).update(status=EmployeeStatus.ARCHIVED)
        self.employee.refresh_from_db()
        self.employee.first_name = 'Changed'
        with self.assertRaises(ValidationError) as ctx:
            self.employee.clean()
        self.assertIn("Archived employees are read-only and cannot be modified.", str(ctx.exception))

    def test_mandatory_reason_enforced(self):
        Employee.objects.filter(pk=self.employee.pk).update(status=EmployeeStatus.ACTIVE)
        self.employee.refresh_from_db()

        from apps.employees.views import _apply_transition
        class FakeReq:
            to_status = 'suspended'
            reason = ''
            effective_date = date.today()

        with self.assertRaises(ValidationError) as ctx:
            _apply_transition(self.employee, FakeReq(), self.admin)
        self.assertIn("A transition reason is mandatory for 'suspended' status.", str(ctx.exception))


class EmployeeWizardTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            email='wizardadmin@test.com',
            password='password123',
            role='admin'
        )
        self.staff = User.objects.create_user(
            email='wizardstaff@test.com',
            password='password123',
            role='staff'
        )
        self.client.force_login(self.admin)
        from apps.accounts.rbac_models import Role
        self.role = Role.objects.create(name='Staff Role', code='staff_role', is_active=True)

    def test_wizard_step_1_saves_draft_employee(self):
        url = reverse('employees:employee_wizard')
        data = {
            'employee_number': 'EMP-WIZ-001',
            'first_name': 'Wizard',
            'last_name': 'Test',
            'personal_email': 'wizard@test.com',
            'phone': '+8801700000001',
            'next_step': '2'
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        from apps.employees.models import Employee, EmployeeStatus
        emp = Employee.objects.get(employee_number='EMP-WIZ-001')
        self.assertEqual(emp.status, EmployeeStatus.DRAFT)
        self.assertEqual(emp.get_completion_percentage(), 20)

    def test_wizard_step_4_creates_user_and_user_role_assignment(self):
        from apps.employees.models import Employee, EmployeeStatus
        emp = Employee.objects.create(
            employee_number='EMP-WIZ-004',
            first_name='Sec',
            last_name='User',
            status=EmployeeStatus.DRAFT
        )
        url = reverse('employees:employee_wizard_step', kwargs={'uuid': emp.uuid, 'step': 4})
        data = {
            'login_email': 'secuser@test.com',
            'password1': 'secpassword123',
            'password2': 'secpassword123',
            'roles': [self.role.pk],
            'data_scope': 'branch',
            'next_step': '5'
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        emp.refresh_from_db()
        self.assertIsNotNone(emp.user)
        self.assertEqual(emp.user.email, 'secuser@test.com')
        from apps.accounts.rbac_models import UserRoleAssignment
        self.assertTrue(UserRoleAssignment.objects.filter(user=emp.user, role=self.role).exists())

    def test_wizard_step_8_approves_draft_to_active(self):
        from apps.employees.models import Employee, EmployeeStatus
        emp = Employee.objects.create(
            employee_number='EMP-WIZ-008',
            first_name='Approved',
            last_name='Emp',
            status=EmployeeStatus.DRAFT
        )
        url = reverse('employees:employee_wizard_step', kwargs={'uuid': emp.uuid, 'step': 8})
        response = self.client.post(url, {'action': 'approve'})
        self.assertEqual(response.status_code, 302)
        emp.refresh_from_db()
        self.assertEqual(emp.status, EmployeeStatus.ACTIVE)

    def test_sensitive_document_rbac_download_gate(self):
        from apps.employees.models import Employee, EmployeeDocument, DocumentType
        emp = Employee.objects.create(
            employee_number='EMP-DOC-001',
            first_name='Doc',
            last_name='Owner'
        )
        from django.core.files.uploadedfile import SimpleUploadedFile
        doc_file = SimpleUploadedFile("nid.pdf", b"file_content", content_type="application/pdf")
        doc = EmployeeDocument.objects.create(
            employee_master=emp,
            document_type=DocumentType.NID,
            title='National ID',
            file=doc_file
        )
        self.assertTrue(doc.is_sensitive())

        # Test non-superuser staff user without permission
        self.client.force_login(self.staff)
        download_url = reverse('employees:document_download', kwargs={'pk': doc.pk})
        response = self.client.get(download_url)
        self.assertEqual(response.status_code, 403)

        # Test admin superuser
        self.client.force_login(self.admin)
        response = self.client.get(download_url)
        self.assertEqual(response.status_code, 200)

    def test_asset_double_assignment_blocked(self):
        from apps.employees.models import Employee, Asset, AssetAssignment, AssetType
        from django.core.exceptions import ValidationError
        emp1 = Employee.objects.create(employee_number='EMP-AST-1', first_name='A', last_name='1')
        emp2 = Employee.objects.create(employee_number='EMP-AST-2', first_name='B', last_name='2')
        asset = Asset.objects.create(asset_type=AssetType.LAPTOP, asset_tag='TAG-001', name='MacBook Pro')

        AssetAssignment.objects.create(asset=asset, employee=emp1)
        with self.assertRaises(ValidationError):
            AssetAssignment.objects.create(asset=asset, employee=emp2)


class SuspensionTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email='suspend_admin@test.com', password='password123', role='admin')
        self.staff_user = User.objects.create_user(email='suspend_staff@test.com', password='password123', role='staff')
        
        from apps.employees.models import Employee, EmployeeStatus
        self.employee = Employee.objects.create(
            employee_number='EMP-SUS-001',
            first_name='Suspended',
            last_name='User',
            status=EmployeeStatus.ACTIVE,
            user=self.staff_user
        )
        # Create legacy profile and link it (since attendance/leave views look up employee_profile)
        from apps.employees.models import EmployeeProfile
        self.profile = EmployeeProfile.objects.create(
            user=self.staff_user,
            master_employee=self.employee,
            employee_id='EMP-SUS-001',
            full_name='Suspended User',
            phone='+8801700000002',
            joined_date=timezone.localdate()
        )

    def test_suspension_blocks_login_middleware(self):
        self.employee.is_suspended = True
        self.employee.save()
        
        self.client.force_login(self.staff_user)
        response = self.client.get(reverse('employees:employee_list'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('suspended=true', response.url)

    def test_suspension_blocks_attendance_clock_in(self):
        self.employee.is_suspended = True
        self.employee.save()
        
        # Test normal request redirects (302)
        self.client.force_login(self.staff_user)
        response = self.client.post(reverse('attendance:check_in'), {
            'latitude': 23.8103,
            'longitude': 90.4125
        })
        self.assertEqual(response.status_code, 302)

        # Test HTMX request returns 403
        self.client.force_login(self.staff_user)
        response = self.client.post(reverse('attendance:check_in'), {
            'latitude': 23.8103,
            'longitude': 90.4125
        }, HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 403)
        self.assertIn('suspended', response.json().get('error', '').lower())

    def test_suspension_blocks_leave_submission(self):
        self.employee.is_suspended = True
        self.employee.save()
        
        self.client.force_login(self.staff_user)
        from apps.leave.models import LeaveType
        lt = LeaveType.objects.create(name='Casual', default_days_per_year=10, category='casual')
        
        # Test normal request redirects (302)
        response = self.client.post(reverse('leave:staff_request_create'), {
            'leave_type': lt.pk,
            'start_date': '2026-08-01',
            'end_date': '2026-08-02',
            'reason': 'Vacation'
        })
        self.assertEqual(response.status_code, 302)

        # Test HTMX request returns 403
        self.client.force_login(self.staff_user)
        response = self.client.post(reverse('leave:staff_request_create'), {
            'leave_type': lt.pk,
            'start_date': '2026-08-01',
            'end_date': '2026-08-02',
            'reason': 'Vacation'
        }, HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 403)

    def test_suspend_action_toggle_and_history(self):
        self.client.force_login(self.admin)
        url = reverse('employees:employee_suspend_toggle', kwargs={'pk': self.employee.pk})
        
        self.assertFalse(self.employee.is_suspended)
        
        response = self.client.post(url, {'reason': 'Violation of policy'})
        self.assertEqual(response.status_code, 302)
        
        self.employee.refresh_from_db()
        self.assertTrue(self.employee.is_suspended)
        
        from apps.employees.models import EmploymentHistory
        hist = EmploymentHistory.objects.filter(employee=self.employee, field_changed='is_suspended').first()
        self.assertIsNotNone(hist)
        self.assertEqual(hist.new_value, 'True')
        self.assertEqual(hist.reason, 'Violation of policy')


class ActivityLogTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email='activity_admin@test.com', password='password123', role='admin')
        self.staff_user = User.objects.create_user(email='activity_staff@test.com', password='password123', role='staff')
        
        from apps.employees.models import Employee, EmployeeStatus
        self.employee = Employee.objects.create(
            employee_number='EMP-ACT-001',
            first_name='Activity',
            last_name='User',
            status=EmployeeStatus.ACTIVE,
            user=self.staff_user
        )

    def test_activity_log_on_edit(self):
        self.client.force_login(self.admin)
        url = reverse('employees:master_edit', kwargs={'pk': self.employee.pk})
        
        response = self.client.post(url, {
            'first_name': 'UpdatedName',
            'last_name': 'User',
            'employee_number': 'EMP-ACT-001',
            'gender': 'male',
            'joined_date': '2026-07-01',
            'employment_type': 'full_time',
            'status': 'active',
            'change_reason': 'Updating name for test'
        })
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.first_name, 'UpdatedName')

        from apps.audit.models import AuditEvent
        logs = AuditEvent.objects.filter(object_id=str(self.employee.pk))
        self.assertTrue(logs.exists())
        self.assertTrue(any('first_name' in l.changed_fields for l in logs))
        log_first_name = logs.first()
        self.assertIsNotNone(log_first_name)

    def test_activity_log_on_archive(self):
        self.client.force_login(self.admin)
        url = reverse('employees:master_archive', kwargs={'pk': self.employee.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)

        from apps.audit.models import AuditEvent
        log = AuditEvent.objects.filter(object_id=str(self.employee.pk), action="deleted").first()
        self.assertIsNotNone(log)


class AuditLogTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email='audit_admin@test.com', password='password123', role='admin')
        self.staff_user = User.objects.create_user(email='audit_staff@test.com', password='password123', role='staff')
        
        from apps.employees.models import Employee, EmployeeStatus
        self.employee = Employee.objects.create(
            employee_number='EMP-AUD-001',
            first_name='Audit',
            last_name='User',
            status=EmployeeStatus.ACTIVE,
            user=self.staff_user
        )

    def test_audit_log_immutability(self):
        from apps.employees.models import EmployeeAuditLog
        from django.core.exceptions import ValidationError
        
        log = EmployeeAuditLog.objects.create(
            employee=self.employee,
            old_value={"first_name": "OldName"},
            new_value={"first_name": "NewName"},
            changed_by=self.admin
        )
        log.old_value = {"first_name": "Hack"}
        with self.assertRaises(ValidationError):
            log.save()
            
        with self.assertRaises(ValidationError):
            log.delete()

    def test_audit_log_admin_permissions(self):
        from apps.employees.admin import EmployeeAuditLogAdmin
        from apps.employees.models import EmployeeAuditLog
        from django.contrib.admin.sites import AdminSite
        
        site = AdminSite()
        admin_obj = EmployeeAuditLogAdmin(EmployeeAuditLog, site)
        
        self.assertFalse(admin_obj.has_add_permission(None))
        self.assertFalse(admin_obj.has_change_permission(None))
        self.assertFalse(admin_obj.has_delete_permission(None))

    def test_audit_log_written_on_edit(self):
        self.client.force_login(self.admin)
        url = reverse('employees:master_edit', kwargs={'pk': self.employee.pk})
        
        response = self.client.post(url, {
            'first_name': 'NewAuditName',
            'last_name': 'User',
            'employee_number': 'EMP-AUD-001',
            'gender': 'male',
            'joined_date': '2026-07-01',
            'employment_type': 'full_time',
            'status': 'active',
            'change_reason': 'Audit update test'
        })
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.first_name, 'NewAuditName')

        from apps.audit.models import AuditEvent
        audit = AuditEvent.objects.filter(object_id=str(self.employee.pk)).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.before_data.get('first_name'), 'Audit')
        self.assertEqual(audit.after_data.get('first_name'), 'NewAuditName')


class DeviceLifecycleTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email='dev_admin@test.com', password='password123', role='admin')
        
        from apps.employees.models import Employee, EmployeeStatus, Asset, AssetType, AssetCondition, AssetAssignment
        
        self.emp1 = Employee.objects.create(
            employee_number='EMP-DEV-001',
            first_name='Device',
            last_name='One',
            status=EmployeeStatus.ACTIVE
        )
        self.emp2 = Employee.objects.create(
            employee_number='EMP-DEV-002',
            first_name='Device',
            last_name='Two',
            status=EmployeeStatus.ACTIVE
        )
        self.asset = Asset.objects.create(
            asset_type=AssetType.LAPTOP,
            asset_tag='AST-DEV-999',
            name='Test Laptop',
            condition=AssetCondition.NEW,
            is_active=True
        )
        self.assignment = AssetAssignment.objects.create(
            asset=self.asset,
            employee=self.emp1,
            assigned_date='2026-07-01',
            condition_at_assignment=AssetCondition.NEW,
            assigned_by=self.admin
        )

    def test_asset_reassignment(self):
        self.client.force_login(self.admin)
        url = reverse('employees:asset_reassign', kwargs={'pk': self.assignment.pk})
        
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Reassign Asset')

        response = self.client.post(url, {
            'returned_date': '2026-07-15',
            'condition_at_return': 'good',
            'return_notes': 'Returned for upgrade',
            'new_employee': self.emp2.pk,
            'assigned_date': '2026-07-16',
            'condition_at_assignment': 'good',
            'new_notes': 'Reassigned to new hire'
        })
        self.assertEqual(response.status_code, 302)

        self.assignment.refresh_from_db()
        self.assertEqual(str(self.assignment.returned_date), '2026-07-15')
        self.assertEqual(self.assignment.condition_at_return, 'good')
        self.assertEqual(self.assignment.notes, 'Returned for upgrade')
        
        self.assertIsNotNone(self.assignment.reassigned_to)
        new_assign = self.assignment.reassigned_to
        self.assertEqual(new_assign.employee, self.emp2)
        self.assertEqual(new_assign.asset, self.asset)
        self.assertEqual(str(new_assign.assigned_date), '2026-07-16')
        self.assertEqual(new_assign.condition_at_assignment, 'good')
        self.assertEqual(new_assign.notes, 'Reassigned to new hire')


class DocumentLifecycleTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email='doc_admin@test.com', password='password123', role='admin')
        
        from apps.employees.models import Employee, EmployeeStatus, EmployeeDocument, DocumentType
        self.employee = Employee.objects.create(
            employee_number='EMP-DOC-001',
            first_name='Doc',
            last_name='User',
            status=EmployeeStatus.ACTIVE
        )
        self.document = EmployeeDocument.objects.create(
            employee_master=self.employee,
            document_type=DocumentType.NID,
            title='NID Card',
            is_active=True
        )

    def test_document_verification(self):
        self.client.force_login(self.admin)
        url = reverse('employees:document_verify', kwargs={'pk': self.document.pk})
        
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        
        self.document.refresh_from_db()
        self.assertTrue(self.document.is_verified)
        self.assertEqual(self.document.verified_by, self.admin)
        self.assertIsNotNone(self.document.verified_at)

        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        
        self.document.refresh_from_db()
        self.assertFalse(self.document.is_verified)
        self.assertIsNone(self.document.verified_by)
        self.assertIsNone(self.document.verified_at)

    def test_document_archiving(self):
        self.client.force_login(self.admin)
        url = reverse('employees:document_archive', kwargs={'pk': self.document.pk})
        
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        
        self.document.refresh_from_db()
        self.assertTrue(self.document.is_archived)

        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        
        self.document.refresh_from_db()
        self.assertFalse(self.document.is_archived)


class ReportingManagerChainTests(TestCase):
    def setUp(self):
        self.manager_user = User.objects.create_user(email='chain_manager@test.com', password='password123', role='manager')
        self.staff_user = User.objects.create_user(email='chain_staff@test.com', password='password123', role='staff')
        
        from apps.employees.models import Employee, EmployeeStatus, EmployeeProfile
        
        self.manager_master = Employee.objects.create(
            employee_number='EMP-MGR-001',
            first_name='Chain',
            last_name='Manager',
            status=EmployeeStatus.ACTIVE,
            user=self.manager_user
        )
        self.manager_profile = EmployeeProfile.objects.create(
            user=self.manager_user,
            full_name='Chain Manager',
            employee_id='EMP-MGR-001',
            master_employee=self.manager_master,
            joined_date='2026-07-01',
            phone='01711111111'
        )

        self.staff_master = Employee.objects.create(
            employee_number='EMP-STF-001',
            first_name='Chain',
            last_name='Staff',
            status=EmployeeStatus.ACTIVE,
            user=self.staff_user,
            reporting_manager=self.manager_master
        )
        self.staff_profile = EmployeeProfile.objects.create(
            user=self.staff_user,
            full_name='Chain Staff',
            employee_id='EMP-STF-001',
            master_employee=self.staff_master,
            joined_date='2026-07-01',
            phone='01722222222'
        )

        from apps.accounts.rbac_models import Permission as RBACPermission, Role as RBACRole, UserRoleAssignment, RolePermission, Module, Action
        
        self.role_obj = RBACRole.objects.create(name='manager_role', code='manager_role')
        
        self.mod_leave = Module.objects.create(name='Leave', code='leave')
        self.act_approve = Action.objects.create(name='Approve', code='approve')
        
        self.perm_leave = RBACPermission.objects.create(
            module=self.mod_leave,
            action=self.act_approve,
            codename='leave.approve',
            name='Approve Leave'
        )
        
        self.mod_expense = Module.objects.create(name='Expense', code='expense')
        self.perm_expense = RBACPermission.objects.create(
            module=self.mod_expense,
            action=self.act_approve,
            codename='expense.approve',
            name='Approve Expense'
        )
        
        RolePermission.objects.create(role=self.role_obj, permission=self.perm_leave, data_scope='team')
        RolePermission.objects.create(role=self.role_obj, permission=self.perm_expense, data_scope='team')
        
        UserRoleAssignment.objects.create(user=self.manager_user, role=self.role_obj)

        from apps.leave.models import LeaveType, LeaveRequest
        self.leave_type = LeaveType.objects.create(name='Casual Leave', default_days_per_year=10, is_default=True)
        from datetime import date
        self.leave_request = LeaveRequest.objects.create(
            employee=self.staff_profile,
            leave_type=self.leave_type,
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 5),
            status='pending',
            reason='Family trip'
        )

        from apps.expense.models import Expense, ExpenseCategory
        cat = ExpenseCategory.objects.create(name='Travel', code='travel')
        self.expense = Expense.objects.create(
            employee=self.staff_profile,
            amount=500.00,
            category=cat,
            description='Client site travel',
            status='pending_manager'
        )

    def test_leave_approval_via_reporting_manager(self):
        self.client.force_login(self.manager_user)
        url = reverse('leave:approve_request', kwargs={'pk': self.leave_request.pk})
        
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        
        self.leave_request.refresh_from_db()
        self.assertEqual(self.leave_request.status, 'approved')

    def test_expense_approval_via_reporting_manager(self):
        self.client.force_login(self.manager_user)
        url = reverse('expense:approve_expense', kwargs={'pk': self.expense.pk})
        
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        
        self.expense.refresh_from_db()
        self.assertEqual(self.expense.status, 'pending_finance')


class EmployeeStatusEngineTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name='Test Branch', latitude=23.8, longitude=90.4, radius_meters=100)
        self.user = User.objects.create_user(phone='+8801700000010', password='pass123', role='staff')
        self.employee = Employee.objects.create(
            employee_number='EMP-STAT-001',
            first_name='Status',
            last_name='Engine',
            status=EmployeeStatus.DRAFT,
            user=self.user
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            master_employee=self.employee,
            employee_id='EMP-STAT-001',
            full_name='Status Engine',
            phone='+8801700000010',
            joined_date=date.today(),
            branch=self.branch
        )

    def test_business_status_mapping(self):
        self.assertEqual(self.employee.business_status, 'inactive')
        self.assertEqual(self.employee.business_status_display, 'Inactive')

        Employee.objects.filter(pk=self.employee.pk).update(status=EmployeeStatus.ACTIVE)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.business_status, 'active')

        Employee.objects.filter(pk=self.employee.pk).update(status=EmployeeStatus.PROBATION)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.business_status, 'on_probation')

        Employee.objects.filter(pk=self.employee.pk).update(status=EmployeeStatus.SUSPENDED)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.business_status, 'suspended')

    def test_bidirectional_suspension_sync(self):
        Employee.objects.filter(pk=self.employee.pk).update(status=EmployeeStatus.ACTIVE)
        self.employee.refresh_from_db()

        self.employee.status = EmployeeStatus.SUSPENDED
        self.employee.save()
        self.assertTrue(self.employee.is_suspended)

        self.employee.is_suspended = False
        self.employee.save()
        self.assertEqual(self.employee.status, EmployeeStatus.ACTIVE)


class EmployeeTimelineEngineTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email='admin_time@example.com', password='pass123', role='admin')
        self.employee = Employee.objects.create(
            employee_number='EMP-TIME-001',
            first_name='Time',
            last_name='Engine',
            status=EmployeeStatus.DRAFT
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.admin,
            master_employee=self.employee,
            employee_id='EMP-TIME-001',
            full_name='Time Engine',
            phone='+8801700000020',
            joined_date=date.today()
        )

    def test_timeline_compilation_and_filters(self):
        self.client.force_login(self.admin)
        url = reverse('employees:employee_timeline', kwargs={'pk': self.employee.pk})

        # Add history record
        EmploymentHistory.objects.create(
            employee=self.employee,
            field_changed='status',
            old_value='draft',
            new_value='active',
            reason='Hired',
            approved_by=self.admin
        )

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Field &#x27;Status&#x27; updated")
        self.assertContains(response, "Hired")

        # Test filter by category
        response_leave = self.client.get(url + "?category=leave")
        self.assertEqual(response_leave.status_code, 200)
        self.assertNotContains(response_leave, "Field 'Status' updated")


class OrgHierarchyTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email='admin_h@example.com', password='pass123', role='admin')
        
        # Create hierarchy: CEO -> Director -> Manager -> Employee
        self.ceo = Employee.objects.create(
            employee_number='CEO-001',
            first_name='CEO',
            last_name='User',
            status=EmployeeStatus.ACTIVE
        )
        self.director = Employee.objects.create(
            employee_number='DIR-001',
            first_name='Director',
            last_name='User',
            reporting_manager=self.ceo,
            status=EmployeeStatus.ACTIVE
        )
        self.manager = Employee.objects.create(
            employee_number='MGR-001',
            first_name='Manager',
            last_name='User',
            reporting_manager=self.director,
            status=EmployeeStatus.ACTIVE
        )
        self.emp = Employee.objects.create(
            employee_number='EMP-001',
            first_name='Employee',
            last_name='User',
            reporting_manager=self.manager,
            status=EmployeeStatus.ACTIVE
        )

    def test_hierarchy_service_traversal(self):
        from apps.employees.hierarchy_services import OrgHierarchyService
        
        # CEO reports
        self.assertEqual(list(OrgHierarchyService.get_direct_reports(self.ceo)), [self.director])
        
        # CEO all subordinates
        subordinates = list(OrgHierarchyService.get_all_subordinates(self.ceo))
        self.assertIn(self.director, subordinates)
        self.assertIn(self.manager, subordinates)
        self.assertIn(self.emp, subordinates)
        
        # Management chain of Employee
        chain = OrgHierarchyService.get_management_chain(self.emp)
        self.assertEqual(chain, [self.manager, self.director, self.ceo])
        
        # Depths
        self.assertEqual(OrgHierarchyService.get_reporting_depth(self.ceo), 1)
        self.assertEqual(OrgHierarchyService.get_reporting_depth(self.emp), 4)
        
        # Manager checks
        self.assertTrue(OrgHierarchyService.is_manager_of(self.ceo, self.emp))
        self.assertTrue(OrgHierarchyService.is_manager_of(self.director, self.emp))
        self.assertFalse(OrgHierarchyService.is_manager_of(self.emp, self.ceo))
        
        # Scoped queryset
        scoped = list(OrgHierarchyService.get_subordinate_scoped_queryset(self.manager).values_list('id', flat=True))
        self.assertIn(self.manager.id, scoped)
        self.assertIn(self.emp.id, scoped)
        self.assertNotIn(self.director.id, scoped)

        # Analytics
        analytics = OrgHierarchyService.get_org_analytics()
        self.assertEqual(analytics['max_depth'], 4)
        self.assertEqual(analytics['avg_span_of_control'], 1.0)

    def test_get_all_subordinates_query_count_scales_with_depth_not_total_employees(self):
        from apps.employees.hierarchy_services import OrgHierarchyService
        
        # 1. Measure queries for the original chain (depth = 4)
        with self.assertNumQueries(5):
            subordinates_1 = list(OrgHierarchyService.get_all_subordinates(self.ceo))
        self.assertEqual(len(subordinates_1), 3)

        # 2. Add 20 unrelated employees to the database
        for i in range(20):
            Employee.objects.create(
                employee_number=f'UNRELATED-{i}',
                first_name='Unrelated',
                last_name=str(i),
                status=EmployeeStatus.ACTIVE
            )

        # 3. Assert query count remains exactly the same (5 queries) and is independent of total employee count
        with self.assertNumQueries(5):
            subordinates_2 = list(OrgHierarchyService.get_all_subordinates(self.ceo))
        self.assertEqual(len(subordinates_2), 3)

    def test_team_data_scope_includes_grand_reports(self):
        from apps.accounts.rbac_models import Permission as RBACPermission, Role as RBACRole, UserRoleAssignment, RolePermission, Module, Action
        from apps.accounts.models import DataScope
        from apps.accounts.engine import PermissionEngine
        
        # Setup a manager user for the director employee
        director_user = get_user_model().objects.create_user(
            phone='+8801700000201',
            password='testpassword123',
            role='manager'
        )
        self.director.user = director_user
        self.director.save()

        # Create profiles for employees in the tree
        from apps.employees.models import EmployeeProfile
        director_profile = EmployeeProfile.objects.create(
            user=director_user, full_name='Director User', employee_id='DIR-001',
            master_employee=self.director, joined_date='2026-07-01', phone='01700000201'
        )
        
        manager_user = get_user_model().objects.create_user(phone='+8801700000202', password='testpassword123', role='staff')
        self.manager.user = manager_user
        self.manager.save()
        manager_profile = EmployeeProfile.objects.create(
            user=manager_user, full_name='Manager User', employee_id='MGR-001',
            master_employee=self.manager, joined_date='2026-07-01', phone='01700000202'
        )
        
        emp_user = get_user_model().objects.create_user(phone='+8801700000203', password='testpassword123', role='staff')
        self.emp.user = emp_user
        self.emp.save()
        emp_profile = EmployeeProfile.objects.create(
            user=emp_user, full_name='Emp User', employee_id='EMP-001',
            master_employee=self.emp, joined_date='2026-07-01', phone='01700000203'
        )

        ceo_user = get_user_model().objects.create_user(phone='+8801700000204', password='testpassword123', role='staff')
        self.ceo.user = ceo_user
        self.ceo.save()
        ceo_profile = EmployeeProfile.objects.create(
            user=ceo_user, full_name='CEO User', employee_id='CEO-001',
            master_employee=self.ceo, joined_date='2026-07-01', phone='01700000204'
        )

        # Setup permission system for TEAM scope view using RBAC models
        module = Module.objects.create(name='EmployeesModule', code='employees')
        action = Action.objects.create(name='View', code='view')
        perm = RBACPermission.objects.create(
            module=module,
            action=action,
            codename='employees.view',
            name='View Employees'
        )
        role = RBACRole.objects.create(name='ManagerRole', code='manager')
        UserRoleAssignment.objects.create(user=director_user, role=role)
        RolePermission.objects.create(role=role, permission=perm, data_scope=DataScope.TEAM)

        # Create leave requests to filter
        from apps.leave.models import LeaveType, LeaveRequest
        leave_type = LeaveType.objects.create(name='Casual', default_days_per_year=10)
        from datetime import date
        req_director = LeaveRequest.objects.create(employee=director_profile, leave_type=leave_type, start_date=date(2026, 8, 1), end_date=date(2026, 8, 2), status='pending')
        req_manager = LeaveRequest.objects.create(employee=manager_profile, leave_type=leave_type, start_date=date(2026, 8, 1), end_date=date(2026, 8, 2), status='pending')
        req_emp = LeaveRequest.objects.create(employee=emp_profile, leave_type=leave_type, start_date=date(2026, 8, 1), end_date=date(2026, 8, 2), status='pending')
        req_ceo = LeaveRequest.objects.create(employee=ceo_profile, leave_type=leave_type, start_date=date(2026, 8, 1), end_date=date(2026, 8, 2), status='pending')

        # Filter queryset under TEAM scope for the director
        filtered_qs = PermissionEngine.filter_by_data_scope(
            director_user, LeaveRequest.objects.all(), 'employees.view', employee_field='employee'
        )
        
        filtered_list = list(filtered_qs)
        self.assertIn(req_director, filtered_list)
        self.assertIn(req_manager, filtered_list)
        self.assertIn(req_emp, filtered_list)
        self.assertNotIn(req_ceo, filtered_list)


class ManagerDelegationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email='admin_del@example.com', password='pass123', role='admin')
        self.manager = Employee.objects.create(
            employee_number='DEL-MGR-001',
            first_name='Manager',
            last_name='Delegator',
            status=EmployeeStatus.ACTIVE
        )
        self.delegate = Employee.objects.create(
            employee_number='DEL-EMP-001',
            first_name='Delegate',
            last_name='User',
            status=EmployeeStatus.ACTIVE
        )

    def test_delegation_validation(self):
        from apps.employees.models import ManagerDelegation
        from django.core.exceptions import ValidationError
        
        # Self-delegation error
        with self.assertRaises(ValidationError):
            ManagerDelegation.objects.create(
                manager=self.manager,
                delegate_to=self.manager,
                start_date=date.today(),
                end_date=date.today()
            )
            
        # Date order error
        with self.assertRaises(ValidationError):
            ManagerDelegation.objects.create(
                manager=self.manager,
                delegate_to=self.delegate,
                start_date=date(2026, 7, 26),
                end_date=date(2026, 7, 25)
            )

    def test_delegation_views(self):
        self.client.force_login(self.admin)
        
        # Create via view
        url_create = reverse('employees:delegation_create')
        data = {
            'manager': self.manager.id,
            'delegate_to': self.delegate.id,
            'start_date': '2026-07-25',
            'end_date': '2026-07-30',
            'reason': 'Vacation'
        }
        res = self.client.post(url_create, data=data)
        self.assertEqual(res.status_code, 302)
        
        from apps.employees.models import ManagerDelegation
        delg = ManagerDelegation.objects.get(manager=self.manager)
        self.assertEqual(delg.delegate_to, self.delegate)
        self.assertEqual(delg.reason, 'Vacation')
        self.assertTrue(delg.is_active)
        
        # List view
        url_list = reverse('employees:delegation_list')
        res_list = self.client.get(url_list)
        self.assertEqual(res_list.status_code, 200)
        self.assertContains(res_list, 'Vacation')
        
        # End delegation via view
        url_end = reverse('employees:delegation_end', kwargs={'pk': delg.pk})
        res_end = self.client.post(url_end)
        self.assertEqual(res_end.status_code, 302)
        
        delg.refresh_from_db()
        self.assertFalse(delg.is_active)


class SubordinateAPITests(TestCase):
    def setUp(self):
        from apps.branches.models import Branch
        from apps.employees.models import Department, Designation, Employee, EmployeeStatus, EmployeeProfile
        from apps.accounts.rbac_models import Permission as RBACPermission, Role as RBACRole, UserRoleAssignment, RolePermission, Module, Action
        from apps.accounts.models import DataScope

        self.branch1 = Branch.objects.create(name='Branch 1', latitude=23.8, longitude=90.4, radius_meters=100)
        self.branch2 = Branch.objects.create(name='Branch 2', latitude=23.9, longitude=90.5, radius_meters=100)
        
        self.dept = Department.objects.create(name='Engineering', code='ENG')
        self.desig = Designation.objects.create(name='Developer', code='DEV')

        self.admin_user = User.objects.create_superuser(email='api_admin@test.com', password='password123', role='admin')
        self.manager_user = User.objects.create_user(phone='+8801733333331', password='password123', role='manager')
        self.staff_user = User.objects.create_user(phone='+8801733333332', password='password123', role='staff')
        self.unrelated_user = User.objects.create_user(phone='+8801733333333', password='password123', role='staff')

        self.manager_master = Employee.objects.create(
            employee_number='API-MGR-001', first_name='API', last_name='Manager', status=EmployeeStatus.ACTIVE,
            branch=self.branch1, department=self.dept, designation=self.desig, user=self.manager_user
        )
        self.staff_master = Employee.objects.create(
            employee_number='API-STF-001', first_name='API', last_name='Staff', status=EmployeeStatus.ACTIVE,
            branch=self.branch1, department=self.dept, designation=self.desig, user=self.staff_user,
            reporting_manager=self.manager_master
        )
        self.unrelated_master = Employee.objects.create(
            employee_number='API-STF-002', first_name='API', last_name='Unrelated', status=EmployeeStatus.ACTIVE,
            branch=self.branch2, department=self.dept, designation=self.desig, user=self.unrelated_user
        )

        EmployeeProfile.objects.create(user=self.manager_user, full_name='API Manager', employee_id='API-MGR-001', master_employee=self.manager_master, joined_date='2026-07-01', phone='01733333331')
        EmployeeProfile.objects.create(user=self.staff_user, full_name='API Staff', employee_id='API-STF-001', master_employee=self.staff_master, joined_date='2026-07-01', phone='01733333332')
        EmployeeProfile.objects.create(user=self.unrelated_user, full_name='API Unrelated', employee_id='API-STF-002', master_employee=self.unrelated_master, joined_date='2026-07-01', phone='01733333333')

        # Setup RBAC permissions for manager
        module = Module.objects.create(name='EmployeesModule', code='employees')
        action = Action.objects.create(name='View', code='view')
        perm = RBACPermission.objects.create(module=module, action=action, codename='employees.view', name='View Employees')
        
        self.role_manager = RBACRole.objects.create(name='ManagerRole', code='manager')
        UserRoleAssignment.objects.create(user=self.manager_user, role=self.role_manager)
        RolePermission.objects.create(role=self.role_manager, permission=perm, data_scope=DataScope.TEAM)

        self.role_staff = RBACRole.objects.create(name='StaffRole', code='staff')
        UserRoleAssignment.objects.create(user=self.staff_user, role=self.role_staff)
        RolePermission.objects.create(role=self.role_staff, permission=perm, data_scope=DataScope.TEAM)

    def test_direct_reports_api(self):
        self.client.force_login(self.manager_user)
        url = reverse('employees:api_direct_reports', kwargs={'pk': self.manager_master.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['results'][0]['employee_number'], 'API-STF-001')

    def test_subordinates_api_recursive(self):
        self.client.force_login(self.manager_user)
        # Create a grand-report reporting to staff_master
        from apps.employees.models import Employee, EmployeeStatus, EmployeeProfile
        grand_user = User.objects.create_user(phone='+8801733333334', password='password123', role='staff')
        grand_master = Employee.objects.create(
            employee_number='API-STF-003', first_name='API', last_name='Grand', status=EmployeeStatus.ACTIVE,
            branch=self.branch1, department=self.dept, designation=self.desig, user=grand_user,
            reporting_manager=self.staff_master
        )
        EmployeeProfile.objects.create(user=grand_user, full_name='API Grand', employee_id='API-STF-003', master_employee=grand_master, joined_date='2026-07-01', phone='01733333334')

        url = reverse('employees:api_subordinates', kwargs={'pk': self.manager_master.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['count'], 2)
        numbers = [x['employee_number'] for x in data['results']]
        self.assertIn('API-STF-001', numbers)
        self.assertIn('API-STF-003', numbers)

    def test_org_chain_api(self):
        self.client.force_login(self.staff_user)
        url = reverse('employees:api_org_chain', kwargs={'pk': self.staff_master.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['results'][0]['employee_number'], 'API-MGR-001')

    def test_org_analytics_api(self):
        self.client.force_login(self.admin_user)
        url = reverse('employees:api_org_analytics')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('max_depth', data)

    def test_is_manager_api(self):
        self.client.force_login(self.manager_user)
        url = reverse('employees:api_is_manager', kwargs={'pk': self.manager_master.pk, 'target_pk': self.staff_master.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['is_manager'])

    def test_api_permission_denied_data_scope(self):
        # staff_user cannot view unrelated_master because unrelated_master is not in their team
        self.client.force_login(self.staff_user)
        url = reverse('employees:api_direct_reports', kwargs={'pk': self.unrelated_master.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

class HRMasterReadinessTests(TestCase):
    def setUp(self):
        from apps.branches.models import Branch
        from apps.employees.models import Employee, EmployeeProfile, Department, Designation, EmployeeStatus
        from django.contrib.auth import get_user_model
        User = get_user_model()

        self.branch1 = Branch.objects.create(name='SSOT Branch 1', address='Addr 1', latitude=23.0, longitude=90.0, is_active=True)
        self.branch2 = Branch.objects.create(name='SSOT Branch 2', address='Addr 2', latitude=23.0, longitude=90.0, is_active=True)
        self.dept = Department.objects.create(name='SSOT Engineering', code='ENG-SSOT')
        self.desig = Designation.objects.create(name='Senior SSOT Engineer')

        self.user = User.objects.create_user(phone='+8801755556666', password='pass', role='staff')
        
        self.legacy_profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id='LEGACY-001',
            full_name='Legacy Alice',
            phone='+8801755556666',
            joined_date='2026-01-01',
            branch=self.branch1,
            department='Legacy Eng',
            designation='Legacy Dev',
            is_active=True
        )

    def test_legacy_profile_fields(self):
        self.assertEqual(self.legacy_profile.canonical_full_name, 'Legacy Alice')
        self.assertEqual(self.legacy_profile.canonical_phone, '+8801755556666')
        self.assertEqual(self.legacy_profile.canonical_branch, self.branch1)
        self.assertEqual(self.legacy_profile.canonical_department, 'Legacy Eng')
        self.assertEqual(self.legacy_profile.canonical_designation, 'Legacy Dev')
        self.assertTrue(self.legacy_profile.canonical_is_active)

    def test_linked_employee_delegation(self):
        from apps.employees.models import Employee, EmployeeStatus
        from apps.employees.hr_resolver import get_canonical_employee

        master = Employee.objects.create(
            employee_number='CANON-001',
            first_name='Canonical',
            last_name='Bob',
            phone='+8801799999999',
            joined_date='2026-02-02',
            branch=self.branch2,
            department=self.dept,
            designation=self.desig,
            status='active',
            is_suspended=False
        )
        self.legacy_profile.master_employee = master
        self.legacy_profile.save()

        self.legacy_profile.refresh_from_db()

        self.assertEqual(self.legacy_profile.canonical_full_name, 'Canonical Bob')
        self.assertEqual(self.legacy_profile.canonical_phone, '+8801799999999')
        self.assertEqual(self.legacy_profile.canonical_branch, self.branch2)
        self.assertEqual(self.legacy_profile.canonical_department, 'SSOT Engineering')
        self.assertEqual(self.legacy_profile.canonical_designation, 'Senior SSOT Engineer')
        self.assertTrue(self.legacy_profile.canonical_is_active)

        resolved = get_canonical_employee(self.user)
        self.assertEqual(resolved, master)
        self.assertEqual(resolved.canonical_full_name, 'Canonical Bob')

        master.is_suspended = True
        master.save()
        self.legacy_profile.refresh_from_db()
        self.assertFalse(self.legacy_profile.canonical_is_active)


class HRHardeningRegressionTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from apps.branches.models import Branch
        User = get_user_model()
        self.branch = Branch.objects.create(name='Test Branch', latitude=23.0, longitude=90.0)
        self.user = User.objects.create_user(email='test_hard@test.com', phone='+8801999999991', password='Password123!')

    def test_asset_assignment_clean_validation(self):
        from apps.employees.models import Employee, Asset, AssetAssignment, AssetType, AssetCondition, EmployeeStatus
        from django.core.exceptions import ValidationError
        
        emp1 = Employee.objects.create(
            employee_number='EMP-H-01', first_name='Test', last_name='One', branch=self.branch, status=EmployeeStatus.ACTIVE
        )
        emp2 = Employee.objects.create(
            employee_number='EMP-H-02', first_name='Test', last_name='Two', branch=self.branch, status=EmployeeStatus.ACTIVE
        )
        
        asset = Asset.objects.create(
            asset_type=AssetType.LAPTOP, asset_tag='TAG-H-01', name='H Laptop', condition=AssetCondition.GOOD
        )
        
        # First assignment
        AssetAssignment.objects.create(asset=asset, employee=emp1, assigned_date=timezone.localdate())
        
        # Second active assignment should fail clean/save
        assign2 = AssetAssignment(asset=asset, employee=emp2, assigned_date=timezone.localdate())
        with self.assertRaises(ValidationError):
            assign2.full_clean()

    def test_form_auto_creates_profile(self):
        from apps.employees.models import Employee, EmployeeProfile, EmployeeStatus
        from apps.employees.forms import EmployeeMasterForm
        
        master = Employee.objects.create(
            employee_number='EMP-H-03',
            first_name='Auto',
            last_name='Profile',
            phone='+8801999999992',
            branch=self.branch,
            status=EmployeeStatus.ACTIVE
        )
        
        self.assertFalse(EmployeeProfile.objects.filter(master_employee=master).exists())
        
        # Save via form which simulates the admin panel edit flow
        data = {
            'employee_number': master.employee_number,
            'first_name': master.first_name,
            'last_name': master.last_name,
            'branch': self.branch.pk,
            'status': master.status,
            'user': self.user.pk,
            'phone': '+8801999999992',
        }
        form = EmployeeMasterForm(data=data, instance=master)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        
        self.assertTrue(EmployeeProfile.objects.filter(master_employee=master).exists())
        prof = EmployeeProfile.objects.get(master_employee=master)
        self.assertEqual(prof.user, self.user)
        self.assertEqual(prof.full_name, 'Auto Profile')



from datetime import timedelta
class HRHardeningTests(TestCase):
    def setUp(self):
        from apps.branches.models import Branch
        from apps.employees.models import Department, Designation, Employee
        from django.contrib.auth import get_user_model
        User = get_user_model()

        self.branch = Branch.objects.create(name='Test Branch', latitude=23.0, longitude=90.0, is_active=True)
        self.dept = Department.objects.create(name='Engineering', code='ENG')
        self.desig = Designation.objects.create(name='Engineer', code='ENG_DES')
        self.admin_user = User.objects.create_superuser(email='admin@example.com', phone='+8801700000000', password='password123')

    def test_new_employee_circular_manager_prevention(self):
        from apps.employees.models import Employee
        # Create an employee
        emp1 = Employee.objects.create(
            employee_number='EMP-001', first_name='Emp', last_name='One',
            branch=self.branch, department=self.dept, designation=self.desig,
            status='active'
        )
        # Create another employee reporting to emp1
        emp2 = Employee.objects.create(
            employee_number='EMP-002', first_name='Emp', last_name='Two',
            branch=self.branch, department=self.dept, designation=self.desig,
            status='active', reporting_manager=emp1
        )
        # Now try to create a new employee reporting to emp2
        emp3 = Employee(
            employee_number='EMP-003', first_name='Emp', last_name='Three',
            branch=self.branch, department=self.dept, designation=self.desig,
            status='active', reporting_manager=emp2
        )
        emp3.save()
        self.assertIsNotNone(emp3.pk)

    def test_lifecycle_transition_profile_sync(self):
        from apps.employees.models import Employee, EmployeeProfile
        from apps.employees.views import _apply_transition
        from django.contrib.auth import get_user_model
        User = get_user_model()
        
        user = User.objects.create_user(phone='+8801700000004', password='password', role='staff')
        master = Employee.objects.create(
            employee_number='EMP-004', first_name='John', last_name='Doe',
            branch=self.branch, department=self.dept, designation=self.desig,
            status='active', user=user
        )
        profile = EmployeeProfile.objects.create(
            user=user, employee_id='EMP-004', full_name='John Doe',
            joined_date='2026-01-01', phone='+8801700000004', master_employee=master,
            is_active=True
        )

        class FakeReq:
            to_status = 'suspended'
            effective_date = timezone.now().date()
            reason = 'Temporary suspension for audit'
            new_department = None
            new_designation = None

        _apply_transition(master, FakeReq(), self.admin_user)
        
        master.refresh_from_db()
        profile.refresh_from_db()
        
        self.assertEqual(master.status, 'suspended')
        self.assertTrue(master.is_suspended)
        self.assertFalse(profile.is_active)

    def test_document_expiry_active_queries(self):
        from apps.employees.models import Employee, EmployeeDocument, DocumentType
        master = Employee.objects.create(
            employee_number='EMP-005', first_name='Jane', last_name='Doe',
            branch=self.branch, department=self.dept, designation=self.desig,
            status='active'
        )
        # Active, not expired document
        doc1 = EmployeeDocument.objects.create(
            employee_master=master, document_type=DocumentType.NID,
            title='NID', expiry_date=timezone.localdate() + timedelta(days=10),
            is_active=True, is_archived=False
        )
        # Expired document
        doc2 = EmployeeDocument.objects.create(
            employee_master=master, document_type=DocumentType.PASSPORT,
            title='Passport', expiry_date=timezone.localdate() - timedelta(days=1),
            is_active=True, is_archived=False
        )
        
        self.assertEqual(master.get_completion_percentage(), 40)


class EmployeeLifecycleTests(TestCase):
    def setUp(self):
        from apps.branches.models import Branch
        from apps.employees.models import Department, Designation
        self.branch = Branch.objects.create(name='Test Branch', latitude=23.8103, longitude=90.4125, radius_meters=100)
        self.dept = Department.objects.create(name='Engineering', code='ENG')
        self.desig = Designation.objects.create(name='Engineer', code='ENG_DES')
        self.admin = User.objects.create_superuser(email='lifecycle_admin@example.com', phone='+8801700000001', password='password123', role='admin')
        self.staff_user = User.objects.create_user(email='lifecycle_staff@example.com', phone='+8801700000002', password='password123', role='staff')
        
        from apps.employees.models import Employee
        self.employee = Employee.objects.create(
            employee_number='EMP-LIFE-001',
            first_name='Lifecycle',
            last_name='User',
            status='active',
            user=self.staff_user,
            branch=self.branch,
            department=self.dept,
            designation=self.desig
        )

    def test_active_to_inactive_with_reason(self):
        from apps.employees.views import _apply_transition
        class FakeReq:
            to_status = 'inactive'
            effective_date = timezone.now().date()
            reason = 'Temporary inactive reason'
        
        _apply_transition(self.employee, FakeReq(), self.admin)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.status, 'inactive')
        self.assertFalse(self.employee.is_login_allowed())

    def test_active_to_inactive_requires_reason(self):
        from apps.employees.views import _apply_transition
        class FakeReq:
            to_status = 'inactive'
            effective_date = timezone.now().date()
            reason = ''
        
        with self.assertRaises(ValidationError):
            _apply_transition(self.employee, FakeReq(), self.admin)

    def test_inactive_to_active(self):
        from apps.employees.views import _apply_transition
        self.employee.status = 'inactive'
        self.employee.save()
        
        class FakeReq:
            to_status = 'active'
            effective_date = timezone.now().date()
            reason = 'Reactivating employee'
            
        _apply_transition(self.employee, FakeReq(), self.admin)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.status, 'active')
        self.assertTrue(self.employee.is_login_allowed())

    def test_active_to_suspended(self):
        from apps.employees.views import _apply_transition
        class FakeReq:
            to_status = 'suspended'
            effective_date = timezone.now().date()
            reason = 'Investigation'
            suspension_start_date = timezone.now().date()
            suspension_end_date = timezone.now().date() + timedelta(days=5)
            auto_reactivate = True
            
        _apply_transition(self.employee, FakeReq(), self.admin)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.status, 'suspended')
        self.assertTrue(self.employee.is_suspended)
        self.assertFalse(self.employee.is_login_allowed())

        # Check suspension record created
        susp = self.employee.suspensions.filter(is_active=True).first()
        self.assertIsNotNone(susp)
        self.assertEqual(susp.suspension_reason, 'Investigation')
        self.assertTrue(susp.auto_reactivate)

    def test_invalid_suspension_dates(self):
        from apps.employees.forms import LifecycleActionForm
        form_data = {
            'to_status': 'suspended',
            'reason': 'Testing date range validation',
            'effective_date': timezone.now().date(),
            'suspension_start_date': timezone.now().date(),
            'suspension_end_date': timezone.now().date() - timedelta(days=2),
            'auto_reactivate': True
        }
        form = LifecycleActionForm(data=form_data, to_status='suspended')
        self.assertFalse(form.is_valid())
        self.assertIn('__all__', form.errors)

    def test_suspension_expiry_auto_reactivation(self):
        from apps.employees.models import EmployeeSuspension
        from django.core.management import call_command
        
        # Suspend the employee with expiry yesterday
        self.employee.status = 'suspended'
        self.employee.is_suspended = True
        self.employee.save()
        
        EmployeeSuspension.objects.create(
            employee=self.employee,
            suspension_start_date=timezone.now().date() - timedelta(days=5),
            suspension_end_date=timezone.now().date() - timedelta(days=1),
            suspension_reason='Past expiry suspension',
            auto_reactivate=True,
            is_active=True,
            previous_status='active'
        )

        call_command('reactivate_suspended')
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.status, 'active')
        self.assertFalse(self.employee.is_suspended)

    def test_manual_reactivation_clears_suspension(self):
        from apps.employees.models import EmployeeSuspension
        from apps.employees.views import _apply_transition
        
        self.employee.status = 'suspended'
        self.employee.is_suspended = True
        self.employee.save()
        
        susp = EmployeeSuspension.objects.create(
            employee=self.employee,
            suspension_start_date=timezone.now().date(),
            suspension_reason='Testing manual clear',
            is_active=True,
            previous_status='active'
        )

        class FakeReq:
            to_status = 'active'
            effective_date = timezone.now().date()
            reason = 'Manual lift of suspension'

        _apply_transition(self.employee, FakeReq(), self.admin)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.status, 'active')
        self.assertFalse(self.employee.is_suspended)
        
        susp.refresh_from_db()
        self.assertFalse(susp.is_active)

    def test_archived_employee_rules(self):
        from apps.employees.views import _apply_transition
        class FakeReq:
            to_status = 'archived'
            effective_date = timezone.now().date()
            reason = 'Archiving employee records'

        _apply_transition(self.employee, FakeReq(), self.admin)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.status, 'archived')
        self.assertFalse(self.employee.is_login_allowed())

    def test_hard_delete_restrictions(self):
        from apps.audit.services import TrashService
        self.client.defaults['HTTP_X_FORWARDED_PROTO'] = 'https'
        non_super_admin = User.objects.create_user(email='nonsuper_admin@example.com', phone='+8801700000003', password='password123', role='admin')
        TrashService.soft_delete(self.employee, actor=self.admin, reason='Soft delete for test')
        self.client.force_login(non_super_admin)
        url = reverse('employees:master_delete', kwargs={'pk': self.employee.pk})
        response = self.client.post(url, secure=True)
        self.assertIn(response.status_code, [302, 403])


    def test_delete_lifecycle_ui_and_rules(self):
        from apps.audit.models import TrashEntry, AuditEvent
        from apps.attendance.models import Attendance
        from django.utils import timezone
        
        self.client.force_login(self.admin)
        url = reverse('employees:master_delete', kwargs={'pk': self.employee.pk})

        # 1. Employee Delete button / GET opens confirmation modal
        response = self.client.get(url, HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'employee-confirm-modal')
        self.assertContains(response, 'Move to Trash')

        # 3. GET cannot delete (verify still active/not trashed)
        self.employee.refresh_from_db()
        self.assertFalse(self.employee.is_trashed)

        # 4. POST without required confirmation/reason rejected (returns modal with error)
        response = self.client.post(url, data={'reason': ''}, HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Reason is required')
        self.employee.refresh_from_db()
        self.assertFalse(self.employee.is_trashed)

        # 5. Confirmed delete sets is_trashed=True, 6. TrashEntry created once, 7. AuditEvent created
        response = self.client.post(url, data={'reason': 'Employee resigned'}, HTTP_HX_REQUEST='true')
        self.assertIn(response.status_code, [200, 204])
        self.employee.refresh_from_db()
        self.assertTrue(self.employee.is_trashed)
        self.assertEqual(self.employee.status, 'archived')

        self.assertEqual(TrashEntry.objects.filter(content_object_id=self.employee.pk, status='active').count(), 1)

        self.assertTrue(AuditEvent.objects.filter(object_id=str(self.employee.pk), action='deleted').exists())

        # 8. Duplicate POST is idempotent
        response = self.client.post(url, data={'reason': 'Employee resigned again'}, HTTP_HX_REQUEST='true')
        self.assertIn(response.status_code, [200, 204])
        self.assertEqual(TrashEntry.objects.filter(content_object_id=self.employee.pk, status='active').count(), 1)



        # 9. Employee disappears from normal Employee list
        list_url = reverse('employees:master_list')
        response = self.client.get(list_url)
        self.assertNotContains(response, self.employee.employee_number)

        # 10. Employee appears in Trash
        trash_list_url = reverse('audit:trash_list')
        response = self.client.get(trash_list_url)
        self.assertContains(response, self.employee.get_full_name())


        # 12. Trashed employee cannot check in
        from apps.attendance.transaction_service import AttendanceTransactionService, AttendanceTransactionError
        with self.assertRaises(AttendanceTransactionError):
            AttendanceTransactionService.check_in(self.staff_user, {'sync_uuid': 'test-uuid-sync-1', 'client_event_time': timezone.now().isoformat()})


        # 18. Restore works
        restore_url = reverse('audit:trash_restore', kwargs={'pk': TrashEntry.objects.get(content_object_id=self.employee.pk).pk})
        response = self.client.post(restore_url)
        self.employee.refresh_from_db()
        self.assertFalse(self.employee.is_trashed)
        self.assertEqual(self.employee.status, 'active')
        self.assertTrue(AuditEvent.objects.filter(object_id=str(self.employee.pk), action='restored').exists())

        # 20. Suspend requires confirmation + reason
        suspend_modal_url = reverse('employees:employee_suspend_toggle_modal', kwargs={'pk': self.employee.pk})
        response = self.client.get(suspend_modal_url)
        self.assertContains(response, 'employee-confirm-modal')
        self.assertContains(response, 'Suspend Profile')

        suspend_url = reverse('employees:employee_suspend_toggle', kwargs={'pk': self.employee.pk})
        response = self.client.post(suspend_url, data={'reason': ''}, HTTP_HX_REQUEST='true')
        self.assertContains(response, 'Reason is required')
        self.employee.refresh_from_db()
        self.assertFalse(self.employee.is_suspended)

        # 21. Confirmed suspend sets is_suspended=True and requires reason
        response = self.client.post(suspend_url, data={'reason': 'Temporary suspend', 'suspension_start_date': timezone.localdate().isoformat()}, HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 200)
        self.employee.refresh_from_db()
        self.assertTrue(self.employee.is_suspended)
        self.assertTrue(AuditEvent.objects.filter(object_id=str(self.employee.pk), action='suspended').exists())


class DepartmentCRUDTests(TestCase):
    def setUp(self):
        from apps.branches.models import Branch
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.branch = Branch.objects.create(name='CRUD Branch', latitude=23.8, longitude=90.4, radius_meters=100)
        self.admin = User.objects.create_superuser(email='crud_admin@example.com', phone='+8801700000003', password='password123', role='admin')
        
    def test_department_create_and_edit_drawer(self):
        self.client.force_login(self.admin)
        create_url = reverse('employees:department_create')
        
        # Get create form drawer
        response = self.client.get(create_url, HTTP_HX_REQUEST='true')
        self.assertContains(response, 'dept-drawer')
        self.assertContains(response, 'New Department')
        
        # Post create
        response = self.client.post(create_url, data={
            'name': 'Test Dev',
            'code': 'TDEV',
            'description': 'Test dev department',
            'is_global': 'on',
            'is_active': 'on'
        }, HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 204)
        
        dept = Department.objects.get(name='Test Dev')
        self.assertTrue(dept.is_global)
        self.assertTrue(dept.is_active)
        
        # Get edit form drawer
        edit_url = reverse('employees:department_edit', kwargs={'pk': dept.pk})
        response = self.client.get(edit_url, HTTP_HX_REQUEST='true')
        self.assertContains(response, 'dept-drawer')
        self.assertContains(response, 'Edit Department')
        
        # Post edit
        response = self.client.post(edit_url, data={
            'name': 'Test Dev Updated',
            'code': 'TDEV2',
            'description': 'Test dev updated',
            'is_global': '',
            'branches': [self.branch.pk],
            'is_active': 'on'
        }, HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 204)
        
        dept.refresh_from_db()
        self.assertEqual(dept.name, 'Test Dev Updated')
        self.assertFalse(dept.is_global)
        self.assertIn(self.branch, dept.branches.all())

    def test_department_delete(self):
        self.client.force_login(self.admin)
        dept = Department.objects.create(name='Delete Dept', code='DEL')
        delete_url = reverse('employees:department_delete', kwargs={'pk': dept.pk})
        
        response = self.client.post(delete_url, HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Department.objects.filter(pk=dept.pk).exists())

    def test_department_export_and_import(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.client.force_login(self.admin)
        dept1 = Department.objects.create(name='Export 1', code='EX1', is_global=True)
        dept2 = Department.objects.create(name='Export 2', code='EX2', is_global=False)
        dept2.branches.add(self.branch)
        
        # Test Export
        export_url = reverse('employees:department_export_csv')
        response = self.client.get(export_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')
        
        content = response.content.decode('utf-8')
        self.assertIn('Export 1', content)
        self.assertIn('Export 2', content)
        
        # Test Import
        import_url = reverse('employees:department_import_csv')
        
        import_csv_content = (
            "Name,Code,Description,Is Global,Branches,Is Active\n"
            "Imported Dept 1,IMP1,Description 1,True,All,True\n"
            f"Imported Dept 2,IMP2,Description 2,False,{self.branch.name},True\n"
        )
        
        import_file = SimpleUploadedFile("imported_depts.csv", import_csv_content.encode('utf-8'), content_type="text/csv")
        response = self.client.post(import_url, {'file': import_file})
        self.assertEqual(response.status_code, 302)
        
        self.assertTrue(Department.objects.filter(name='Imported Dept 1').exists())
        imp_dept2 = Department.objects.get(name='Imported Dept 2')
        self.assertFalse(imp_dept2.is_global)
        self.assertIn(self.branch, imp_dept2.branches.all())


class EmployeeMoveToTrashWorkflowTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            phone='+8801711111111',
            email='admin@fieldtrack.local',
            password='Password123!',
            role='admin'
        )
        self.staff_user = User.objects.create_user(
            phone='+8801722222222',
            email='staff@fieldtrack.local',
            password='Password123!',
            role='staff'
        )
        self.branch = Branch.objects.create(name="HQ Branch", latitude=23.81, longitude=90.41, radius_meters=200)
        self.dept = Department.objects.create(name="Operations", code="OPS")
        self.desig = Designation.objects.create(name="Field Officer", code="FO")

        self.emp_user = User.objects.create_user(
            phone='+8801733333333',
            email='target@fieldtrack.local',
            password='Password123!',
            role='employee'
        )
        self.employee = Employee.objects.create(
            user=self.emp_user,
            first_name="Rahim",
            last_name="Uddin",
            employee_number="EMP-TEST-001",
            personal_email="target@fieldtrack.local",
            phone="+8801733333333",
            branch=self.branch,
            department=self.dept,
            designation=self.desig,
            status=EmployeeStatus.ACTIVE,
            is_trashed=False
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.emp_user,
            master_employee=self.employee,
            employee_id="EMP-TEST-001",
            full_name="Rahim Uddin",
            phone="+8801733333333",
            branch=self.branch,
            joined_date=date.today(),
            is_active=True
        )

        from apps.attendance.models import Attendance
        self.attendance = Attendance.objects.create(
            employee=self.profile,
            date=date.today(),
            status='on_time',
            type='office'
        )

    def test_rbac_unauthorized_user_forbidden(self):
        self.client.force_login(self.staff_user)
        url = reverse('employees:master_delete', kwargs={'pk': self.employee.pk})

        get_res = self.client.get(url)
        self.assertEqual(get_res.status_code, 403)

        post_res = self.client.post(url, {'reason': 'Should not work'})
        self.assertEqual(post_res.status_code, 403)

    def test_missing_reason_validation(self):
        from apps.audit.models import TrashEntry
        self.client.force_login(self.admin_user)
        url = reverse('employees:master_delete', kwargs={'pk': self.employee.pk})

        res = self.client.post(url, {'reason': ''}, HTTP_HX_REQUEST='true')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Reason is required")

        self.employee.refresh_from_db()
        self.assertFalse(self.employee.is_trashed)
        self.assertEqual(TrashEntry.objects.filter(object_id=str(self.employee.pk)).count(), 0)

    def test_persisted_database_state_on_soft_delete(self):
        from apps.audit.models import TrashEntry, AuditEvent
        self.client.force_login(self.admin_user)
        url = reverse('employees:master_delete', kwargs={'pk': self.employee.pk})

        res = self.client.post(url, {'reason': 'Relocating to another city'}, HTTP_HX_REQUEST='true')
        self.assertEqual(res.status_code, 200)
        self.assertIn("employee-row-", res.content.decode('utf-8'))
        self.assertIn('hx-swap-oob="delete"', res.content.decode('utf-8'))

        self.employee.refresh_from_db()
        self.profile.refresh_from_db()

        self.assertTrue(self.employee.is_trashed)
        self.assertEqual(self.employee.status, 'archived')
        self.assertIsNotNone(self.employee.trashed_at)
        self.assertFalse(self.profile.is_active)

        entry = TrashEntry.objects.get(object_id=str(self.employee.pk), status=TrashEntry.STATUS_ACTIVE)
        self.assertEqual(entry.delete_reason, 'Relocating to another city')
        self.assertEqual(entry.metadata.get('profile_is_active'), True)

        event = AuditEvent.objects.filter(action='deleted', object_id=str(self.employee.pk)).first()
        self.assertIsNotNone(event)

    def test_exclusion_from_active_querysets_and_pickers(self):
        from apps.audit.services import TrashService
        self.client.force_login(self.admin_user)
        TrashService.soft_delete(self.employee, actor=self.admin_user, reason='Exclusion test')

        master_list_url = reverse('employees:master_list')
        res = self.client.get(master_list_url)
        self.assertNotContains(res, self.employee.employee_number)

        legacy_list_url = reverse('employees:employee_list')
        res_legacy = self.client.get(legacy_list_url)
        self.assertNotContains(res_legacy, self.employee.employee_number)

        self.assertNotIn(self.employee, Employee.objects.active_operational())
        self.assertNotIn(self.profile, EmployeeProfile.objects.active_operational())
        self.assertNotIn(self.profile, EmployeeProfile.objects.filter(is_active=True))

    def test_historical_preservation(self):
        from apps.audit.services import TrashService
        TrashService.soft_delete(self.employee, actor=self.admin_user, reason='Preserve test')

        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.employee_id, self.profile.pk)

    def test_restoration_preserves_exact_profile_is_active(self):
        from apps.audit.services import TrashService
        # Case A: Profile was originally inactive
        self.profile.is_active = False
        self.profile.save()

        entry, created = TrashService.soft_delete(self.employee, actor=self.admin_user, reason='Inactive test')
        self.assertEqual(entry.metadata.get('profile_is_active'), False)

        TrashService.restore(entry, actor=self.admin_user)
        self.employee.refresh_from_db()
        self.profile.refresh_from_db()

        self.assertFalse(self.employee.is_trashed)
        self.assertEqual(self.employee.status, EmployeeStatus.ACTIVE)
        # MUST remain False because previous was False!
        self.assertFalse(self.profile.is_active)

        # Case B: Profile was originally active
        self.profile.is_active = True
        self.profile.save()

        entry2, created2 = TrashService.soft_delete(self.employee, actor=self.admin_user, reason='Active test')
        self.assertEqual(entry2.metadata.get('profile_is_active'), True)

        TrashService.restore(entry2, actor=self.admin_user)
        self.employee.refresh_from_db()
        self.profile.refresh_from_db()

        self.assertFalse(self.employee.is_trashed)
        self.assertTrue(self.profile.is_active)

    def test_inconsistency_repair_without_duplicate_entry_or_event(self):
        from apps.audit.models import TrashEntry, AuditEvent
        from apps.audit.services import TrashService
        from django.contrib.contenttypes.models import ContentType

        entry = TrashEntry.objects.create(
            module=self.employee._meta.app_label,
            object_type=self.employee.__class__.__name__,
            object_id=str(self.employee.pk),
            object_label=self.employee.get_full_name(),
            content_type=ContentType.objects.get_for_model(self.employee.__class__),
            content_object_id=self.employee.pk,
            status=TrashEntry.STATUS_ACTIVE,
            metadata={'previous_status': self.employee.status}
        )
        self.assertFalse(self.employee.is_trashed)
        self.assertTrue(self.profile.is_active)

        initial_entries = TrashEntry.objects.filter(object_id=str(self.employee.pk)).count()
        initial_events = AuditEvent.objects.filter(action='deleted', object_id=str(self.employee.pk)).count()

        ret_entry, created = TrashService.soft_delete(self.employee, actor=self.admin_user, reason='Repair')
        self.assertFalse(created)
        self.assertEqual(ret_entry.pk, entry.pk)

        self.employee.refresh_from_db()
        self.profile.refresh_from_db()

        self.assertTrue(self.employee.is_trashed)
        self.assertEqual(self.employee.status, 'archived')
        self.assertFalse(self.profile.is_active)

        self.assertEqual(TrashEntry.objects.filter(object_id=str(self.employee.pk)).count(), initial_entries)
        self.assertEqual(AuditEvent.objects.filter(action='deleted', object_id=str(self.employee.pk)).count(), initial_events)

    def test_duplicate_post_idempotent(self):
        from apps.audit.models import TrashEntry
        self.client.force_login(self.admin_user)
        url = reverse('employees:master_delete', kwargs={'pk': self.employee.pk})

        res1 = self.client.post(url, {'reason': 'First delete'}, HTTP_HX_REQUEST='true')
        self.assertEqual(res1.status_code, 200)

        res2 = self.client.post(url, {'reason': 'Duplicate delete'}, HTTP_HX_REQUEST='true')
        self.assertEqual(res2.status_code, 200)

        self.assertEqual(TrashEntry.objects.filter(object_id=str(self.employee.pk)).count(), 1)
