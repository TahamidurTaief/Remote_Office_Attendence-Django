from django.contrib import admin
from .models import EmployeeProfile

@admin.register(EmployeeProfile)
class EmployeeProfileAdmin(admin.ModelAdmin):
    list_display = ('employee_id', 'full_name', 'department', 'branch', 'is_active')
    list_filter = ('is_active', 'department', 'branch')
    search_fields = ('employee_id', 'full_name', 'user__email')
