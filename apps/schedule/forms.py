from django import forms
from .models import ScheduleEvent
from apps.employees.models import EmployeeProfile
from apps.projects.models import Project

TEXT_INPUT = (
    "w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-xs text-slate-800 dark:text-slate-100 "
    "bg-white dark:bg-slate-900 focus:outline-none focus:ring-1 focus:ring-primary-500 focus:border-primary-500 "
    "placeholder:text-slate-400 dark:placeholder:text-slate-500 transition-colors"
)

SELECT_INPUT = (
    "w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-xs text-slate-800 dark:text-slate-100 "
    "bg-white dark:bg-slate-900 focus:outline-none focus:ring-1 focus:ring-primary-500 focus:border-primary-500 transition-colors"
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

    def __init__(self, *args, user=None, scope_permission=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.scope_permission = scope_permission

        if user and scope_permission:
            from apps.schedule.views import (
                _get_effective_schedule_scope,
                get_scoped_employee_queryset_for_schedule,
                get_scoped_project_queryset_for_schedule,
            )
            scope = _get_effective_schedule_scope(user, scope_permission)
            self.fields['assigned_to'].queryset = get_scoped_employee_queryset_for_schedule(user, scope)
            self.fields['project'].queryset = get_scoped_project_queryset_for_schedule(user, scope)
        else:
            self.fields['assigned_to'].queryset = EmployeeProfile.objects.filter(is_active=True)
            self.fields['project'].queryset = Project.objects.exclude(status='Completed')

        self.fields['assigned_to'].label_from_instance = lambda obj: f"{obj.canonical_full_name} ({obj.employee_id})"

    def clean(self):
        cleaned_data = super().clean()
        event_date = cleaned_data.get('date')
        start_time = cleaned_data.get('start_time')
        end_time = cleaned_data.get('end_time')
        assigned_employees = cleaned_data.get('assigned_to')
        project = cleaned_data.get('project')

        # Project validation
        if project and project.status == 'Completed':
            raise forms.ValidationError("Cannot schedule events for a completed project.")

        # Server-side validation against forged employee or project IDs
        if self.user and self.scope_permission:
            allowed_emps = self.fields['assigned_to'].queryset
            if assigned_employees:
                for emp in assigned_employees:
                    if not allowed_emps.filter(pk=emp.pk).exists():
                        raise forms.ValidationError(
                            f"Unauthorized employee assignment: {emp.canonical_full_name} is outside your authorized scope."
                        )
            if project:
                allowed_projs = self.fields['project'].queryset
                if not allowed_projs.filter(pk=project.pk).exists():
                    raise forms.ValidationError(
                        f"Unauthorized project selection: '{project.name}' is outside your authorized scope."
                    )

        # S2: Conflict/overlap checks for assigned employees
        if event_date and assigned_employees:
            for emp in assigned_employees:
                # Find all conflicting events on the same day for this employee
                conflicts = ScheduleEvent.objects.filter(
                    date=event_date,
                    assigned_to=emp
                )
                if self.instance.pk:
                    conflicts = conflicts.exclude(pk=self.instance.pk)

                # Overlap calculation (only if not all-day event)
                if start_time and end_time:
                    for conf in conflicts:
                        if conf.start_time and conf.end_time:
                            # Standard interval overlap checks
                            # Either:
                            # 1. New event starts during existing
                            # 2. New event ends during existing
                            # 3. New event completely wraps existing
                            # 4. Same start/end times
                            if (conf.start_time <= start_time < conf.end_time) or \
                               (conf.start_time < end_time <= conf.end_time) or \
                               (start_time <= conf.start_time and end_time >= conf.end_time):
                                raise forms.ValidationError(
                                    f"Scheduling conflict: {emp.canonical_full_name} is already assigned to the event '{conf.title}' at this time."
                                )
                else:
                    # If it's an all-day event, conflict with any other event on the same day
                    if conflicts.exists():
                        raise forms.ValidationError(
                            f"Scheduling conflict: {emp.canonical_full_name} is already assigned to an event on {event_date}."
                        )

        return cleaned_data

