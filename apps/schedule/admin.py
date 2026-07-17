from django.contrib import admin
from .models import ScheduleEvent

@admin.register(ScheduleEvent)
class ScheduleEventAdmin(admin.ModelAdmin):
    list_display = ('title', 'date', 'start_time', 'end_time', 'event_type', 'created_by')
    list_filter = ('event_type', 'date', 'created_by')
    search_fields = ('title', 'description')
    filter_horizontal = ('assigned_to',)
