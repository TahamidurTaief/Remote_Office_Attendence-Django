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
        self.client.login(username='staff@test.com', password='staffpassword123')
        response = self.client.get(url_add)
        self.assertEqual(response.status_code, 302)
        self.client.logout()

        # Admin can add document
        self.client.login(username='admin@test.com', password='adminpassword123')
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



