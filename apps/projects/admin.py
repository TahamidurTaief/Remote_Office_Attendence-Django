from django.contrib import admin
from .models import Project, ProjectType, TaskTemplate, TaskTemplateItem, ProjectTask, DailyProgressLog, ManpowerDeployment, ProjectMaterial, ProjectSignOff

@admin.register(ProjectType)
class ProjectTypeAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)
    ordering = ('name',)

@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'client_name', 'status', 'progress_percent', 'project_manager', 'branch', 'start_date')
    list_filter = ('status', 'system_type', 'branch')
    search_fields = ('name', 'client_name', 'location')

class TaskTemplateItemInline(admin.TabularInline):
    model = TaskTemplateItem
    extra = 1

@admin.register(TaskTemplate)
class TaskTemplateAdmin(admin.ModelAdmin):
    list_display = ('name', 'description', 'created_at')
    inlines = [TaskTemplateItemInline]

@admin.register(ProjectTask)
class ProjectTaskAdmin(admin.ModelAdmin):
    list_display = ('project', 'order', 'activity', 'responsible_person', 'planned_start', 'planned_finish', 'status')
    list_filter = ('status', 'project')
    search_fields = ('activity', 'project__name')

@admin.register(DailyProgressLog)
class DailyProgressLogAdmin(admin.ModelAdmin):
    list_display = ('project', 'date', 'supervisor_name', 'manpower_count', 'logged_by', 'created_at')
    list_filter = ('date', 'project')
    search_fields = ('supervisor_name', 'project__name', 'planned_work', 'completed_work')

@admin.register(ManpowerDeployment)
class ManpowerDeploymentAdmin(admin.ModelAdmin):
    list_display = ('project', 'date', 'trade', 'required_count', 'present_count')
    list_filter = ('date', 'trade', 'project')
    search_fields = ('project__name', 'trade')

@admin.register(ProjectMaterial)
class ProjectMaterialAdmin(admin.ModelAdmin):
    list_display = ('project', 'material_name', 'unit', 'required_qty', 'received_qty', 'balance')
    list_filter = ('project',)
    search_fields = ('material_name', 'project__name')

@admin.register(ProjectSignOff)
class ProjectSignOffAdmin(admin.ModelAdmin):
    list_display = ('project', 'project_manager_name', 'project_manager_signed_at', 'site_engineer_name', 'site_engineer_signed_at')
    search_fields = ('project__name',)






