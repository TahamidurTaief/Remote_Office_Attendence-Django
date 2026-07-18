from django import forms
from .models import ScheduleEvent
from apps.employees.models import EmployeeProfile
from apps.projects.models import Project

TEXT_INPUT = (
    "w-full px-3 py-2.5 border border-gray-200 rounded text-sm text-gray-800 "
    "focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent "
    "placeholder:text-gray-400 transition-colors"
)

SELECT_INPUT = (
    "w-full px-3 py-1.5 border border-gray-200 rounded text-sm text-gray-800 bg-white "
    "focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
)

class ScheduleEventForm(forms.ModelForm):
    class Meta:
        model = ScheduleEvent
        fields = [
            'title', 'description', 'date', 'start_time', 'end_time',
            'event_type', 'assigned_to', 'project'
        ]
        widgets = {
            'title': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'Event Title'}),
            'description': forms.Textarea(attrs={'class': TEXT_INPUT, 'placeholder': 'Description (optional)', 'rows': 3}),
            'date': forms.DateInput(format='%Y-%m-%d', attrs={'class': TEXT_INPUT, 'type': 'date'}),
            'start_time': forms.TimeInput(attrs={'class': TEXT_INPUT, 'type': 'time'}),
            'end_time': forms.TimeInput(attrs={'class': TEXT_INPUT, 'type': 'time'}),
            'event_type': forms.Select(attrs={'class': SELECT_INPUT}),
            'assigned_to': forms.SelectMultiple(attrs={'class': SELECT_INPUT, 'size': 5}),
            'project': forms.Select(attrs={'class': SELECT_INPUT}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Filter profiles to active ones
        self.fields['assigned_to'].queryset = EmployeeProfile.objects.filter(is_active=True)
        self.fields['assigned_to'].label_from_instance = lambda obj: f"{obj.full_name} ({obj.employee_id})"
        self.fields['project'].queryset = Project.objects.all()
