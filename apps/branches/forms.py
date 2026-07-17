from django import forms
from .models import Branch, Holiday


TEXT_INPUT = (
    "w-full px-3 py-2.5 border border-gray-200 rounded-xl text-sm text-gray-800 "
    "focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent "
    "placeholder:text-gray-400 transition-colors"
)

SELECT_INPUT = (
    "w-full px-3 py-2.5 border border-gray-200 rounded-xl text-sm text-gray-800 bg-white "
    "focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
)

TEXTAREA_INPUT = (
    "w-full px-3 py-2.5 border border-gray-200 rounded-xl text-sm text-gray-800 "
    "focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent "
    "placeholder:text-gray-400 resize-none"
)

class BranchForm(forms.ModelForm):
    class Meta:
        model = Branch
        fields = ['name', 'address', 'latitude', 'longitude', 'radius_meters', 'wifi_ip', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': TEXT_INPUT}),
            'address': forms.Textarea(attrs={'class': TEXTAREA_INPUT, 'rows': 3}),
            'latitude': forms.NumberInput(attrs={'class': TEXT_INPUT, 'step': '0.000001'}),
            'longitude': forms.NumberInput(attrs={'class': TEXT_INPUT, 'step': '0.000001'}),
            'radius_meters': forms.NumberInput(attrs={'class': TEXT_INPUT}),
            'wifi_ip': forms.TextInput(attrs={'class': TEXT_INPUT}),
            'is_active': forms.CheckboxInput(attrs={'class': 'h-4 w-4 text-indigo-600 focus:ring-indigo-500 border-gray-300 rounded'}),
        }

class HolidayForm(forms.ModelForm):
    class Meta:
        model = Holiday
        fields = ['name', 'date', 'branch']
        widgets = {
            'name': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'Holiday Name'}),
            'date': forms.DateInput(attrs={'class': TEXT_INPUT, 'type': 'date'}),
            'branch': forms.Select(attrs={'class': SELECT_INPUT}),
        }
        labels = {
            'branch': 'Branch (Leave empty for all branches)',
        }
