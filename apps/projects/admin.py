from django.contrib import admin
from .models import Project

@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'client_name', 'status', 'progress_percent', 'project_manager', 'branch', 'start_date')
    list_filter = ('status', 'system_type', 'branch')
    search_fields = ('name', 'client_name', 'location')

