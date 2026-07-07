from django.contrib import admin
from .models import Attendance, AttendanceLocation

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
