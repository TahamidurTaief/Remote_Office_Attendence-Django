from django.test import TestCase
from django.contrib.auth import get_user_model, authenticate
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



