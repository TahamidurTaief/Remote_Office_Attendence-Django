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
            'document_type': 'Trade License',
            'expiry_date': '2026-12-31',
        }
        response = self.client.post(url_add, data=post_data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(EmployeeDocument.objects.filter(document_type='Trade License').exists())

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
        self.assertEqual(reloaded.status, EmployeeStatus.ARCHIVED)
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
        response = self.client.post(reverse('accounts:login'), {
            'email': 'emplogin@test.com',
            'password': 'UserPassword123!'
        })
        self.assertEqual(response.status_code, 200)
        msgs1 = [m.message for m in get_messages(response.wsgi_request)]
        self.assertTrue(any('deactivated or suspended' in m for m in msgs1))

        # 2. Active status -> login succeeds
        emp.status = EmployeeStatus.ACTIVE
        emp.save()
        self.assertTrue(emp.is_login_allowed())
        response = self.client.post(reverse('accounts:login'), {
            'email': 'emplogin@test.com',
            'password': 'UserPassword123!'
        })
        self.assertEqual(response.status_code, 302)

        # Logout
        self.client.logout()

        # 3. Archived status -> login blocked
        emp.delete()  # soft delete -> archived
        self.assertEqual(emp.status, EmployeeStatus.ARCHIVED)
        response = self.client.post(reverse('accounts:login'), {
            'email': 'emplogin@test.com',
            'password': 'UserPassword123!'
        })
        self.assertEqual(response.status_code, 200)
        msgs3 = [m.message for m in get_messages(response.wsgi_request)]
        self.assertTrue(any('deactivated or suspended' in m for m in msgs3))

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
            'status': 'active',
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
            'status': 'confirmed',
            'branch': self.branch.id,
            'department': self.dept.id,
            'designation': self.desig.id,
            'change_reason': 'Passed Probation'
        }
        res_edit = self.client.post(url_edit, data=edit_data, HTTP_HX_REQUEST='true')
        self.assertEqual(res_edit.status_code, 200)
        emp.refresh_from_db()
        self.assertEqual(emp.status, 'confirmed')
        self.assertTrue(EmploymentHistory.objects.filter(employee=emp, reason='Passed Probation').exists())

        # 5. Archive view POST
        url_archive = reverse('employees:master_archive', kwargs={'pk': emp.pk})
        res_archive = self.client.post(url_archive, HTTP_HX_REQUEST='true')
        self.assertEqual(res_archive.status_code, 200)
        emp.refresh_from_db()
        self.assertEqual(emp.status, 'archived')





