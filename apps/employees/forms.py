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

import uuid

def generate_employee_id():
    from apps.employees.models import EmployeeProfile, Employee
    year = date.today().year
    for _ in range(200):
        rand_num = random.randint(1000, 9999)
        candidate = f"EMP-{year}-{rand_num}"
        if not EmployeeProfile.objects.filter(employee_id=candidate).exists() and \
           not Employee.objects.filter(employee_number=candidate).exists():
            return candidate
    
    unique_hex = uuid.uuid4().hex[:6].upper()
    return f"EMP-{year}-{unique_hex}"

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

    def clean_employee_id(self):
        emp_id = self.cleaned_data.get('employee_id', '').strip()
        if not emp_id:
            return generate_employee_id()
        qs = EmployeeProfile.objects.filter(employee_id=emp_id)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            return generate_employee_id()
        return emp_id

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
            assignment = self.instance.user.role_assignments.select_related('role').first()
            if assignment:
                self.fields['role'].initial = assignment.role.code
            else:
                self.fields['role'].initial = self.instance.user.role
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
        
        # Sync phone and role to CustomUser and UserRoleAssignment
        user = profile.user
        if phone:
            phone = phone.strip()
        user.phone = phone
        if role:
            user.role = role
            from apps.accounts.rbac_models import Role, UserRoleAssignment
            role_obj = Role.objects.filter(code=role).first()
            if role_obj:
                UserRoleAssignment.objects.update_or_create(
                    user=user,
                    defaults={'role': role_obj}
                )
        user.save()
        user.groups.set(self.cleaned_data.get('groups', []))
        
        if new_password:
            user.set_password(new_password)
            user.save()
        if commit:
            profile.save()
        return profile



from apps.employees.models import Employee, Department, Designation, EmployeeStatus, EmploymentHistory

class EmployeeMasterForm(forms.ModelForm):
    class Meta:
        model = Employee
        fields = [
            'employee_number', 'first_name', 'last_name', 'dob', 'gender', 'national_id',
            'phone', 'personal_email', 'address',
            'emergency_contact_name', 'emergency_contact_phone', 'emergency_contact_relation', 'emergency_contact_address',
            'branch', 'department', 'designation', 'reporting_manager', 'joined_date', 'employment_type', 'shift', 'weekly_holiday_policy',
            'basic_salary', 'salary_structure', 'bank_name', 'bank_account', 'payment_method', 'tax_profile', 'pf_enabled', 'overtime_policy',
            'user', 'data_scope', 'mfa_required'
        ]
        widgets = {
            'dob': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': TEXT_INPUT}),
            'joined_date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': TEXT_INPUT}),
            'employee_number': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'e.g. EMP-2026-001'}),
            'first_name': forms.TextInput(attrs={'class': TEXT_INPUT}),
            'last_name': forms.TextInput(attrs={'class': TEXT_INPUT}),
            'gender': forms.Select(attrs={'class': SELECT_INPUT}),
            'national_id': forms.TextInput(attrs={'class': TEXT_INPUT}),
            'phone': forms.TextInput(attrs={'class': TEXT_INPUT}),
            'personal_email': forms.EmailInput(attrs={'class': TEXT_INPUT}),
            'address': forms.Textarea(attrs={'class': TEXT_INPUT, 'rows': 2}),
            'emergency_contact_name': forms.TextInput(attrs={'class': TEXT_INPUT}),
            'emergency_contact_phone': forms.TextInput(attrs={'class': TEXT_INPUT}),
            'emergency_contact_relation': forms.TextInput(attrs={'class': TEXT_INPUT}),
            'emergency_contact_address': forms.Textarea(attrs={'class': TEXT_INPUT, 'rows': 2}),
            'branch': forms.Select(attrs={'class': SELECT_INPUT}),
            'department': forms.Select(attrs={'class': SELECT_INPUT}),
            'designation': forms.Select(attrs={'class': SELECT_INPUT}),
            'reporting_manager': forms.Select(attrs={'class': SELECT_INPUT}),
            'employment_type': forms.Select(attrs={'class': SELECT_INPUT}),
            'shift': forms.TextInput(attrs={'class': TEXT_INPUT}),
            'weekly_holiday_policy': forms.TextInput(attrs={'class': TEXT_INPUT}),
            'basic_salary': forms.NumberInput(attrs={'class': TEXT_INPUT, 'placeholder': 'e.g. 50000.00'}),
            'salary_structure': forms.TextInput(attrs={'class': TEXT_INPUT}),
            'bank_name': forms.TextInput(attrs={'class': TEXT_INPUT}),
            'bank_account': forms.TextInput(attrs={'class': TEXT_INPUT}),
            'payment_method': forms.Select(attrs={'class': SELECT_INPUT}),
            'tax_profile': forms.TextInput(attrs={'class': TEXT_INPUT}),
            'pf_enabled': forms.CheckboxInput(attrs={'class': CHECKBOX_INPUT}),
            'overtime_policy': forms.TextInput(attrs={'class': TEXT_INPUT}),
            'user': forms.Select(attrs={'class': SELECT_INPUT}),
            'data_scope': forms.Select(attrs={'class': SELECT_INPUT}),
            'mfa_required': forms.CheckboxInput(attrs={'class': CHECKBOX_INPUT}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields['reporting_manager'].queryset = Employee.objects.exclude(pk=self.instance.pk)
        else:
            self.fields['reporting_manager'].queryset = Employee.objects.all()

    def clean(self):
        cleaned_data = super().clean()
        manager = cleaned_data.get('reporting_manager')
        if self.instance and self.instance.pk and manager:
            if manager.pk == self.instance.pk:
                self.add_error('reporting_manager', "An employee cannot report to themselves.")
            else:
                curr = manager
                visited = {self.instance.pk}
                while curr:
                    if curr.pk in visited:
                        self.add_error('reporting_manager', f"Circular reporting structure detected involving {curr.get_full_name()}.")
                        break
                    visited.add(curr.pk)
                    curr = curr.reporting_manager
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=commit)
        if commit and instance.user:
            from django.utils import timezone
            from apps.employees.models import EmployeeProfile
            profile = getattr(instance, 'legacy_profile', None)
            if not profile:
                profile = getattr(instance.user, 'employee_profile', None)
            if not profile:
                EmployeeProfile.objects.create(
                    user=instance.user,
                    master_employee=instance,
                    employee_id=instance.employee_number,
                    full_name=instance.get_full_name(),
                    phone=instance.phone or instance.user.phone or f"+8801000000{instance.pk}",
                    joined_date=instance.joined_date or timezone.localdate(),
                    branch=instance.branch
                )
        return instance


class DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields = ['name', 'code', 'description', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': TEXT_INPUT}),
            'code': forms.TextInput(attrs={'class': TEXT_INPUT}),
            'description': forms.Textarea(attrs={'class': TEXT_INPUT, 'rows': 2}),
            'is_active': forms.CheckboxInput(attrs={'class': CHECKBOX_INPUT}),
        }


class DesignationForm(forms.ModelForm):
    class Meta:
        model = Designation
        fields = ['name', 'code', 'description', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': TEXT_INPUT}),
            'code': forms.TextInput(attrs={'class': TEXT_INPUT}),
            'description': forms.Textarea(attrs={'class': TEXT_INPUT, 'rows': 2}),
            'is_active': forms.CheckboxInput(attrs={'class': CHECKBOX_INPUT}),
        }


# ── Lifecycle forms ───────────────────────────────────────────────────────────
from apps.employees.models import LifecycleTransitionRequest


class LifecycleActionForm(forms.Form):
    """Generic form for initiating any lifecycle transition."""
    to_status = forms.CharField(widget=forms.HiddenInput())
    reason = forms.CharField(
        label='Reason / Notes',
        widget=forms.Textarea(attrs={
            'class': TEXT_INPUT,
            'rows': 3,
            'placeholder': 'Provide a reason for this status change…',
        }),
        required=True,
    )
    effective_date = forms.DateField(
        label='Effective Date',
        widget=forms.DateInput(attrs={'class': TEXT_INPUT, 'type': 'date'}),
        required=True,
    )
    # Optional: for Promote / Transfer
    new_department = forms.ModelChoiceField(
        queryset=Department.objects.filter(is_active=True),
        required=False,
        label='New Department',
        widget=forms.Select(attrs={'class': SELECT_INPUT}),
    )
    new_designation = forms.ModelChoiceField(
        queryset=Designation.objects.filter(is_active=True),
        required=False,
        label='New Designation',
        widget=forms.Select(attrs={'class': SELECT_INPUT}),
    )

    def __init__(self, *args, to_status=None, **kwargs):
        super().__init__(*args, **kwargs)
        if to_status:
            self.fields['to_status'].initial = to_status
            # Show dept/desig only for relevant transitions
            if to_status not in ('promoted', 'transferred', 'demoted'):
                del self.fields['new_department']
                del self.fields['new_designation']


class ReviewTransitionForm(forms.Form):
    """Form for admin to approve or reject a LifecycleTransitionRequest."""
    review_note = forms.CharField(
        label='Review Note (optional)',
        widget=forms.Textarea(attrs={'class': TEXT_INPUT, 'rows': 2, 'placeholder': 'Optional note for the requester…'}),
        required=False,
    )
    # 'action' comes from the submit button name, not this form field.


from apps.employees.models import Asset, AssetAssignment, DocumentType, AssetType, AssetCondition

class EmployeeDocumentForm(forms.ModelForm):
    class Meta:
        model = EmployeeDocument
        fields = ['document_type', 'title', 'file', 'expiry_date']
        widgets = {
            'document_type': forms.Select(attrs={'class': SELECT_INPUT}),
            'title': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'e.g. Passport 2026-2036'}),
            'file': forms.FileInput(attrs={'class': FILE_INPUT}),
            'expiry_date': forms.DateInput(attrs={'class': TEXT_INPUT, 'type': 'date'}),
        }


class AssetForm(forms.ModelForm):
    class Meta:
        model = Asset
        fields = ['asset_type', 'asset_tag', 'name', 'serial_number', 'condition', 'warranty_expiry', 'is_active']
        widgets = {
            'asset_type': forms.Select(attrs={'class': SELECT_INPUT}),
            'asset_tag': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'AST-1001'}),
            'name': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'MacBook Pro 16"'}),
            'serial_number': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'C02F...'}),
            'condition': forms.Select(attrs={'class': SELECT_INPUT}),
            'warranty_expiry': forms.DateInput(attrs={'class': TEXT_INPUT, 'type': 'date'}),
            'is_active': forms.CheckboxInput(attrs={'class': CHECKBOX_INPUT}),
        }


class AssetAssignmentForm(forms.ModelForm):
    class Meta:
        model = AssetAssignment
        fields = ['asset', 'assigned_date', 'condition_at_assignment', 'notes']
        widgets = {
            'asset': forms.Select(attrs={'class': SELECT_INPUT}),
            'assigned_date': forms.DateInput(attrs={'class': TEXT_INPUT, 'type': 'date'}),
            'condition_at_assignment': forms.Select(attrs={'class': SELECT_INPUT}),
            'notes': forms.Textarea(attrs={'class': TEXT_INPUT, 'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        assigned_asset_ids = AssetAssignment.objects.filter(returned_date__isnull=True).values_list('asset_id', flat=True)
        self.fields['asset'].queryset = Asset.objects.filter(is_active=True).exclude(id__in=assigned_asset_ids)


class AssetReturnForm(forms.ModelForm):
    class Meta:
        model = AssetAssignment
        fields = ['returned_date', 'condition_at_return', 'notes']
        widgets = {
            'returned_date': forms.DateInput(attrs={'class': TEXT_INPUT, 'type': 'date'}),
            'condition_at_return': forms.Select(attrs={'class': SELECT_INPUT}),
            'notes': forms.Textarea(attrs={'class': TEXT_INPUT, 'rows': 2}),
        }


class AssetReassignForm(forms.Form):
    returned_date = forms.DateField(
        initial=timezone.localdate if 'timezone' in globals() else date.today,
        widget=forms.DateInput(attrs={'class': TEXT_INPUT, 'type': 'date'}),
        label="Return Date"
    )
    condition_at_return = forms.ChoiceField(
        choices=AssetCondition.choices,
        widget=forms.Select(attrs={'class': SELECT_INPUT}),
        label="Condition on Return"
    )
    return_notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': TEXT_INPUT, 'rows': 2}),
        label="Return Notes"
    )
    
    new_employee = forms.ModelChoiceField(
        queryset=Employee.objects.filter(status='active'),
        widget=forms.Select(attrs={'class': SELECT_INPUT}),
        label="Reassign To Employee"
    )
    assigned_date = forms.DateField(
        initial=timezone.localdate if 'timezone' in globals() else date.today,
        widget=forms.DateInput(attrs={'class': TEXT_INPUT, 'type': 'date'}),
        label="New Assignment Date"
    )
    condition_at_assignment = forms.ChoiceField(
        choices=AssetCondition.choices,
        initial=AssetCondition.GOOD,
        widget=forms.Select(attrs={'class': SELECT_INPUT}),
        label="New Assignment Condition"
    )
    new_notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': TEXT_INPUT, 'rows': 2}),
        label="New Assignment Notes"
    )

    def __init__(self, *args, **kwargs):
        self.current_assignment = kwargs.pop('current_assignment', None)
        super().__init__(*args, **kwargs)
        from django.utils import timezone
        self.fields['returned_date'].initial = timezone.localdate()
        self.fields['assigned_date'].initial = timezone.localdate()
        if self.current_assignment:
            self.fields['new_employee'].queryset = Employee.objects.filter(status='active').exclude(pk=self.current_assignment.employee_id)


# ── Wizard Step Forms ────────────────────────────────────────────────────────
from apps.accounts.rbac_models import Role, UserRoleAssignment

class WizardStep1Form(forms.ModelForm):
    class Meta:
        model = Employee
        fields = ['employee_number', 'first_name', 'last_name', 'personal_email', 'phone', 'dob', 'gender']
        widgets = {
            'employee_number': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'e.g. EMP-2026-001'}),
            'first_name': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'First Name'}),
            'last_name': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'Last Name'}),
            'personal_email': forms.EmailInput(attrs={'class': TEXT_INPUT, 'placeholder': 'name@example.com'}),
            'phone': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': '+880...'}),
            'dob': forms.DateInput(attrs={'class': TEXT_INPUT, 'type': 'date'}),
            'gender': forms.Select(attrs={'class': SELECT_INPUT}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.initial.get('employee_number'):
            self.initial['employee_number'] = generate_employee_id()
        self.fields['employee_number'].required = True
        self.fields['first_name'].required = True
        self.fields['last_name'].required = True

    def clean_employee_number(self):
        emp_num = self.cleaned_data.get('employee_number', '').strip()
        if not emp_num:
            return generate_employee_id()
        qs = Employee.objects.filter(employee_number=emp_num)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            return generate_employee_id()
        return emp_num

    def clean_personal_email(self):
        email = self.cleaned_data.get('personal_email', '').strip()
        if email:
            qs = Employee.objects.filter(personal_email__iexact=email)
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError("An employee with this email address already exists.")
        return email

    def clean_phone(self):
        phone = self.cleaned_data.get('phone', '').strip()
        if phone:
            qs = Employee.objects.filter(phone=phone)
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError("An employee with this phone number already exists.")
        return phone


class WizardStep2Form(forms.ModelForm):
    class Meta:
        model = Employee
        fields = [
            'branch', 'department', 'designation', 'reporting_manager',
            'employment_type', 'joined_date', 'shift', 'weekly_holiday_policy'
        ]
        widgets = {
            'branch': forms.Select(attrs={'class': SELECT_INPUT}),
            'department': forms.Select(attrs={'class': SELECT_INPUT}),
            'designation': forms.Select(attrs={'class': SELECT_INPUT}),
            'reporting_manager': forms.Select(attrs={'class': SELECT_INPUT}),
            'employment_type': forms.Select(attrs={'class': SELECT_INPUT}),
            'joined_date': forms.DateInput(attrs={'class': TEXT_INPUT, 'type': 'date'}),
            'shift': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'e.g. Day Shift (9 AM - 6 PM)'}),
            'weekly_holiday_policy': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'e.g. Friday, Saturday'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields['reporting_manager'].queryset = Employee.objects.exclude(pk=self.instance.pk)
        if not self.initial.get('joined_date'):
            self.initial['joined_date'] = date.today()

    def clean(self):
        cleaned_data = super().clean()
        manager = cleaned_data.get('reporting_manager')
        if self.instance and self.instance.pk and manager:
            if manager.pk == self.instance.pk:
                self.add_error('reporting_manager', "An employee cannot report to themselves.")
            else:
                curr = manager
                visited = {self.instance.pk}
                while curr:
                    if curr.pk in visited:
                        self.add_error('reporting_manager', f"Circular reporting structure detected involving {curr.get_full_name()}.")
                        break
                    visited.add(curr.pk)
                    curr = curr.reporting_manager
        return cleaned_data


class WizardStep3Form(forms.ModelForm):
    class Meta:
        model = Employee
        fields = [
            'basic_salary', 'salary_structure', 'bank_name', 'bank_account',
            'payment_method', 'tax_profile', 'pf_enabled', 'overtime_policy'
        ]
        widgets = {
            'basic_salary': forms.NumberInput(attrs={'class': TEXT_INPUT, 'step': '0.01', 'placeholder': '0.00'}),
            'salary_structure': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'e.g. Executive Grade B'}),
            'bank_name': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'e.g. City Bank Ltd'}),
            'bank_account': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'Account Number'}),
            'payment_method': forms.Select(attrs={'class': SELECT_INPUT}),
            'tax_profile': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'TIN / Tax Region'}),
            'pf_enabled': forms.CheckboxInput(attrs={'class': CHECKBOX_INPUT}),
            'overtime_policy': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'e.g. Standard 1.5x'}),
        }


class WizardStep4Form(forms.Form):
    login_email = forms.EmailField(
        label="Login Email",
        widget=forms.EmailInput(attrs={'class': TEXT_INPUT, 'placeholder': 'user@company.com'}),
        required=True
    )
    password1 = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={'class': TEXT_INPUT, 'placeholder': 'Set password'}),
        required=False
    )
    password2 = forms.CharField(
        label="Confirm Password",
        widget=forms.PasswordInput(attrs={'class': TEXT_INPUT, 'placeholder': 'Repeat password'}),
        required=False
    )
    roles = forms.ModelMultipleChoiceField(
        queryset=Role.objects.filter(is_active=True),
        widget=forms.SelectMultiple(attrs={'class': SELECT_INPUT, 'size': 4}),
        required=True,
        label="Assigned Roles (UserRoleAssignment)"
    )
    data_scope = forms.ChoiceField(
        choices=Employee.DATA_SCOPE_CHOICES,
        widget=forms.Select(attrs={'class': SELECT_INPUT}),
        initial='branch',
        label="Data Scope"
    )
    mfa_required = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={'class': CHECKBOX_INPUT}),
        label="Require Multi-Factor Authentication (MFA)"
    )

    def __init__(self, *args, employee=None, **kwargs):
        self.employee = employee
        super().__init__(*args, **kwargs)
        if employee and employee.user:
            self.fields['login_email'].initial = employee.user.email
            self.fields['data_scope'].initial = employee.data_scope
            self.fields['mfa_required'].initial = employee.mfa_required
            assigned_role_ids = UserRoleAssignment.objects.filter(user=employee.user).values_list('role_id', flat=True)
            self.fields['roles'].initial = assigned_role_ids

    def clean_login_email(self):
        email = self.cleaned_data.get('login_email', '').strip()
        qs = User.objects.filter(email__iexact=email)
        if self.employee and self.employee.user:
            qs = qs.exclude(pk=self.employee.user.pk)
        if qs.exists():
            raise forms.ValidationError("A user account with this login email already exists.")
        return email

    def clean(self):
        cleaned_data = super().clean()
        p1 = cleaned_data.get('password1')
        p2 = cleaned_data.get('password2')
        # If new account (no employee.user), password is required
        if not (self.employee and self.employee.user) and not p1:
            self.add_error('password1', "Password is required when creating a new user account.")
        if p1 or p2:
            if p1 != p2:
                self.add_error('password2', "Passwords do not match.")
            elif len(p1) < 8:
                self.add_error('password1', "Password must be at least 8 characters long.")
        return cleaned_data

    def save(self):
        cleaned_data = self.cleaned_data
        email = cleaned_data['login_email']
        p1 = cleaned_data.get('password1')
        roles = cleaned_data['roles']
        data_scope = cleaned_data['data_scope']
        mfa_required = cleaned_data['mfa_required']

        user = self.employee.user if self.employee else None
        if not user:
            # Check if user with email exists
            user = User.objects.filter(email__iexact=email).first()

        if not user:
            user = User.objects.create_user(
                email=email,
                phone=self.employee.phone or None,
                password=p1
            )
        elif p1:
            user.set_password(p1)
            user.save()
        # Update Employee fields
        self.employee.user = user
        self.employee.data_scope = data_scope
        self.employee.mfa_required = mfa_required
        self.employee.save()

        # Auto-create or sync EmployeeProfile legacy bridge
        from apps.employees.models import EmployeeProfile
        from django.utils import timezone
        profile = getattr(self.employee, 'legacy_profile', None)
        if not profile:
            profile = getattr(user, 'employee_profile', None)
        if not profile:
            EmployeeProfile.objects.create(
                user=user,
                master_employee=self.employee,
                employee_id=self.employee.employee_number,
                full_name=self.employee.get_full_name(),
                phone=self.employee.phone or user.phone or f"+8801000000{self.employee.pk}",
                joined_date=self.employee.joined_date or timezone.localdate(),
                branch=self.employee.branch
            )

        # Sync UserRoleAssignment — zero direct CustomUser.role write
        UserRoleAssignment.objects.filter(user=user).delete()
        for r in roles:
            UserRoleAssignment.objects.create(user=user, role=r, assigned_by=None)

        return user


class WizardStep6Form(forms.ModelForm):
    class Meta:
        model = Employee
        fields = ['emergency_contact_name', 'emergency_contact_relation', 'emergency_contact_phone', 'emergency_contact_address']
        widgets = {
            'emergency_contact_name': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'Contact Name'}),
            'emergency_contact_relation': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'Relation (e.g. Spouse, Parent)'}),
            'emergency_contact_phone': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'Phone Number'}),
            'emergency_contact_address': forms.Textarea(attrs={'class': TEXT_INPUT, 'rows': 2, 'placeholder': 'Address'}),
        }



