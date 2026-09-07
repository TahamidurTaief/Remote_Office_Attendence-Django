from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
import re
import logging

logger = logging.getLogger(__name__)
from apps.employees.models import EmployeeProfile, EmployeeDocument, Bank, BankBranch, EmployeeBankAccount, Employee
from apps.employees.bank_crypto import normalize_account_number
from apps.employees.bank_registry import (
    BANGLADESH_BANK_CHOICES,
    BANGLADESH_BANK_GROUPS,
    resolve_canonical_bank_name,
)
from django.db.models import Q
from apps.branches.models import Branch
from apps.accounts.rbac_models import Role, UserRoleAssignment
from apps.accounts.services import RoleAssignmentService
from django.db import transaction
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
    roles = forms.ModelMultipleChoiceField(
        queryset=Role.objects.none(),
        required=True,
        label="Roles"
    )
    custom_permissions = forms.CharField(required=False, widget=forms.HiddenInput())
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

    def __init__(self, *args, actor=None, **kwargs):
        self.actor = actor
        super().__init__(*args, **kwargs)
        if not self.initial.get('employee_id'):
            self.initial['employee_id'] = generate_employee_id()
        if not self.initial.get('joined_date'):
            self.initial['joined_date'] = date.today()

        assignable_qs = RoleAssignmentService.get_assignable_roles_queryset(actor=self.actor)
        self.fields['roles'].queryset = assignable_qs
        from apps.accounts.rbac_registry import RBACRegistryService
        self.fields['roles'].available_permissions = RBACRegistryService.get_canonical_permissions_catalog()

        # Set default role initial selection (e.g. staff role)
        if not self.initial.get('roles'):
            default_role = assignable_qs.filter(code='staff').first()
            if default_role:
                self.initial['roles'] = [default_role.pk]
                self.fields['roles'].initial = [default_role.pk]

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

    def clean_roles(self):
        roles = list(self.cleaned_data.get('roles') or [])
        if not roles:
            raise forms.ValidationError("At least one active role must be assigned.")

        for r in roles:
            if not r.is_active:
                raise forms.ValidationError(f"Role '{r.name}' is inactive and cannot be assigned.")
            if r.code == 'system_owner' or r.is_system_protected:
                raise forms.ValidationError("The System Owner role cannot be assigned via employee forms.")

        if not self.actor or not self.actor.is_authenticated:
            raise forms.ValidationError("You must be logged in with authorization to assign roles.")

        try:
            RoleAssignmentService.validate_role_authority(self.actor, roles)
        except PermissionDenied as e:
            raise forms.ValidationError(str(e))

        return roles

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get('password1')
        password2 = cleaned_data.get('password2')
        if password1 or password2:
            if password1 != password2:
                self.add_error('password2', "Passwords do not match")
            elif len(password1) < 6:
                self.add_error('password1', "Password must be at least 6 characters long.")
        return cleaned_data

    @transaction.atomic
    def save(self, commit=True):
        profile = super().save(commit=False)
        email = self.cleaned_data.get('email')
        roles = self.cleaned_data['roles']
        password = self.cleaned_data['password1']
        phone = self.cleaned_data.get('phone')

        if email:
            email = email.strip()
            if not email:
                email = None
        else:
            email = None

        compat_role = RoleAssignmentService.compute_compatibility_persona(roles)
        user = User.objects.create_user(email=email, phone=phone, password=password, role=compat_role)
        profile.user = user

        # Ensure Employee Master SSOT record exists and is linked
        from django.utils import timezone
        name_parts = (profile.full_name or 'Employee').strip().split(' ', 1)
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else ''
        emp_master, _ = Employee.objects.get_or_create(
            employee_number=profile.employee_id,
            defaults={
                'user': user,
                'first_name': first_name,
                'last_name': last_name,
                'phone': phone or '',
                'personal_email': email or '',
                'branch': profile.branch,
                'joined_date': profile.joined_date or timezone.localdate(),
                'status': 'active' if profile.is_active else 'inactive'
            }
        )
        if emp_master.user != user:
            emp_master.user = user
            emp_master.save(update_fields=['user'])
        profile.master_employee = emp_master

        if commit:
            profile.save()

        # Perform atomic role assignment sync
        RoleAssignmentService.sync_user_roles(
            user=user,
            target_roles=roles,
            actor=self.actor,
            preserve_protected=True
        )

        # Atomic custom permissions override sync
        custom_perms_raw = self.cleaned_data.get('custom_permissions')
        if custom_perms_raw:
            try:
                import json
                from apps.accounts.rbac_models import UserPermissionOverride, Permission
                from apps.accounts.rbac_registry import RBACRegistryService
                overrides_data = json.loads(custom_perms_raw) if isinstance(custom_perms_raw, str) else custom_perms_raw
                if isinstance(overrides_data, list):
                    saved_perm_ids = set()
                    for item in overrides_data:
                        codename = item.get('codename')
                        perm_id = item.get('permission_id')
                        perm = Permission.objects.filter(pk=perm_id).first() if perm_id else None
                        if not perm and codename:
                            perm = RBACRegistryService.ensure_permission(codename)
                        if perm:
                            is_granted = bool(item.get('is_granted', True))
                            scope_val = item.get('data_scope') or None
                            UserPermissionOverride.objects.update_or_create(
                                user=user,
                                permission=perm,
                                defaults={'is_granted': is_granted, 'data_scope': scope_val}
                            )
                            saved_perm_ids.add(perm.pk)
                    UserPermissionOverride.objects.filter(user=user).exclude(permission_id__in=saved_perm_ids).delete()
                    RoleAssignmentService.invalidate_user_permissions(user)
            except Exception:
                pass

        return profile

class EmployeeEditForm(forms.ModelForm):
    roles = forms.ModelMultipleChoiceField(
        queryset=Role.objects.none(),
        required=True,
        label="Roles"
    )
    custom_permissions = forms.CharField(required=False, widget=forms.HiddenInput())
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

    def __init__(self, *args, actor=None, **kwargs):
        self.actor = actor
        super().__init__(*args, **kwargs)

        assignable_qs = RoleAssignmentService.get_assignable_roles_queryset(actor=self.actor)
        self.fields['roles'].queryset = assignable_qs
        from apps.accounts.rbac_registry import RBACRegistryService
        from apps.accounts.rbac_models import UserPermissionOverride
        import json
        self.fields['roles'].available_permissions = RBACRegistryService.get_canonical_permissions_catalog()

        if self.instance and self.instance.user:
            user_role_ids = list(
                UserRoleAssignment.objects.filter(user=self.instance.user).values_list('role_id', flat=True)
            )
            if not user_role_ids and self.instance.user.role:
                role_match = assignable_qs.filter(code=self.instance.user.role).first()
                if role_match:
                    user_role_ids = [role_match.pk]
            self.fields['roles'].initial = user_role_ids
            self.initial['roles'] = user_role_ids
            existing_overrides = UserPermissionOverride.objects.filter(user=self.instance.user).select_related('permission')
            if existing_overrides.exists():
                self.fields['custom_permissions'].initial = json.dumps([
                    {
                        'permission_id': ov.permission_id,
                        'codename': ov.permission.codename,
                        'name': ov.permission.name or ov.permission.codename,
                        'is_granted': ov.is_granted,
                        'data_scope': ov.data_scope or ''
                    }
                    for ov in existing_overrides
                ])
        elif not self.initial.get('roles'):
            default_role = assignable_qs.filter(code='staff').first()
            if default_role:
                self.initial['roles'] = [default_role.pk]

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

    def clean_roles(self):
        roles = list(self.cleaned_data.get('roles') or [])
        if not roles:
            raise forms.ValidationError("At least one active role must be assigned.")

        for r in roles:
            if not r.is_active:
                raise forms.ValidationError(f"Role '{r.name}' is inactive and cannot be assigned.")
            if r.code == 'system_owner' or r.is_system_protected:
                raise forms.ValidationError("The System Owner role cannot be assigned via employee forms.")

        if not self.actor or not self.actor.is_authenticated:
            raise forms.ValidationError("You must be logged in with authorization to assign roles.")

        try:
            RoleAssignmentService.validate_role_authority(self.actor, roles)
        except PermissionDenied as e:
            raise forms.ValidationError(str(e))

        return roles

    def clean(self):
        cleaned_data = super().clean()
        new_password = cleaned_data.get('new_password')
        confirm_password = cleaned_data.get('confirm_password')
        if new_password or confirm_password:
            if new_password != confirm_password:
                self.add_error('confirm_password', "Passwords do not match")
            else:
                from django.contrib.auth.password_validation import validate_password
                from django.core.exceptions import ValidationError as DjangoValidationError
                user_instance = self.instance.user if (self.instance and self.instance.user) else None
                try:
                    validate_password(new_password, user=user_instance)
                except DjangoValidationError as e:
                    self.add_error('new_password', e.messages)
        return cleaned_data

    @transaction.atomic
    def save(self, commit=True):
        profile = super().save(commit=False)
        new_password = self.cleaned_data.get('new_password')
        phone = self.cleaned_data.get('phone')
        roles = self.cleaned_data.get('roles')

        # Sync phone to CustomUser
        user = profile.user
        if phone:
            phone = phone.strip()
        user.phone = phone
        user.save()

        if new_password:
            user.set_password(new_password)
            user.save()
            from apps.accounts.models import UserSession
            from django.contrib.sessions.models import Session
            from apps.audit.services import AuditService
            from apps.notifications.models import log_audit
            from django.utils import timezone
            now = timezone.now()
            for sess in UserSession.objects.filter(user=user, is_active=True):
                sess.is_active = False
                sess.logout_time = now
                sess.save(update_fields=['is_active', 'logout_time'])
                if sess.session_key:
                    Session.objects.filter(session_key=sess.session_key).delete()
            AuditService.log_event(
                actor=self.actor if (self.actor and self.actor.is_authenticated) else None,
                action="password_change",
                instance=user,
                module="employees",
                object_type="CustomUser",
                object_id=str(user.pk),
                object_label=user.email or user.phone,
                reason=f"Password updated for employee {profile.full_name}"
            )
            log_audit(
                actor=self.actor if (self.actor and self.actor.is_authenticated) else None,
                action="password_change",
                target=user,
                summary=f"Password updated for employee {profile.full_name}"
            )

        # Perform atomic role assignment diff sync
        if roles is not None:
            RoleAssignmentService.sync_user_roles(
                user=user,
                target_roles=roles,
                actor=self.actor,
                preserve_protected=True
            )

        # Atomic custom permissions override sync
        custom_perms_raw = self.cleaned_data.get('custom_permissions')
        if custom_perms_raw and user:
            try:
                import json
                from apps.accounts.rbac_models import UserPermissionOverride, Permission
                from apps.accounts.rbac_registry import RBACRegistryService
                overrides_data = json.loads(custom_perms_raw) if isinstance(custom_perms_raw, str) else custom_perms_raw
                if isinstance(overrides_data, list):
                    saved_perm_ids = set()
                    for item in overrides_data:
                        codename = item.get('codename')
                        perm_id = item.get('permission_id')
                        perm = Permission.objects.filter(pk=perm_id).first() if perm_id else None
                        if not perm and codename:
                            perm = RBACRegistryService.ensure_permission(codename)
                        if perm:
                            is_granted = bool(item.get('is_granted', True))
                            scope_val = item.get('data_scope') or None
                            UserPermissionOverride.objects.update_or_create(
                                user=user,
                                permission=perm,
                                defaults={'is_granted': is_granted, 'data_scope': scope_val}
                            )
                            saved_perm_ids.add(perm.pk)
                    UserPermissionOverride.objects.filter(user=user).exclude(permission_id__in=saved_perm_ids).delete()
                    RoleAssignmentService.invalidate_user_permissions(user)
            except Exception:
                pass
        elif custom_perms_raw == '[]' and user:
            from apps.accounts.rbac_models import UserPermissionOverride
            UserPermissionOverride.objects.filter(user=user).delete()
            RoleAssignmentService.invalidate_user_permissions(user)

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
            'bank_name': forms.Select(choices=BANGLADESH_BANK_CHOICES, attrs={'class': SELECT_INPUT}),
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

        # Populate department queryset scoped to selected branch
        branch = None
        if self.instance and self.instance.pk and self.instance.branch_id:
            branch = self.instance.branch
        elif self.data.get('branch'):
            from apps.branches.models import Branch as BranchModel
            try:
                branch = BranchModel.objects.get(pk=self.data['branch'])
            except BranchModel.DoesNotExist:
                pass
        self.fields['department'].queryset = Department.available_for_branch(branch)

        # Populate designation queryset scoped to selected department
        dept = None
        if self.instance and self.instance.pk and self.instance.department_id:
            dept = self.instance.department
        elif self.data.get('department'):
            try:
                dept = Department.objects.get(pk=self.data['department'])
            except Department.DoesNotExist:
                pass
        if dept:
            self.fields['designation'].queryset = Designation.available_for_department(dept)
        else:
            self.fields['designation'].queryset = Designation.objects.filter(is_active=True)

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

        # Cross-hierarchy validation: branch ↔ department ↔ designation
        branch = cleaned_data.get('branch')
        department = cleaned_data.get('department')
        designation = cleaned_data.get('designation')

        if department and branch:
            if not department.is_global and not department.branches.filter(pk=branch.pk).exists():
                self.add_error('department', f'Department "{department.name}" is not available for branch "{branch.name}".')

        if designation and department:
            if designation.department_id and designation.department_id != department.pk:
                self.add_error('designation', f'Designation "{designation.name}" does not belong to department "{department.name}".')

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
        if commit and instance.pk:
            from apps.payroll.payment_destination_service import PayrollPaymentDestinationService
            pm = self.cleaned_data.get('payment_method') or 'bank'
            dest_data = {
                'bank_name': self.cleaned_data.get('bank_name'),
                'account_number': self.cleaned_data.get('bank_account'),
                'account_holder_name': instance.get_full_name(),
            }
            try:
                PayrollPaymentDestinationService.save_destination(
                    employee=instance,
                    payment_type=pm,
                    data=dest_data
                )
            except Exception as e:
                logger.warning("Could not sync payment destination during EmployeeMasterForm save: %s", e)
        return instance


class DepartmentForm(forms.ModelForm):
    branches = forms.ModelMultipleChoiceField(
        queryset=Branch.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': SELECT_INPUT}),
        label='Branch-Specific Branches',
        help_text='Only set when Department is NOT global. Leave empty for global departments.',
    )

    class Meta:
        model = Department
        fields = ['name', 'code', 'description', 'is_global', 'branches', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': TEXT_INPUT}),
            'code': forms.TextInput(attrs={'class': TEXT_INPUT}),
            'description': forms.Textarea(attrs={'class': TEXT_INPUT, 'rows': 2}),
            'is_global': forms.CheckboxInput(attrs={'class': CHECKBOX_INPUT}),
            'is_active': forms.CheckboxInput(attrs={'class': CHECKBOX_INPUT}),
        }

    def clean(self):
        cleaned_data = super().clean()
        is_global = cleaned_data.get('is_global', True)
        branches = cleaned_data.get('branches', [])
        if not is_global and not branches:
            raise forms.ValidationError('A branch-specific department must have at least one branch assigned.')
        return cleaned_data


class DesignationForm(forms.ModelForm):
    class Meta:
        model = Designation
        fields = ['department', 'name', 'code', 'description', 'is_active']
        widgets = {
            'department': forms.Select(attrs={'class': SELECT_INPUT}),
            'name': forms.TextInput(attrs={'class': TEXT_INPUT}),
            'code': forms.TextInput(attrs={'class': TEXT_INPUT}),
            'description': forms.Textarea(attrs={'class': TEXT_INPUT, 'rows': 2}),
            'is_active': forms.CheckboxInput(attrs={'class': CHECKBOX_INPUT}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['department'].queryset = Department.objects.filter(is_active=True).order_by('name')


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

    # Optional: for Suspension
    suspension_start_date = forms.DateField(
        label='Suspension Start Date',
        widget=forms.DateInput(attrs={'class': TEXT_INPUT, 'type': 'date'}),
        required=False,
    )
    suspension_end_date = forms.DateField(
        label='Suspension End Date / Expiry Date',
        widget=forms.DateInput(attrs={'class': TEXT_INPUT, 'type': 'date'}),
        required=False,
    )
    auto_reactivate = forms.BooleanField(
        label='Auto-reactivate when expired',
        required=False,
        widget=forms.CheckboxInput(attrs={'class': CHECKBOX_INPUT}),
    )

    def __init__(self, *args, to_status=None, **kwargs):
        super().__init__(*args, **kwargs)
        if to_status:
            self.fields['to_status'].initial = to_status
            # Show dept/desig only for relevant transitions
            if to_status not in ('promoted', 'transferred', 'demoted'):
                del self.fields['new_department']
                del self.fields['new_designation']
            if to_status != 'suspended':
                del self.fields['suspension_start_date']
                del self.fields['suspension_end_date']
                del self.fields['auto_reactivate']
            else:
                self.fields['suspension_start_date'].required = True

    def clean(self):
        cleaned_data = super().clean()
        to_status = cleaned_data.get('to_status')
        reason = cleaned_data.get('reason')

        # Enforce reason requirement for inactive, suspended, archived
        if to_status in ('inactive', 'suspended', 'archived') and not reason:
            self.add_error('reason', 'A reason is mandatory for this status change.')

        if to_status == 'suspended':
            start_date = cleaned_data.get('suspension_start_date')
            end_date = cleaned_data.get('suspension_end_date')
            if start_date and end_date and end_date < start_date:
                raise forms.ValidationError("Suspension end date must be greater than or equal to start date.")
        return cleaned_data


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
            raise forms.ValidationError("An employee with this Employee ID already exists.")
        from apps.employees.models import EmployeeProfile
        qs_prof = EmployeeProfile.objects.filter(employee_id=emp_num)
        if self.instance and self.instance.pk and hasattr(self.instance, 'legacy_profile') and self.instance.legacy_profile:
            qs_prof = qs_prof.exclude(pk=self.instance.legacy_profile.pk)
        if qs_prof.exists():
            raise forms.ValidationError("An employee profile with this Employee ID already exists.")
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
            from apps.employees.models import EmployeeProfile
            qs_prof = EmployeeProfile.objects.filter(phone=phone)
            if self.instance and self.instance.pk and hasattr(self.instance, 'legacy_profile') and self.instance.legacy_profile:
                qs_prof = qs_prof.exclude(pk=self.instance.legacy_profile.pk)
            if qs_prof.exists():
                raise forms.ValidationError("An employee profile with this phone number already exists.")
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


WIZARD_PAYMENT_METHOD_CHOICES = [
    ('bank', 'Bank Transfer'),
    ('mfs', 'Mobile Financial Service'),
    ('cash', 'Cash'),
    ('split', 'Multiple Methods (Bank & MFS)'),
]


class WizardStep3Form(forms.ModelForm):
    bank = forms.ModelChoiceField(
        queryset=Bank.objects.filter(is_active=True),
        required=False,
        empty_label="-- Choose Bank --",
        widget=forms.Select(attrs={'class': SELECT_INPUT})
    )
    bank_name = forms.CharField(
        required=False,
        label="Bank Name",
        widget=forms.Select(choices=BANGLADESH_BANK_CHOICES, attrs={'class': SELECT_INPUT})
    )
    branch = forms.ModelChoiceField(
        queryset=BankBranch.objects.filter(is_active=True).select_related('bank'),
        required=False,
        empty_label="-- Choose Branch --",
        widget=forms.Select(attrs={'class': SELECT_INPUT})
    )
    account_holder_name = forms.CharField(
        max_length=255,
        required=False,
        label="AC Name",
        widget=forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'Account Holder Name as in Bank Record'})
    )
    routing_number = forms.CharField(
        max_length=9,
        required=False,
        label="Routing Number",
        widget=forms.TextInput(attrs={'class': TEXT_INPUT, 'readonly': 'readonly', 'placeholder': '9-digit routing number'})
    )
    mfs_provider = forms.ChoiceField(
        choices=[('', '-- Choose Banking Method --'), ('bkash', 'bKash'), ('nagad', 'Nagad'), ('rocket', 'Rocket')],
        required=False,
        label="Banking Method",
        widget=forms.Select(attrs={'class': SELECT_INPUT})
    )
    wallet_number = forms.CharField(
        max_length=20,
        required=False,
        label="Transaction Number",
        widget=forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'e.g. 017XXXXXXXX'})
    )
    transaction_id = forms.CharField(
        max_length=100,
        required=False,
        label="Transaction ID",
        widget=forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'e.g. TXN987654321 / Ref ID'})
    )
    has_secondary = forms.BooleanField(
        required=False,
        widget=forms.HiddenInput()
    )

    @property
    def bank_groups(self):
        return BANGLADESH_BANK_GROUPS

    class Meta:
        model = Employee
        fields = [
            'basic_salary', 'salary_structure', 'bank_name', 'bank_account',
            'payment_method', 'tax_profile', 'pf_enabled', 'overtime_policy'
        ]
        widgets = {
            'basic_salary': forms.NumberInput(attrs={'class': TEXT_INPUT, 'step': '0.01', 'placeholder': '0.00'}),
            'salary_structure': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'e.g. Executive Grade B'}),
            'bank_name': forms.Select(choices=BANGLADESH_BANK_CHOICES, attrs={'class': SELECT_INPUT}),
            'bank_account': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'AC Number'}),
            'payment_method': forms.Select(attrs={'class': SELECT_INPUT}),
            'tax_profile': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'TIN / Tax Region'}),
            'pf_enabled': forms.CheckboxInput(attrs={'class': CHECKBOX_INPUT}),
            'overtime_policy': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'e.g. Standard 1.5x'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['payment_method'].choices = WIZARD_PAYMENT_METHOD_CHOICES
        self.fields['bank_account'].label = 'AC Number'
        if self.instance and self.instance.pk:
            dest = None
            try:
                dest = getattr(self.instance, 'payment_destination', None)
            except Exception as e:
                logger.warning("Could not load payment destination for employee %s: %s", self.instance.pk, e)
            if dest:
                if dest.payment_type == 'mfs':
                    self.fields['payment_method'].initial = 'mfs'
                    self.fields['mfs_provider'].initial = dest.mfs_provider
                    self.fields['wallet_number'].initial = dest.get_wallet_number()
                    self.fields['transaction_id'].initial = dest.notes
                    self.fields['bank_account'].initial = dest.get_wallet_number()
                elif dest.payment_type == 'cash':
                    self.fields['payment_method'].initial = 'cash'
                elif dest.payment_type == 'bank':
                    self.fields['payment_method'].initial = 'bank'
                    if dest.bank_id:
                        self.fields['bank'].initial = dest.bank_id
                    if dest.branch_id:
                        self.fields['branch'].initial = dest.branch_id
                    if dest.account_holder_name:
                        self.fields['account_holder_name'].initial = dest.account_holder_name
                    if dest.routing_number:
                        self.fields['routing_number'].initial = dest.routing_number
                    if dest.bank_name and not self.initial.get('bank_name'):
                        self.fields['bank_name'].initial = dest.bank_name
                    raw_acc = dest.get_account_number()
                    if raw_acc and not self.initial.get('bank_account'):
                        self.fields['bank_account'].initial = raw_acc
                elif dest.payment_type == 'split':
                    self.fields['payment_method'].initial = 'split'
                    self.fields['has_secondary'].initial = True
                    if dest.bank_id:
                        self.fields['bank'].initial = dest.bank_id
                    if dest.branch_id:
                        self.fields['branch'].initial = dest.branch_id
                    if dest.account_holder_name:
                        self.fields['account_holder_name'].initial = dest.account_holder_name
                    if dest.routing_number:
                        self.fields['routing_number'].initial = dest.routing_number
                    if dest.bank_name and not self.initial.get('bank_name'):
                        self.fields['bank_name'].initial = dest.bank_name
                    raw_acc = dest.get_account_number()
                    if raw_acc and not self.initial.get('bank_account'):
                        self.fields['bank_account'].initial = raw_acc
                    self.fields['mfs_provider'].initial = dest.mfs_provider
                    self.fields['wallet_number'].initial = dest.get_wallet_number()
                    self.fields['transaction_id'].initial = dest.notes

            primary_acc = getattr(self.instance, 'primary_bank_account', None)
            if primary_acc and not self.fields['bank'].initial:
                self.fields['bank'].initial = primary_acc.bank_id
                self.fields['branch'].initial = primary_acc.branch_id
                self.fields['account_holder_name'].initial = primary_acc.account_holder_name
                self.fields['routing_number'].initial = primary_acc.routing_number
                if not self.initial.get('bank_name'):
                    self.fields['bank_name'].initial = resolve_canonical_bank_name(primary_acc.bank.name)
                if not self.initial.get('bank_account'):
                    self.fields['bank_account'].initial = primary_acc.get_account_number()
            elif self.instance.bank_name and not self.fields['bank_name'].initial:
                canonical = resolve_canonical_bank_name(self.instance.bank_name)
                self.fields['bank_name'].initial = canonical
                matched_bank = Bank.objects.filter(
                    Q(name__iexact=self.instance.bank_name) |
                    Q(name__iexact=canonical) |
                    Q(short_name__iexact=self.instance.bank_name) |
                    Q(code__iexact=self.instance.bank_name)
                ).first()
                if matched_bank:
                    self.fields['bank'].initial = matched_bank.pk
            if not self.fields['account_holder_name'].initial and hasattr(self.instance, 'get_full_name'):
                self.fields['account_holder_name'].initial = self.instance.get_full_name()

    def clean(self):
        cleaned_data = super().clean()
        payment_method = cleaned_data.get('payment_method') or 'bank'
        raw_has_sec = self.data.get('has_secondary')
        has_secondary = bool(raw_has_sec in ('true', 'True', True, '1', 1) or cleaned_data.get('has_secondary'))
        cleaned_data['has_secondary'] = has_secondary

        bank = cleaned_data.get('bank')
        branch = cleaned_data.get('branch')
        raw_bank_name = cleaned_data.get('bank_name') or ''
        bank_name = resolve_canonical_bank_name(raw_bank_name)
        if bank_name:
            cleaned_data['bank_name'] = bank_name
        bank_account = cleaned_data.get('bank_account') or ''
        holder_name = cleaned_data.get('account_holder_name') or ''
        mfs_provider = cleaned_data.get('mfs_provider') or ''
        wallet_number = cleaned_data.get('wallet_number') or ''
        transaction_id = cleaned_data.get('transaction_id') or ''

        is_multi_method = (payment_method == 'split' or has_secondary)

        # 1. Evaluate Bank requirements
        need_bank_validation = (payment_method in ('bank', 'split')) or (is_multi_method and (bank or bank_name or bank_account))
        if need_bank_validation:
            # Auto-match legacy bank_name if bank not explicitly selected
            if not bank and bank_name:
                matched_bank = Bank.objects.filter(
                    Q(name__iexact=bank_name) |
                    Q(short_name__iexact=bank_name) |
                    Q(code__iexact=bank_name),
                    is_active=True
                ).first()
                if not matched_bank:
                    tokens = bank_name.lower().replace("ltd", "").replace("limited", "").replace("plc", "").replace("bank", "").strip()
                    if tokens:
                        matched_bank = Bank.objects.filter(name__icontains=tokens, is_active=True).first()
                if matched_bank:
                    bank = matched_bank
                    cleaned_data['bank'] = bank
                    if not branch:
                        branch = bank.branches.filter(is_active=True).first()
                        cleaned_data['branch'] = branch

            # Enforce server-side validation for bank transfers
            if not bank and not bank_name:
                self.add_error('bank', 'Bank selection is required for bank transfer disbursements.')
                self.add_error('bank_name', 'Bank selection is required for bank transfer disbursements.')
            if not bank_account:
                self.add_error('bank_account', 'AC Number is required for bank transfer disbursements.')
            else:
                cleaned_acc = normalize_account_number(bank_account)
                if len(cleaned_acc) < 6 or len(cleaned_acc) > 30:
                    self.add_error('bank_account', 'Bank AC number must be between 6 and 30 characters.')
                elif not re.match(r"^[A-Za-z0-9]+$", cleaned_acc):
                    self.add_error('bank_account', 'Bank AC number must contain only alphanumeric characters.')
                cleaned_data['bank_account'] = cleaned_acc

            if bank and branch:
                if branch.bank_id != bank.id:
                    self.add_error('branch', 'Selected branch does not belong to the submitted bank.')
                cleaned_data['routing_number'] = branch.routing_number
                if not cleaned_data.get('bank_name'):
                    cleaned_data['bank_name'] = bank.name

        # 2. Evaluate MFS requirements
        need_mfs_validation = (payment_method in ('mfs', 'mobile', 'split')) or (is_multi_method and (mfs_provider or wallet_number))
        if need_mfs_validation:
            if not mfs_provider:
                self.add_error('mfs_provider', 'Banking Method (bKash, Nagad, or Rocket) is required.')
            elif mfs_provider not in ['bkash', 'nagad', 'rocket']:
                self.add_error('mfs_provider', 'Choose a valid Banking Method: bKash, Nagad, or Rocket.')

            target_wallet = wallet_number or (bank_account if payment_method in ('mfs', 'mobile') else '')
            if not target_wallet:
                self.add_error('wallet_number', 'Transaction Number (mobile wallet number) is required.')
            else:
                cleaned_wallet = target_wallet.strip().replace(" ", "").replace("-", "")
                if not re.match(r"^01[3-9]\d{8,9}$", cleaned_wallet):
                    self.add_error('wallet_number', 'Invalid Bangladeshi wallet number. Must start with 01 and contain 11 digits (or 12 for Rocket).')
                cleaned_data['wallet_number'] = cleaned_wallet
                if payment_method in ('mfs', 'mobile'):
                    cleaned_data['bank_account'] = cleaned_wallet

            cleaned_data['transaction_id'] = transaction_id.strip()

        # 3. Single vs Multi Method Routing
        if not is_multi_method:
            if payment_method in ('mfs', 'mobile'):
                cleaned_data['payment_method'] = 'mfs'
                cleaned_data['bank'] = None
                cleaned_data['bank_name'] = ''
                cleaned_data['branch'] = None
                cleaned_data['routing_number'] = ''
            elif payment_method == 'bank':
                cleaned_data['payment_method'] = 'bank'
                cleaned_data['mfs_provider'] = ''
                cleaned_data['wallet_number'] = ''
                cleaned_data['transaction_id'] = ''
            elif payment_method == 'cash':
                cleaned_data['payment_method'] = 'cash'
                cleaned_data['bank'] = None
                cleaned_data['bank_name'] = ''
                cleaned_data['branch'] = None
                cleaned_data['routing_number'] = ''
                cleaned_data['bank_account'] = ''
                cleaned_data['account_holder_name'] = ''
                cleaned_data['mfs_provider'] = ''
                cleaned_data['wallet_number'] = ''
                cleaned_data['transaction_id'] = ''
        else:
            cleaned_data['payment_method'] = 'split'

        return cleaned_data

    def save(self, commit=True):
        employee = super().save(commit=commit)
        if commit and employee.pk:
            from apps.payroll.payment_destination_service import PayrollPaymentDestinationService
            pm = self.cleaned_data.get('payment_method') or 'bank'
            dest_data = {
                'bank': self.cleaned_data.get('bank'),
                'bank_name': self.cleaned_data.get('bank_name'),
                'branch': self.cleaned_data.get('branch'),
                'account_holder_name': (self.cleaned_data.get('account_holder_name') or employee.get_full_name()).strip(),
                'account_number': self.cleaned_data.get('bank_account'),
                'routing_number': self.cleaned_data.get('routing_number'),
                'mfs_provider': self.cleaned_data.get('mfs_provider'),
                'wallet_number': self.cleaned_data.get('wallet_number') or self.cleaned_data.get('bank_account'),
                'transaction_id': self.cleaned_data.get('transaction_id') or '',
                'notes': self.cleaned_data.get('transaction_id') or '',
            }
            try:
                PayrollPaymentDestinationService.save_destination(
                    employee=employee,
                    payment_type=pm,
                    data=dest_data
                )
            except Exception as e:
                logger.warning("Could not sync payment destination during WizardStep3Form save: %s", e)
        return employee



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
    custom_permissions = forms.CharField(required=False, widget=forms.HiddenInput())

    def __init__(self, *args, employee=None, actor=None, **kwargs):
        self.employee = employee
        self.actor = actor
        super().__init__(*args, **kwargs)

        assignable_qs = RoleAssignmentService.get_assignable_roles_queryset(actor=self.actor)
        self.fields['roles'].queryset = assignable_qs

        from apps.accounts.rbac_registry import RBACRegistryService
        from apps.accounts.rbac_models import UserPermissionOverride
        import json
        self.fields['roles'].available_permissions = RBACRegistryService.get_canonical_permissions_catalog()

        if employee and employee.user:
            self.fields['login_email'].initial = employee.user.email
            self.fields['data_scope'].initial = employee.data_scope
            self.fields['mfa_required'].initial = employee.mfa_required
            assigned_role_ids = list(UserRoleAssignment.objects.filter(user=employee.user).values_list('role_id', flat=True))
            if not assigned_role_ids and employee.user.role:
                role_match = assignable_qs.filter(code=employee.user.role).first()
                if role_match:
                    assigned_role_ids = [role_match.pk]
            self.fields['roles'].initial = assigned_role_ids
            self.initial['roles'] = assigned_role_ids

            existing_overrides = UserPermissionOverride.objects.filter(user=employee.user).select_related('permission')
            if existing_overrides.exists():
                self.fields['custom_permissions'].initial = json.dumps([
                    {
                        'permission_id': ov.permission_id,
                        'codename': ov.permission.codename,
                        'name': ov.permission.name or ov.permission.codename,
                        'is_granted': ov.is_granted,
                        'data_scope': ov.data_scope or ''
                    }
                    for ov in existing_overrides
                ])
        elif not self.initial.get('roles'):
            default_role = assignable_qs.filter(code='staff').first()
            if default_role:
                self.initial['roles'] = [default_role.pk]
                self.fields['roles'].initial = [default_role.pk]

    def clean_login_email(self):
        email = self.cleaned_data.get('login_email', '').strip()
        qs = User.objects.filter(email__iexact=email)
        if self.employee and self.employee.user:
            qs = qs.exclude(pk=self.employee.user.pk)
        if qs.exists():
            raise forms.ValidationError("A user account with this login email already exists.")
        return email

    def clean_roles(self):
        roles = list(self.cleaned_data.get('roles') or [])
        if not roles:
            raise forms.ValidationError("Please select at least one authorized role.")

        for r in roles:
            if not r.is_active:
                raise forms.ValidationError(f"Role '{r.name}' is inactive and cannot be assigned.")
            if r.code == 'system_owner' or r.is_system_protected:
                raise forms.ValidationError("The System Owner role cannot be assigned via employee forms.")

        if not self.actor or not self.actor.is_authenticated:
            raise forms.ValidationError("You must be logged in with authorization to assign roles.")

        try:
            RoleAssignmentService.validate_role_authority(self.actor, roles)
        except PermissionDenied as e:
            raise forms.ValidationError(str(e))
        return roles

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
            elif len(p1) < 6:
                self.add_error('password1', "Password must be at least 6 characters long.")
        return cleaned_data

    @transaction.atomic
    def save(self):
        cleaned_data = self.cleaned_data
        email = cleaned_data['login_email']
        p1 = cleaned_data.get('password1')
        roles = cleaned_data['roles']
        data_scope = cleaned_data['data_scope']
        mfa_required = cleaned_data['mfa_required']

        compat_role = RoleAssignmentService.compute_compatibility_persona(roles)

        user = self.employee.user if self.employee else None
        if not user:
            # Check if user with email exists
            user = User.objects.filter(email__iexact=email).first()

        if not user:
            user = User.objects.create_user(
                email=email,
                phone=self.employee.phone or None,
                password=p1,
                role=compat_role
            )
        else:
            if p1:
                user.set_password(p1)
                user.save()
                from apps.accounts.models import UserSession
                from django.contrib.sessions.models import Session
                from apps.audit.services import AuditService
                from apps.notifications.models import log_audit
                from django.utils import timezone
                now = timezone.now()
                for sess in UserSession.objects.filter(user=user, is_active=True):
                    sess.is_active = False
                    sess.logout_time = now
                    sess.save(update_fields=['is_active', 'logout_time'])
                    if sess.session_key:
                        Session.objects.filter(session_key=sess.session_key).delete()
                AuditService.log_event(
                    actor=self.actor if (self.actor and self.actor.is_authenticated) else None,
                    action="password_change",
                    instance=user,
                    module="employees",
                    object_type="CustomUser",
                    object_id=str(user.pk),
                    object_label=user.email or user.phone,
                    reason=f"Password updated via wizard for employee {self.employee.get_full_name()}"
                )
                log_audit(
                    actor=self.actor if (self.actor and self.actor.is_authenticated) else None,
                    action="password_change",
                    target=user,
                    summary=f"Password updated via wizard for employee {self.employee.get_full_name()}"
                )

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

        # Atomic role diff assignment preserving protected roles
        RoleAssignmentService.sync_user_roles(
            user=user,
            target_roles=roles,
            actor=self.actor,
            preserve_protected=True
        )

        # Atomic custom permissions override sync
        custom_perms_raw = cleaned_data.get('custom_permissions')
        if custom_perms_raw:
            try:
                import json
                from apps.accounts.rbac_models import UserPermissionOverride, Permission
                from apps.accounts.rbac_registry import RBACRegistryService
                overrides_data = json.loads(custom_perms_raw) if isinstance(custom_perms_raw, str) else custom_perms_raw
                if isinstance(overrides_data, list):
                    saved_perm_ids = set()
                    for item in overrides_data:
                        codename = item.get('codename')
                        perm_id = item.get('permission_id')
                        perm = Permission.objects.filter(pk=perm_id).first() if perm_id else None
                        if not perm and codename:
                            perm = RBACRegistryService.ensure_permission(codename)
                        if perm:
                            is_granted = bool(item.get('is_granted', True))
                            scope_val = item.get('data_scope') or None
                            UserPermissionOverride.objects.update_or_create(
                                user=user,
                                permission=perm,
                                defaults={'is_granted': is_granted, 'data_scope': scope_val}
                            )
                            saved_perm_ids.add(perm.pk)
                    UserPermissionOverride.objects.filter(user=user).exclude(permission_id__in=saved_perm_ids).delete()
                    RoleAssignmentService.invalidate_user_permissions(user)
            except Exception:
                pass
        elif custom_perms_raw == '[]':
            from apps.accounts.rbac_models import UserPermissionOverride
            UserPermissionOverride.objects.filter(user=user).delete()
            RoleAssignmentService.invalidate_user_permissions(user)

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



