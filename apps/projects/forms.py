from django import forms
from .models import Project

TEXT_INPUT = (
    "w-full px-3 py-2.5 border border-gray-200 rounded-xl text-sm text-gray-800 "
    "focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent "
    "placeholder:text-gray-400 transition-colors"
)

SELECT_INPUT = (
    "w-full px-3 py-2.5 border border-gray-200 rounded-xl text-sm text-gray-800 bg-white "
    "focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
)

class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = [
            'name', 'client_name', 'consultant', 'main_contractor',
            'location', 'project_manager', 'site_engineer',
            'hvac_capacity_tr', 'system_type', 'start_date',
            'completion_date', 'status', 'progress_percent', 'branch'
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'Project Name'}),
            'client_name': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'Client Name'}),
            'consultant': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'Consultant Name'}),
            'main_contractor': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'Main Contractor'}),
            'location': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'Project Location'}),
            'project_manager': forms.Select(attrs={'class': SELECT_INPUT}),
            'site_engineer': forms.Select(attrs={'class': SELECT_INPUT}),
            'hvac_capacity_tr': forms.NumberInput(attrs={'class': TEXT_INPUT, 'placeholder': 'e.g. 150.00', 'step': '0.01'}),
            'system_type': forms.Select(attrs={'class': SELECT_INPUT}),
            'start_date': forms.DateInput(attrs={'class': TEXT_INPUT, 'type': 'date'}),
            'completion_date': forms.DateInput(attrs={'class': TEXT_INPUT, 'type': 'date'}),
            'status': forms.Select(attrs={'class': SELECT_INPUT}),
            'progress_percent': forms.NumberInput(attrs={'class': TEXT_INPUT, 'min': '0', 'max': '100', 'placeholder': '0'}),
            'branch': forms.Select(attrs={'class': SELECT_INPUT}),
        }
