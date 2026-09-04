from django import forms
from apps.attendance.models import Attendance


TEXT_INPUT = (
    "w-full px-3 py-2.5 border border-gray-200 rounded-xl text-sm text-gray-800 "
    "focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent "
    "placeholder:text-gray-400 transition-colors"
)

SELECT_INPUT = (
    "w-full px-3 py-1.5 border border-gray-200 rounded-xl text-sm text-gray-800 bg-white "
    "focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
)

TEXTAREA_INPUT = (
    "w-full px-3 py-2.5 border border-gray-200 rounded-xl text-sm text-gray-800 "
    "focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent "
    "placeholder:text-gray-400 resize-none"
)

class ManualAttendanceForm(forms.ModelForm):
    admin_override_reason = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 2, 'class': TEXTAREA_INPUT}),
        required=True,
        label="Admin Override Reason"
    )

    class Meta:
        model = Attendance
        fields = ['employee', 'date', 'check_in_time', 'check_out_time', 'type', 'status']
        widgets = {
            'employee': forms.Select(attrs={'class': SELECT_INPUT}),
            'date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': TEXT_INPUT}),
            'check_in_time': forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': TEXT_INPUT}),
            'check_out_time': forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': TEXT_INPUT}),
            'type': forms.Select(attrs={'class': SELECT_INPUT}),
            'status': forms.Select(attrs={'class': SELECT_INPUT}),
        }


from apps.leave.models import LeaveBalance

class AdminLeaveBalanceForm(forms.ModelForm):
    class Meta:
        model = LeaveBalance
        fields = ['total_days', 'used_days']
        widgets = {
            'total_days': forms.NumberInput(attrs={'class': TEXT_INPUT, 'min': 0}),
            'used_days': forms.NumberInput(attrs={'class': TEXT_INPUT, 'min': 0}),
        }


class AdminAttendanceForm(forms.ModelForm):
    class Meta:
        model = Attendance
        fields = ['employee', 'date', 'check_in_time', 'check_out_time', 'type', 'status', 'ot_status', 'is_policy_exception']
        widgets = {
            'employee': forms.Select(attrs={'class': SELECT_INPUT}),
            'date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': TEXT_INPUT}),
            'check_in_time': forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': TEXT_INPUT}),
            'check_out_time': forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': TEXT_INPUT}),
            'type': forms.Select(attrs={'class': SELECT_INPUT}),
            'status': forms.Select(attrs={'class': SELECT_INPUT}),
            'ot_status': forms.Select(attrs={'class': SELECT_INPUT}),
            'is_policy_exception': forms.CheckboxInput(attrs={'class': 'h-4 w-4 text-indigo-650 focus:ring-indigo-500 border-gray-305 rounded'}),
        }


import re
from django.db.models import Q
from django.core.exceptions import ValidationError
from apps.accounts.models import Role, UserRoleAssignment, RolePermission, CustomUser


def normalize_role_code(text):
    if not text:
        return ''
    cleaned = re.sub(r'[^a-zA-Z0-9_]+', '_', str(text).strip().lower())
    cleaned = re.sub(r'_+', '_', cleaned).strip('_')
    return cleaned


class DynamicRoleForm(forms.ModelForm):
    class Meta:
        model = Role
        fields = ['name', 'code', 'description', 'is_active']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Describe role responsibilities and access scope...'}),
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            has_assignments = self.instance.user_assignments.exists()
            has_permissions = self.instance.role_permissions.exists()
            self.code_locked = has_assignments or has_permissions or self.instance.is_system_protected
            if self.code_locked:
                self.fields['code'].disabled = True
                self.fields['code'].help_text = "Role code is locked once permissions or user assignments are attached."
            else:
                self.fields['code'].help_text = "Unique identifier (lowercase snake_case). Auto-generated from name if blank."
        else:
            self.code_locked = False
            self.fields['code'].required = False
            self.fields['code'].help_text = "Unique identifier (lowercase snake_case). Auto-generated from name if left blank."

    def clean_name(self):
        name = self.cleaned_data.get('name', '').strip()
        if not name:
            raise ValidationError("Role name is required.")
        # Check case-insensitive and spacing-collapsed name collision
        cleaned_spaces = re.sub(r'\s+', ' ', name).lower()
        all_roles = Role.objects.all()
        if self.instance and self.instance.pk:
            all_roles = all_roles.exclude(pk=self.instance.pk)
        for r in all_roles:
            if re.sub(r'\s+', ' ', r.name).lower() == cleaned_spaces:
                raise ValidationError("A role with this name already exists.")
        return name

    def clean_code(self):
        raw_code = self.data.get('code', '')
        if getattr(self, 'code_locked', False) and self.instance and self.instance.pk:
            if raw_code and normalize_role_code(raw_code) != normalize_role_code(self.instance.code):
                raise ValidationError("Role code cannot be modified once permissions or users are assigned.")
            return self.instance.code

        code = self.cleaned_data.get('code', '')
        name = self.cleaned_data.get('name', '')
        if not code:
            code = normalize_role_code(name)
        else:
            code = normalize_role_code(code)

        if not code:
            raise ValidationError("A valid alphanumeric role code could not be determined from the name.")

        if code == 'system_owner':
            raise ValidationError("The 'system_owner' role is reserved for bootstrap superusers and cannot be managed via UI.")

        if code == 'super_admin':
            if not self.user or not getattr(self.user, 'is_superuser', False):
                raise ValidationError("Only a System Owner (Django superuser) can create or configure the 'super_admin' role.")

        qs = Role.objects.filter(code__iexact=code)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            self.add_error('name', "This role name or code normalizes to an existing role identifier.")
            raise ValidationError(f"A role with code '{code}' already exists.")

        return code

    def clean(self):
        cleaned_data = super().clean()
        raw_code = self.data.get('code', '')
        if getattr(self, 'code_locked', False) and self.instance and self.instance.pk:
            if raw_code and normalize_role_code(raw_code) != normalize_role_code(self.instance.code):
                self.add_error('code', "Role code cannot be modified once permissions or users are assigned.")
        return cleaned_data

    def clean_is_active(self):
        is_active = self.cleaned_data.get('is_active', True)
        if self.instance and self.instance.pk:
            if self.instance.is_system_protected and not is_active:
                raise ValidationError("Protected System Owner role cannot be disabled.")

            if not is_active and self.instance.is_active:
                is_privileged = (
                    self.instance.code in ['admin', 'super_admin', 'system_owner'] or
                    self.instance.role_permissions.filter(
                        permission__codename='accounts.edit',
                        data_scope='global'
                    ).exists()
                )
                if is_privileged:
                    other_privileged_roles = Role.objects.filter(
                        is_active=True
                    ).exclude(pk=self.instance.pk).filter(
                        Q(code__in=['admin', 'super_admin', 'system_owner']) |
                        Q(role_permissions__permission__codename='accounts.edit', role_permissions__data_scope='global')
                    ).distinct()

                    active_privileged_users = UserRoleAssignment.objects.filter(
                        role__in=other_privileged_roles,
                        user__is_active=True
                    ).exists()

                    active_superusers = CustomUser.objects.filter(is_superuser=True, is_active=True).exists()

                    if not (active_privileged_users or active_superusers):
                        raise ValidationError("Cannot deactivate this role: it is the last effective privileged role (lockout prevention).")
        return is_active


