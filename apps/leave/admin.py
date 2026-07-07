from django.contrib import admin
from .models import LeaveType, LeaveBalance, LeaveRequest

@admin.register(LeaveType)
class LeaveTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'default_days_per_year')
    search_fields = ('name',)

@admin.register(LeaveBalance)
class LeaveBalanceAdmin(admin.ModelAdmin):
    list_display = ('employee', 'leave_type', 'year', 'total_days', 'used_days', 'remaining_days')
    list_filter = ('year', 'leave_type')
    search_fields = ('employee__full_name', 'employee__employee_id')

@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display = ('employee', 'leave_type', 'start_date', 'end_date', 'number_of_days', 'status', 'requested_at')
    list_filter = ('status', 'leave_type', 'start_date')
    search_fields = ('employee__full_name', 'employee__employee_id', 'reason')
    readonly_fields = ('number_of_days', 'requested_at')
    
    def save_model(self, request, obj, form, change):
        if not change:
            # Set employee from user if not set (optional fallback)
            pass
        super().save_model(request, obj, form, change)
