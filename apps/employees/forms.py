from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from apps.employees.models import EmployeeProfile, EmployeeDocument
from apps.branches.models import Branch
import random
import string
from datetime import date


TEXT_INPUT = (
    "w-full px-3 py-2.5 border border-gray-200 rounded-xl text-sm text-gray-800 "
    "focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent "
    "placeholder:text-gray-400 transition-colors"
)

SELECT_INPUT = (
    "w-full px-3 py-1.5 border border-gray-200 rounded-xl text-sm text-gray-800 bg-white "
    "focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
)

FILE_INPUT = (
    "block w-full text-sm text-gray-500 file:mr-4 file:py-2 file:px-4 file:rounded-xl "
    "file:border-0 file:text-sm file:font-semibold file:bg-indigo-50 file:text-indigo-700 "
    "hover:file:bg-indigo-100"
)

CHECKBOX_INPUT = 'h-4 w-4 text-indigo-600 focus:ring-indigo-500 border-gray-300 rounded'

User = get_user_model()

def generate_employee_id():
    year = date.today().year
    last_emp = EmployeeProfile.objects.filter(employee_id__startswith=f'EMP-{year}-').order_by('-employee_id').first()
    if last_emp:
        try:
            last_num = int(last_emp.employee_id.split('-')[-1])
            new_num = last_num + 1
        except ValueError:
            new_num = 1
    else:
        new_num = 1
    return f'EMP-{year}-{new_num:03d}'

def generate_random_password(length=10):
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

class EmployeeCreateForm(forms.ModelForm):
    email = forms.EmailField(required=False, label="Email Address (Optional)")
    role = forms.ChoiceField(choices=[('staff', 'Staff'), ('manager', 'Manager')], initial='staff')
    groups = forms.ModelMultipleChoiceField(queryset=Group.objects.all(), required=False, widget=forms.SelectMultiple(attrs={'class': SELECT_INPUT}), label="Roles / Groups")
    send_email = forms.BooleanField(required=False, initial=True, label="Send welcome email")
    password1 = forms.CharField(widget=forms.PasswordInput(attrs={'placeholder': 'Set login password', 'class': TEXT_INPUT}), label="Password", required=True)
    password2 = forms.CharField(widget=forms.PasswordInput(attrs={'placeholder': 'Repeat password', 'class': TEXT_INPUT}), label="Confirm Password", required=True)

    class Meta:
        model = EmployeeProfile
        fields = ['employee_id', 'full_name', 'department', 'designation', 'branch', 'phone', 'emergency_contact', 'profile_photo', 'joined_date', 'is_active', 'tracking_interval', 'overtime_enabled', 'is_project_manager']
        widgets = {
            'joined_date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': TEXT_INPUT}),
            'employee_id': forms.TextInput(attrs={'class': TEXT_INPUT}),
            'full_name': forms.TextInput(attrs={'class': TEXT_INPUT}),
            'department': forms.TextInput(attrs={'class': TEXT_INPUT}),
            'designation': forms.TextInput(attrs={'class': TEXT_INPUT}),
            'branch': forms.Select(attrs={'class': SELECT_INPUT}),
            'phone': forms.TextInput(attrs={'class': TEXT_INPUT}),
            'emergency_contact': forms.TextInput(attrs={'class': TEXT_INPUT}),
            'profile_photo': forms.ClearableFileInput(attrs={'class': FILE_INPUT}),
            'tracking_interval': forms.Select(attrs={'class': SELECT_INPUT}),
            'overtime_enabled': forms.CheckboxInput(attrs={'class': CHECKBOX_INPUT}),
            'is_active': forms.CheckboxInput(attrs={'class': CHECKBOX_INPUT}),
            'is_project_manager': forms.CheckboxInput(attrs={'class': CHECKBOX_INPUT}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.initial.get('employee_id'):
            self.initial['employee_id'] = generate_employee_id()
        if not self.initial.get('joined_date'):
            self.initial['joined_date'] = date.today()
        
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.update({'class': CHECKBOX_INPUT})
            elif not field.widget.attrs.get('class'):
                field.widget.attrs.update({'class': TEXT_INPUT})

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if not email:
            return None
        email = email.strip()
        if not email:
            return None
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("User with this email already exists.")
        return email

    def clean_phone(self):
        phone = self.cleaned_data.get('phone')
        if phone:
            phone = phone.strip()
            if EmployeeProfile.objects.filter(phone=phone).exists():
                raise forms.ValidationError("An employee with this phone number already exists.")
            if User.objects.filter(phone=phone).exists():
                raise forms.ValidationError("A user with this phone number already exists.")
        return phone

    def clean_profile_photo(self):
        photo = self.cleaned_data.get('profile_photo')
        if photo:
            allowed_types = ['image/jpeg', 'image/jpg', 'image/png', 'image/webp']
            content_type = getattr(photo, 'content_type', '')
            if content_type and content_type not in allowed_types:
                raise forms.ValidationError("Invalid file type. Only JPEG, PNG, and WEBP images are allowed.")
            if photo.size > 5 * 1024 * 1024:
                raise forms.ValidationError("File too large. Profile photo must be less than 5MB.")
        return photo

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get('password1')
        password2 = cleaned_data.get('password2')
        if password1 and password2:
            if password1 != password2:
                raise forms.ValidationError("Passwords do not match")
            if len(password1) < 8:
                raise forms.ValidationError("Password must be at least 8 characters")
        return cleaned_data

    def save(self, commit=True):
        profile = super().save(commit=False)
        email = self.cleaned_data.get('email')
        role = self.cleaned_data['role']
        password = self.cleaned_data['password1']
        phone = self.cleaned_data.get('phone')
        
        if email:
            email = email.strip()
            if not email:
                email = None
        else:
            email = None
            
        user = User.objects.create_user(email=email, phone=phone, password=password, role=role)
        user.groups.set(self.cleaned_data.get('groups', []))
        profile.user = user
        
        if commit:
            profile.save()
            # If send_email is True, you would implement email sending here
            
        return profile

class EmployeeEditForm(forms.ModelForm):
    role = forms.ChoiceField(choices=[('staff', 'Staff'), ('manager', 'Manager')], widget=forms.Select(attrs={'class': SELECT_INPUT}), label="Role", required=True)
    groups = forms.ModelMultipleChoiceField(queryset=Group.objects.all(), required=False, widget=forms.SelectMultiple(attrs={'class': SELECT_INPUT}), label="Roles / Groups")
    new_password = forms.CharField(widget=forms.PasswordInput(attrs={'placeholder': 'Leave blank to keep current'}), label="New Password", required=False)
    confirm_password = forms.CharField(widget=forms.PasswordInput(attrs={'placeholder': 'Repeat new password', 'class': TEXT_INPUT}), label="Confirm Password", required=False)

    class Meta:
        model = EmployeeProfile
        fields = ['employee_id', 'full_name', 'department', 'designation', 'branch', 'phone', 'emergency_contact', 'profile_photo', 'joined_date', 'is_active', 'tracking_interval', 'overtime_enabled', 'is_project_manager']
        widgets = {
            'joined_date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': TEXT_INPUT}),
            'employee_id': forms.TextInput(attrs={'class': TEXT_INPUT}),
            'full_name': forms.TextInput(attrs={'class': TEXT_INPUT}),
            'department': forms.TextInput(attrs={'class': TEXT_INPUT}),
            'designation': forms.TextInput(attrs={'class': TEXT_INPUT}),
            'branch': forms.Select(attrs={'class': SELECT_INPUT}),
            'phone': forms.TextInput(attrs={'class': TEXT_INPUT}),
            'emergency_contact': forms.TextInput(attrs={'class': TEXT_INPUT}),
            'profile_photo': forms.ClearableFileInput(attrs={'class': FILE_INPUT}),
            'tracking_interval': forms.Select(attrs={'class': SELECT_INPUT}),
            'overtime_enabled': forms.CheckboxInput(attrs={'class': CHECKBOX_INPUT}),
            'is_active': forms.CheckboxInput(attrs={'class': CHECKBOX_INPUT}),
            'is_project_manager': forms.CheckboxInput(attrs={'class': CHECKBOX_INPUT}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.user:
            self.fields['role'].initial = self.instance.user.role
            self.fields['groups'].initial = self.instance.user.groups.all()
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.update({'class': CHECKBOX_INPUT})
            elif not field.widget.attrs.get('class'):
                field.widget.attrs.update({'class': TEXT_INPUT})

    def clean_phone(self):
        phone = self.cleaned_data.get('phone')
        if phone:
            phone = phone.strip()
            qs_profile = EmployeeProfile.objects.filter(phone=phone)
            if self.instance and self.instance.pk:
                qs_profile = qs_profile.exclude(pk=self.instance.pk)
            if qs_profile.exists():
                raise forms.ValidationError("An employee with this phone number already exists.")
            
            qs_user = User.objects.filter(phone=phone)
            if self.instance and self.instance.user:
                qs_user = qs_user.exclude(pk=self.instance.user.pk)
            if qs_user.exists():
                raise forms.ValidationError("A user with this phone number already exists.")
        return phone

    def clean_profile_photo(self):
        photo = self.cleaned_data.get('profile_photo')
        if photo:
            allowed_types = ['image/jpeg', 'image/jpg', 'image/png', 'image/webp']
            content_type = getattr(photo, 'content_type', '')
            if content_type and content_type not in allowed_types:
                raise forms.ValidationError("Invalid file type. Only JPEG, PNG, and WEBP images are allowed.")
            if photo.size > 5 * 1024 * 1024:
                raise forms.ValidationError("File too large. Profile photo must be less than 5MB.")
        return photo

    def clean(self):
        cleaned_data = super().clean()
        new_password = cleaned_data.get('new_password')
        confirm_password = cleaned_data.get('confirm_password')
        if new_password or confirm_password:
            if new_password != confirm_password:
                raise forms.ValidationError("Passwords do not match")
            if len(new_password) < 8:
                raise forms.ValidationError("Password must be at least 8 characters")
        return cleaned_data
        
    def save(self, commit=True):
        profile = super().save(commit=False)
        new_password = self.cleaned_data.get('new_password')
        phone = self.cleaned_data.get('phone')
        role = self.cleaned_data.get('role')
        
        # Sync phone and role to CustomUser
        user = profile.user
        if phone:
            phone = phone.strip()
        user.phone = phone
        if role:
            user.role = role
        user.save()
        user.groups.set(self.cleaned_data.get('groups', []))
        
        if new_password:
            user.set_password(new_password)
            user.save()
        if commit:
            profile.save()
        return profile


class EmployeeDocumentForm(forms.ModelForm):
    class Meta:
        model = EmployeeDocument
        fields = ['document_type', 'expiry_date', 'file']
        widgets = {
            'document_type': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'e.g. Visa, Trade License'}),
            'expiry_date': forms.DateInput(format='%Y-%m-%d', attrs={'class': TEXT_INPUT, 'type': 'date'}),
            'file': forms.ClearableFileInput(attrs={'class': FILE_INPUT}),
        }
