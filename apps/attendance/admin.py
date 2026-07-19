from django.contrib import admin
from .models import Attendance, AttendanceLocation, SyncLog

class AttendanceLocationInline(admin.TabularInline):
    model = AttendanceLocation
    extra = 0

@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ('employee', 'date', 'check_in_time', 'check_out_time', 'type', 'status', 'total_hours')
    list_filter = ('type', 'status', 'date')
    search_fields = ('employee__full_name', 'employee__employee_id')
    inlines = [AttendanceLocationInline]

@admin.register(AttendanceLocation)
class AttendanceLocationAdmin(admin.ModelAdmin):
    list_display = ('attendance', 'event', 'latitude', 'longitude', 'timestamp')
    list_filter = ('event',)

@admin.register(SyncLog)
class SyncLogAdmin(admin.ModelAdmin):
    list_display = ('employee', 'sync_batch_id', 'started_at', 'completed_at', 'records_total', 'records_success')
    list_filter = ('started_at', 'employee')
    search_fields = ('employee__full_name', 'sync_batch_id')

