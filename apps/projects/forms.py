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

from .models import TaskTemplate, TaskTemplateItem, ProjectTask, DailyProgressLog, ManpowerDeployment, ProjectMaterial

class TaskTemplateForm(forms.ModelForm):
    class Meta:
        model = TaskTemplate
        fields = ['name', 'description']
        widgets = {
            'name': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'e.g. Standard Commercial HVAC Install'}),
            'description': forms.Textarea(attrs={'class': TEXT_INPUT, 'rows': 3, 'placeholder': 'Template description...'}),
        }

class TaskTemplateItemForm(forms.ModelForm):
    class Meta:
        model = TaskTemplateItem
        fields = ['order', 'activity', 'default_responsible_role', 'default_duration_days']
        widgets = {
            'order': forms.NumberInput(attrs={'class': TEXT_INPUT, 'min': 1, 'placeholder': 'Order'}),
            'activity': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'Activity Name'}),
            'default_responsible_role': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'Role (e.g. Project Manager)'}),
            'default_duration_days': forms.NumberInput(attrs={'class': TEXT_INPUT, 'min': 1, 'placeholder': 'Duration (days)'}),
        }

class ProjectTaskForm(forms.ModelForm):
    class Meta:
        model = ProjectTask
        fields = [
            'order', 'activity', 'responsible_person',
            'planned_start', 'planned_finish', 'duration_days',
            'status', 'remarks'
        ]
        widgets = {
            'order': forms.NumberInput(attrs={'class': TEXT_INPUT, 'min': 1}),
            'activity': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'e.g. Duct Installation'}),
            'responsible_person': forms.Select(attrs={'class': SELECT_INPUT}),
            'planned_start': forms.DateInput(attrs={'class': TEXT_INPUT, 'type': 'date'}),
            'planned_finish': forms.DateInput(attrs={'class': TEXT_INPUT, 'type': 'date'}),
            'duration_days': forms.NumberInput(attrs={'class': TEXT_INPUT, 'min': 1}),
            'status': forms.Select(attrs={'class': SELECT_INPUT}),
            'remarks': forms.Textarea(attrs={'class': TEXT_INPUT, 'rows': 2, 'placeholder': 'Remarks...'}),
        }


class DailyProgressLogForm(forms.ModelForm):
    class Meta:
        model = DailyProgressLog
        fields = ['date', 'planned_work', 'completed_work', 'manpower_count', 'delay_reason', 'supervisor_name']
        widgets = {
            'date': forms.DateInput(attrs={'class': TEXT_INPUT, 'type': 'date'}),
            'planned_work': forms.Textarea(attrs={'class': TEXT_INPUT, 'rows': 3, 'placeholder': 'Planned work details...'}),
            'completed_work': forms.Textarea(attrs={'class': TEXT_INPUT, 'rows': 3, 'placeholder': 'Completed work details...'}),
            'manpower_count': forms.NumberInput(attrs={'class': TEXT_INPUT, 'min': 0, 'placeholder': 'Manpower count'}),
            'delay_reason': forms.Textarea(attrs={'class': TEXT_INPUT, 'rows': 2, 'placeholder': 'Any delay reason...'}),
            'supervisor_name': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'Supervisor name'}),
        }

    def clean_manpower_count(self):
        count = self.cleaned_data.get('manpower_count')
        if count is not None and count < 0:
            raise forms.ValidationError("Manpower count cannot be negative.")
        return count


class ManpowerDeploymentForm(forms.ModelForm):
    class Meta:
        model = ManpowerDeployment
        fields = ['date', 'trade', 'required_count', 'present_count']
        widgets = {
            'date': forms.DateInput(attrs={'class': TEXT_INPUT, 'type': 'date'}),
            'trade': forms.Select(attrs={'class': SELECT_INPUT}),
            'required_count': forms.NumberInput(attrs={'class': TEXT_INPUT, 'min': 0, 'placeholder': 'Required count'}),
            'present_count': forms.NumberInput(attrs={'class': TEXT_INPUT, 'min': 0, 'placeholder': 'Present count (optional)'}),
        }

    def clean_required_count(self):
        count = self.cleaned_data.get('required_count')
        if count is not None and count < 0:
            raise forms.ValidationError("Required count cannot be negative.")
        return count

    def clean_present_count(self):
        count = self.cleaned_data.get('present_count')
        if count is not None and count < 0:
            raise forms.ValidationError("Present count cannot be negative.")
        return count


class ProjectMaterialForm(forms.ModelForm):
    class Meta:
        model = ProjectMaterial
        fields = ['material_name', 'unit', 'required_qty', 'received_qty', 'remarks']
        widgets = {
            'material_name': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'Material name'}),
            'unit': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'e.g. pcs, meter, kg'}),
            'required_qty': forms.NumberInput(attrs={'class': TEXT_INPUT, 'min': 0, 'placeholder': 'Required qty', 'step': '0.01'}),
            'received_qty': forms.NumberInput(attrs={'class': TEXT_INPUT, 'min': 0, 'placeholder': 'Received qty', 'step': '0.01'}),
            'remarks': forms.Textarea(attrs={'class': TEXT_INPUT, 'rows': 2, 'placeholder': 'Remarks...'}),
        }

    def clean_required_qty(self):
        qty = self.cleaned_data.get('required_qty')
        if qty is not None and qty < 0:
            raise forms.ValidationError("Required quantity cannot be negative.")
        return qty

    def clean_received_qty(self):
        qty = self.cleaned_data.get('received_qty')
        if qty is not None and qty < 0:
            raise forms.ValidationError("Received quantity cannot be negative.")
        return qty




