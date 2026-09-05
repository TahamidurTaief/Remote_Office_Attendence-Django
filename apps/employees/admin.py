from django.contrib import admin
from .models import EmployeeProfile, EmployeeAuditLog, Bank, BankBranch, EmployeeBankAccount

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


class BankBranchInline(admin.TabularInline):
    model = BankBranch
    extra = 0
    fields = ('name', 'district', 'routing_number', 'branch_code', 'is_active')


@admin.register(Bank)
class BankAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'bank_type', 'swift_code', 'is_active', 'display_order')
    list_filter = ('bank_type', 'is_active')
    search_fields = ('code', 'name', 'swift_code')
    inlines = [BankBranchInline]
    ordering = ('display_order', 'name')


@admin.register(BankBranch)
class BankBranchAdmin(admin.ModelAdmin):
    list_display = ('name', 'bank', 'district', 'routing_number', 'branch_code', 'is_active')
    list_filter = ('bank', 'district', 'is_active')
    search_fields = ('name', 'routing_number', 'district', 'bank__name')
    ordering = ('district', 'name')


@admin.register(EmployeeBankAccount)
class EmployeeBankAccountAdmin(admin.ModelAdmin):
    list_display = (
        'employee', 'account_holder_name', 'bank', 'branch',
        'routing_number', 'masked_account_display', 'verification_status',
        'is_primary', 'is_active'
    )
    list_filter = ('verification_status', 'bank', 'is_primary', 'is_active')
    search_fields = ('employee__employee_number', 'employee__first_name', 'employee__last_name', 'account_holder_name', 'routing_number')
    readonly_fields = ('account_number_encrypted', 'routing_number', 'verified_by', 'verified_at')

    @admin.display(description='Account Number')
    def masked_account_display(self, obj):
        return obj.masked_account_number

