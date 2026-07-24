from django.contrib import admin
from .models import EmployeeProfile, EmployeeAuditLog

@admin.register(EmployeeProfile)
class EmployeeProfileAdmin(admin.ModelAdmin):
    list_display = ('employee_id', 'full_name', 'department', 'branch', 'is_active')
    list_filter = ('is_active', 'department', 'branch')
    search_fields = ('employee_id', 'full_name', 'user__email')


@admin.register(EmployeeAuditLog)
class EmployeeAuditLogAdmin(admin.ModelAdmin):
    list_display = ('employee', 'changed_by', 'ip_address', 'timestamp')
    readonly_fields = ('employee', 'old_value', 'new_value', 'changed_by', 'ip_address', 'user_agent', 'timestamp')
    
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
