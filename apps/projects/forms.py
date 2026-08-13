from django import forms
from .models import Project, ProjectType, TaskTemplate, TaskDependency

TEXT_INPUT = (
    "w-full px-3 py-2.5 border border-gray-200 rounded-xl text-sm text-gray-800 "
    "focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent "
    "placeholder:text-gray-400 transition-colors"
)

SELECT_INPUT = (
    "w-full px-3 py-1.5 border border-gray-200 rounded-xl text-sm text-gray-800 bg-white "
    "focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
)

class ProjectTypeForm(forms.ModelForm):
    class Meta:
        model = ProjectType
        fields = ['name']
        widgets = {
            'name': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'e.g. HVAC Installation, Electrical'}),
        }

class ProjectForm(forms.ModelForm):
    task_template = forms.ModelChoiceField(
        queryset=TaskTemplate.objects.all().order_by('name'),
        required=False,
        label="Initialize Tasks from Template",
        empty_label="-- No Template --",
        widget=forms.Select(attrs={'class': SELECT_INPUT})
    )

    class Meta:
        model = Project
        fields = [
            'name', 'project_type', 'client_name', 'client_email', 'consultant', 'consultant_email', 'main_contractor',
            'location', 'project_managers', 'site_engineers', 'project_members',
            'hvac_capacity_tr', 'system_type', 'start_date',
            'completion_date', 'status', 'progress_percent', 'branch'
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'Project Name'}),
            'project_type': forms.Select(attrs={'class': SELECT_INPUT}),
            'client_name': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'Client Name'}),
            'client_email': forms.EmailInput(attrs={'class': TEXT_INPUT, 'placeholder': 'Client Email'}),
            'consultant': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'Consultant Name'}),
            'consultant_email': forms.EmailInput(attrs={'class': TEXT_INPUT, 'placeholder': 'Consultant Email'}),
            'main_contractor': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'Main Contractor'}),
            'location': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'Project Location'}),
            'project_managers': forms.SelectMultiple(attrs={'class': SELECT_INPUT}),
            'site_engineers': forms.SelectMultiple(attrs={'class': SELECT_INPUT}),
            'project_members': forms.SelectMultiple(attrs={'class': SELECT_INPUT}),
            'hvac_capacity_tr': forms.NumberInput(attrs={'class': TEXT_INPUT, 'placeholder': 'e.g. 150.00', 'step': '0.01'}),
            'system_type': forms.Select(attrs={'class': SELECT_INPUT}),
            'start_date': forms.DateInput(format='%Y-%m-%d', attrs={'class': TEXT_INPUT, 'type': 'date'}),
            'completion_date': forms.DateInput(format='%Y-%m-%d', attrs={'class': TEXT_INPUT, 'type': 'date'}),
            'status': forms.Select(attrs={'class': SELECT_INPUT}),
            'progress_percent': forms.NumberInput(attrs={'class': TEXT_INPUT, 'min': '0', 'max': '100', 'placeholder': '0'}),
            'branch': forms.Select(attrs={'class': SELECT_INPUT}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.employees.models import EmployeeProfile
        from django.db.models import Q
        active_employees = EmployeeProfile.objects.filter(is_active=True)
        active_managers = EmployeeProfile.objects.filter(
            is_active=True,
            is_project_manager=True
        )
        self.fields['project_managers'].queryset = active_managers
        self.fields['site_engineers'].queryset = active_employees
        self.fields['project_members'].queryset = active_employees
        
        self.fields['project_managers'].label_from_instance = lambda obj: f"{obj.full_name} ({obj.designation or 'No Designation'})"
        self.fields['site_engineers'].label_from_instance = lambda obj: f"{obj.full_name} ({obj.designation or 'No Designation'})"
        self.fields['project_members'].label_from_instance = lambda obj: f"{obj.full_name} ({obj.designation or 'No Designation'})"
        
        self.fields['system_type'].required = False
        self.fields['hvac_capacity_tr'].required = False

        if self.instance.pk:
            if 'task_template' in self.fields:
                del self.fields['task_template']

    def clean(self):
        cleaned_data = super().clean()
        project_type = cleaned_data.get('project_type')
        system_type = cleaned_data.get('system_type')
        start_date = cleaned_data.get('start_date')
        completion_date = cleaned_data.get('completion_date')

        # #3 — Date ordering: completion_date cannot be before start_date
        if start_date and completion_date and completion_date < start_date:
            self.add_error('completion_date', 'Completion date cannot be before the start date.')

        if project_type and project_type.name == 'HVAC Installation':
            if not system_type:
                self.add_error('system_type', 'System type is required for HVAC Installation projects.')
        else:
            cleaned_data['system_type'] = ''
            cleaned_data['hvac_capacity_tr'] = None

        return cleaned_data

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
            'baseline_start', 'baseline_finish',
            'actual_start', 'actual_finish',
            'is_milestone',
            'points', 'status', 'remarks', 'employee_note', 'assignment_attachment'
        ]
        widgets = {
            'order': forms.NumberInput(attrs={'class': TEXT_INPUT, 'min': 1}),
            'activity': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'e.g. Duct Installation'}),
            'responsible_person': forms.Select(attrs={'class': SELECT_INPUT}),
            'planned_start': forms.DateInput(format='%Y-%m-%d', attrs={'class': TEXT_INPUT, 'type': 'date'}),
            'planned_finish': forms.DateInput(format='%Y-%m-%d', attrs={'class': TEXT_INPUT, 'type': 'date'}),
            'duration_days': forms.NumberInput(attrs={'class': TEXT_INPUT, 'min': 1}),
            'baseline_start': forms.DateInput(format='%Y-%m-%d', attrs={'class': TEXT_INPUT, 'type': 'date'}),
            'baseline_finish': forms.DateInput(format='%Y-%m-%d', attrs={'class': TEXT_INPUT, 'type': 'date'}),
            'actual_start': forms.DateInput(format='%Y-%m-%d', attrs={'class': TEXT_INPUT, 'type': 'date'}),
            'actual_finish': forms.DateInput(format='%Y-%m-%d', attrs={'class': TEXT_INPUT, 'type': 'date'}),
            'is_milestone': forms.CheckboxInput(attrs={'class': 'w-4 h-4 text-primary-600'}),
            'points': forms.NumberInput(attrs={'class': TEXT_INPUT, 'min': 0, 'placeholder': 'Points (default 10)'}),
            'status': forms.Select(attrs={'class': SELECT_INPUT}),
            'remarks': forms.Textarea(attrs={'class': TEXT_INPUT, 'rows': 2, 'placeholder': 'Remarks...'}),
            'employee_note': forms.Textarea(attrs={'class': TEXT_INPUT, 'rows': 2, 'placeholder': 'Employee Note...'}),
            'assignment_attachment': forms.ClearableFileInput(attrs={'class': TEXT_INPUT}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['points'].required = False
        self.fields['employee_note'].required = False
        self.fields['baseline_start'].required = False
        self.fields['baseline_finish'].required = False
        self.fields['actual_start'].required = False
        self.fields['actual_finish'].required = False
        self.fields['is_milestone'].required = False

    def clean(self):
        cleaned_data = super().clean()
        planned_start = cleaned_data.get('planned_start')
        planned_finish = cleaned_data.get('planned_finish')

        # #4 — Task date ordering: planned_finish cannot be before planned_start
        if planned_start and planned_finish and planned_finish < planned_start:
            self.add_error('planned_finish', 'Planned finish date cannot be before the planned start date.')

        baseline_start = cleaned_data.get('baseline_start')
        baseline_finish = cleaned_data.get('baseline_finish')
        if baseline_start and baseline_finish and baseline_finish < baseline_start:
            self.add_error('baseline_finish', 'Baseline finish cannot be before baseline start.')

        actual_start = cleaned_data.get('actual_start')
        actual_finish = cleaned_data.get('actual_finish')
        if actual_start and actual_finish and actual_finish < actual_start:
            self.add_error('actual_finish', 'Actual finish cannot be before actual start.')

        return cleaned_data


class DailyProgressLogForm(forms.ModelForm):
    class Meta:
        model = DailyProgressLog
        fields = ['date', 'planned_work', 'completed_work', 'manpower_count', 'delay_reason', 'supervisor_name']
        widgets = {
            'date': forms.DateInput(format='%Y-%m-%d', attrs={'class': TEXT_INPUT, 'type': 'date'}),
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
            'date': forms.DateInput(format='%Y-%m-%d', attrs={'class': TEXT_INPUT, 'type': 'date'}),
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

    def clean(self):
        cleaned_data = super().clean()
        required_qty = cleaned_data.get('required_qty')
        received_qty = cleaned_data.get('received_qty')

        # #5 — Over-delivery: allow but surface a non-blocking info note.
        # Hard-blocking would frustrate site teams who sometimes receive buffer stock.
        # The balance property on the model will show as negative, signalling over-delivery.
        if required_qty is not None and received_qty is not None and received_qty > required_qty:
            self.add_error(
                'received_qty',
                f'Received quantity ({received_qty}) exceeds required quantity ({required_qty}). '
                'This is allowed (over-delivery), but verify the figures are correct.'
            )

        return cleaned_data
class GlobalProjectTaskForm(forms.ModelForm):
    class Meta:
        model = ProjectTask
        fields = [
            'project', 'order', 'activity', 'responsible_person',
            'planned_start', 'planned_finish', 'duration_days',
            'points', 'status', 'remarks', 'employee_note', 'assignment_attachment'
        ]
        widgets = {
            'project': forms.Select(attrs={'class': SELECT_INPUT}),
            'order': forms.NumberInput(attrs={'class': TEXT_INPUT, 'min': 1}),
            'activity': forms.TextInput(attrs={'class': TEXT_INPUT, 'placeholder': 'e.g. Duct Installation'}),
            'responsible_person': forms.Select(attrs={'class': SELECT_INPUT}),
            'planned_start': forms.DateInput(format='%Y-%m-%d', attrs={'class': TEXT_INPUT, 'type': 'date'}),
            'planned_finish': forms.DateInput(format='%Y-%m-%d', attrs={'class': TEXT_INPUT, 'type': 'date'}),
            'duration_days': forms.NumberInput(attrs={'class': TEXT_INPUT, 'min': 1}),
            'points': forms.NumberInput(attrs={'class': TEXT_INPUT, 'min': 0, 'placeholder': 'Points (default 10)'}),
            'status': forms.Select(attrs={'class': SELECT_INPUT}),
            'remarks': forms.Textarea(attrs={'class': TEXT_INPUT, 'rows': 2, 'placeholder': 'Remarks...'}),
            'employee_note': forms.Textarea(attrs={'class': TEXT_INPUT, 'rows': 2, 'placeholder': 'Employee Note...'}),
            'assignment_attachment': forms.ClearableFileInput(attrs={'class': TEXT_INPUT}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['project'].required = False
        self.fields['project'].empty_label = '— Standalone Task (no project) —'
        self.fields['points'].required = False
        self.fields['employee_note'].required = False

    def clean(self):
        cleaned_data = super().clean()
        planned_start = cleaned_data.get('planned_start')
        planned_finish = cleaned_data.get('planned_finish')

        if planned_start and planned_finish and planned_finish < planned_start:
            self.add_error('planned_finish', 'Planned finish date cannot be before the planned start date.')

        return cleaned_data


class TaskDependencyForm(forms.ModelForm):
    """Form to add a dependency between two tasks in the same project."""

    class Meta:
        model = TaskDependency
        fields = ['predecessor', 'dep_type', 'lag_days']
        widgets = {
            'predecessor': forms.Select(attrs={'class': SELECT_INPUT}),
            'dep_type': forms.Select(attrs={'class': SELECT_INPUT}),
            'lag_days': forms.NumberInput(attrs={
                'class': TEXT_INPUT,
                'placeholder': '0 (positive=lag, negative=lead)',
            }),
        }

    def __init__(self, *args, project=None, successor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._successor = successor
        if project is not None:
            # Only allow tasks in the same project as predecessors
            qs = ProjectTask.objects.filter(project=project)
            if successor:
                qs = qs.exclude(pk=successor.pk)
            self.fields['predecessor'].queryset = qs
            self.fields['predecessor'].label_from_instance = (
                lambda obj: f"#{obj.order} \u2013 {obj.activity}"
            )
        self.fields['lag_days'].required = False
        self.fields['lag_days'].initial = 0

    def clean(self):
        cleaned_data = super().clean()
        predecessor = cleaned_data.get('predecessor')
        if predecessor and self._successor:
            if predecessor.pk == self._successor.pk:
                raise forms.ValidationError('A task cannot depend on itself.')
            if TaskDependency.has_circular(predecessor.pk, self._successor.pk):
                raise forms.ValidationError(
                    'This dependency would create a circular dependency chain.'
                )
        return cleaned_data
