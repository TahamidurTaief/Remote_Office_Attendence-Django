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

