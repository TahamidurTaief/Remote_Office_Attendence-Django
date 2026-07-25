from django.contrib import admin
from .models import (
    WorkflowDefinition,
    WorkflowStep,
    WorkflowDelegation,
    WorkflowInstance,
    WorkflowAction
)


class WorkflowStepInline(admin.TabularInline):
    model = WorkflowStep
    extra = 1


@admin.register(WorkflowDefinition)
class WorkflowDefinitionAdmin(admin.ModelAdmin):
    list_display = ('code', 'module', 'name', 'is_active', 'created_at')
    list_filter = ('module', 'is_active')
    search_fields = ('code', 'name', 'module')
    inlines = [WorkflowStepInline]


@admin.register(WorkflowStep)
class WorkflowStepAdmin(admin.ModelAdmin):
    list_display = ('workflow', 'step_number', 'name', 'approver_role', 'from_status', 'to_status', 'sla_hours')
    list_filter = ('approver_role', 'workflow')


@admin.register(WorkflowDelegation)
class WorkflowDelegationAdmin(admin.ModelAdmin):
    list_display = ('from_user', 'to_user', 'workflow_code', 'start_date', 'end_date', 'is_active')
    list_filter = ('is_active',)


@admin.register(WorkflowInstance)
class WorkflowInstanceAdmin(admin.ModelAdmin):
    list_display = ('definition', 'object_type', 'object_id', 'current_step', 'current_status', 'initiated_by', 'initiated_at', 'sla_deadline')
    list_filter = ('current_status', 'definition')


@admin.register(WorkflowAction)
class WorkflowActionAdmin(admin.ModelAdmin):
    list_display = ('instance', 'step_number', 'actor', 'action', 'timestamp')
    list_filter = ('action',)
