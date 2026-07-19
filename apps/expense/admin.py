from django.contrib import admin
from .models import Expense

@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ('employee', 'category', 'amount', 'status', 'requested_at', 'reviewed_by', 'reviewed_at')
    list_filter = ('status', 'category', 'requested_at')
    search_fields = ('employee__full_name', 'description')
    readonly_fields = ('sync_uuid', 'client_event_time', 'synced_at')
