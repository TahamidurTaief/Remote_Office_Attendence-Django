from django import forms
from .models import LeaveRequest, LeaveType, LeaveBalance

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

def validate_leave_overlap(employee, start_date, end_date, instance=None, error_msg="This employee has an existing leave request that overlaps with these dates."):
    if start_date and end_date:
        if end_date < start_date:
            raise forms.ValidationError("End date cannot be before start date.")
        if start_date.year != end_date.year:
            raise forms.ValidationError("Leave request cannot span across multiple calendar years. Please submit separate requests for each year.")
        if employee:
            overlap_qs = LeaveRequest.objects.filter(
                employee=employee,
                start_date__lte=end_date,
                end_date__gte=start_date
            ).exclude(status__in=['rejected', 'cancelled'])
            if instance and instance.pk:
                overlap_qs = overlap_qs.exclude(pk=instance.pk)
            if overlap_qs.exists():
                raise forms.ValidationError(error_msg)


class LeaveRequestForm(forms.ModelForm):
    class Meta:
        model = LeaveRequest
        fields = ['leave_type', 'start_date', 'end_date', 'reason']
        widgets = {
            'leave_type': forms.Select(attrs={'class': SELECT_INPUT}),
            'start_date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': TEXT_INPUT}),
            'end_date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': TEXT_INPUT}),
            'reason': forms.Textarea(attrs={'rows': 3, 'class': TEXTAREA_INPUT, 'placeholder': 'Reason for leave request...'}),
        }

    def __init__(self, *args, **kwargs):
        self.employee = kwargs.pop('employee', None)
        super().__init__(*args, **kwargs)
        self.projected_remaining = None
        if self.instance and self.instance.pk and self.instance.leave_type_id:
            from django.db.models import Q
            self.fields['leave_type'].queryset = LeaveType.objects.filter(Q(is_active=True) | Q(pk=self.instance.leave_type_id))
        else:
            self.fields['leave_type'].queryset = LeaveType.objects.filter(is_active=True)

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get('start_date')
        end_date = cleaned_data.get('end_date')
        leave_type = cleaned_data.get('leave_type')

        validate_leave_overlap(
            employee=self.employee,
            start_date=start_date,
            end_date=end_date,
            instance=self.instance,
            error_msg="You have an existing leave request that overlaps with these dates."
        )

        if start_date and end_date:
            number_of_days = (end_date - start_date).days + 1
            year = start_date.year

            if self.employee and leave_type:
                try:
                    balance = LeaveBalance.objects.get(
                        employee=self.employee,
                        leave_type=leave_type,
                        year=year
                    )
                    remaining = balance.remaining_days
                except LeaveBalance.DoesNotExist:
                    remaining = leave_type.default_days_per_year

                # Check if this is an edit of an existing approved request
                # (though usually employees only edit pending requests)
                if self.instance and self.instance.pk and self.instance.status == 'approved':
                    remaining += self.instance.number_of_days

                self.projected_remaining = remaining - number_of_days
        return cleaned_data

from apps.employees.models import EmployeeProfile

class LeaveTypeForm(forms.ModelForm):
    class Meta:
        model = LeaveType
        fields = ['name', 'category', 'default_days_per_year', 'is_default']
        widgets = {
            'name': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'e.g. Casual, Sick'}),
            'category': forms.Select(attrs={'class': SELECT_INPUT}),
            'default_days_per_year': forms.NumberInput(attrs={'class': TEXT_INPUT, 'min': 0}),
            'is_default': forms.CheckboxInput(attrs={'class': 'h-4 w-4 text-indigo-650 focus:ring-indigo-500 border-gray-305 rounded'}),
        }


class AdminAddLeaveForm(forms.ModelForm):
    employee = forms.ModelChoiceField(
        queryset=EmployeeProfile.objects.filter(is_active=True).order_by('full_name'),
        widget=forms.Select(attrs={'class': SELECT_INPUT}),
        label="Employee"
    )

    class Meta:
        model = LeaveRequest
        fields = ['employee', 'leave_type', 'start_date', 'end_date', 'reason', 'status']
        widgets = {
            'leave_type': forms.Select(attrs={'class': SELECT_INPUT}),
            'start_date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': TEXT_INPUT}),
            'end_date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': TEXT_INPUT}),
            'reason': forms.Textarea(attrs={'rows': 3, 'class': TEXTAREA_INPUT, 'placeholder': 'Reason for leave...'}),
            'status': forms.Select(attrs={'class': SELECT_INPUT}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and self.instance.leave_type_id:
            from django.db.models import Q
            self.fields['leave_type'].queryset = LeaveType.objects.filter(Q(is_active=True) | Q(pk=self.instance.leave_type_id))
        else:
            self.fields['leave_type'].queryset = LeaveType.objects.filter(is_active=True)

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get('start_date')
        end_date = cleaned_data.get('end_date')
        employee = cleaned_data.get('employee')

        validate_leave_overlap(
            employee=employee,
            start_date=start_date,
            end_date=end_date,
            instance=self.instance,
            error_msg="This employee has an existing leave request that overlaps with these dates."
        )
        return cleaned_data


class AdminLeaveRequestRescheduleForm(forms.ModelForm):
    class Meta:
        model = LeaveRequest
        fields = ['leave_type', 'start_date', 'end_date', 'reason']
        widgets = {
            'leave_type': forms.Select(attrs={'class': SELECT_INPUT}),
            'start_date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': TEXT_INPUT}),
            'end_date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': TEXT_INPUT}),
            'reason': forms.Textarea(attrs={'rows': 3, 'class': TEXTAREA_INPUT, 'placeholder': 'Reason for reschedule...'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and self.instance.leave_type_id:
            from django.db.models import Q
            self.fields['leave_type'].queryset = LeaveType.objects.filter(Q(is_active=True) | Q(pk=self.instance.leave_type_id))
        else:
            self.fields['leave_type'].queryset = LeaveType.objects.filter(is_active=True)

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get('start_date')
        end_date = cleaned_data.get('end_date')
        employee = getattr(self.instance, 'employee', None) if self.instance else None

        validate_leave_overlap(
            employee=employee,
            start_date=start_date,
            end_date=end_date,
            instance=self.instance,
            error_msg="This employee has an existing leave request that overlaps with these dates."
        )
        return cleaned_data


