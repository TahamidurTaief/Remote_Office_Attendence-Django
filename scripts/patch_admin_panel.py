import re

VIEWS_PATH = 'apps/admin_panel/views.py'
with open(VIEWS_PATH, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Update AdminCRUDPermissionMixin to use required_permission & action_type and leave.add / attendance.add
content = content.replace("codename = 'leave.create'", "required_permission = 'leave.add'\n    action_type = 'add'")
content = content.replace("codename = 'attendance.create'", "required_permission = 'attendance.add'\n    action_type = 'add'")

# 2. Add explicit permissions to all admin_panel CBVs
ADMIN_CBV_PERMS = {
    'AdminDashboardView': ('dashboard.view', 'view'),
    'AdminAttendanceListView': ('attendance.view', 'view'),
    'ExportAttendanceCSVView': ('attendance.export', 'export'),
    'ManualEntryView': ('attendance.add', 'add'),
    'AttendanceDetailView': ('attendance.view', 'view'),
    'AttendanceLocationsView': ('attendance.view', 'view'),
    'ReportsMainView': ('reports.view', 'view'),
    'DailyReportView': ('reports.view', 'view'),
    'MonthlyReportView': ('reports.view', 'view'),
    'EmployeeReportView': ('reports.view', 'view'),
    'EmployeeDayDetailView': ('reports.view', 'view'),
    'LeaveMonthlyReportView': ('reports.view', 'view'),
    'LeaveEmployeeReportView': ('reports.view', 'view'),
    'ExportLeaveReportCSVView': ('reports.export', 'export'),
    'ExportLeaveReportPDFView': ('reports.export', 'export'),
    'ExportReportCSVView': ('reports.export', 'export'),
    'ExportReportPDFView': ('reports.export', 'export'),
    'OfficeScheduleView': ('schedule.view', 'view'),
    'ExpiredDataView': ('dashboard.view', 'view'),
    'AbsentReportView': ('reports.view', 'view'),
    'ExportAbsentReportExcelView': ('reports.export', 'export'),
    'ExportAbsentReportPDFView': ('reports.export', 'export'),
    'AdminAddLeaveView': ('leave.add', 'add'),
    'EmployeeKPIWidgetView': ('employees.view', 'view'),
    'GlobalSearchView': ('dashboard.view', 'view'),
    'RoleBasedDashboardView': ('dashboard.view', 'view'),
    'AdminLeaveRequestCreateView': ('leave.add', 'add'),
    'AdminLeaveRequestUpdateView': ('leave.edit', 'edit'),
    'AdminLeaveRequestDeleteView': ('leave.delete', 'delete'),
    'AdminLeaveBalanceUpdateView': ('leave.edit', 'edit'),
    'AdminAttendanceCreateView': ('attendance.add', 'add'),
    'AdminAttendanceUpdateView': ('attendance.edit', 'edit'),
    'AdminAttendanceDeleteView': ('attendance.delete', 'delete'),
    'AIWorkspaceView': ('dashboard.view', 'view'),
    'AISettingsSaveView': ('settings.edit', 'edit'),
    'AIChatbotResponseView': ('dashboard.view', 'view'),
}

for cls_name, (perm, act) in ADMIN_CBV_PERMS.items():
    if f"class {cls_name}" in content:
        # If it doesn't already have required_permission
        pattern = rf"(class\s+{cls_name}\([^)]*\):)"
        # Check if already present
        if not re.search(rf"class\s+{cls_name}\([^)]*\):(?:\s+[\"'].*?[\"'])?\s+required_permission", content):
            replacement = rf"\1\n    required_permission = '{perm}'\n    action_type = '{act}'"
            content = re.sub(pattern, replacement, content, count=1)

# 3. Remove allowed_roles in admin_panel/views.py
content = re.sub(r"\s+allowed_roles\s*=\s*\[[^\]]+\]", "", content)

# 4. Remove role bypasses in AttendanceDetailView.dispatch
old_att_dispatch = """    def dispatch(self, request, *args, **kwargs):
        from apps.accounts.engine import PermissionEngine
        if not (request.user.is_superuser or PermissionEngine.evaluate(request.user, 'attendance.view').allowed or getattr(request.user, 'role', '') in ('admin', 'hr')):
            from django.http import HttpResponseForbidden
            return HttpResponseForbidden("Unauthorized to view attendance details.")
        return super().dispatch(request, *args, **kwargs)"""
new_att_dispatch = """    def dispatch(self, request, *args, **kwargs):
        from apps.accounts.engine import PermissionEngine
        if not (request.user.is_superuser or PermissionEngine.evaluate(request.user, 'attendance.view').allowed):
            from django.http import HttpResponseForbidden
            return HttpResponseForbidden("Unauthorized to view attendance details.")
        return super().dispatch(request, *args, **kwargs)"""
content = content.replace(old_att_dispatch, new_att_dispatch)

# 5. Fix role_name / has_perm in admin_panel/views.py
content = content.replace(
    "has_approve_permission = self.request.user.has_perm('leave.change_leaverequest') or self.request.user.has_perm('leave.approve_leaverequest')",
    "has_approve_permission = PermissionEngine.evaluate(self.request.user, 'leave.approve').allowed"
)

# Replace role_name in AdminDashboardView
content = content.replace(
    "role_name = self.request.user.role.name if hasattr(self.request.user.role, 'name') else self.request.user.role\n        can_view_all = (role_name == 'admin')",
    "eval_res = PermissionEngine.evaluate(self.request.user, 'dashboard.view')\n        can_view_all = self.request.user.is_superuser or eval_res.scope == DataScope.GLOBAL\n        role_name = 'admin' if can_view_all else 'manager'"
)

# Replace role_name in AdminAttendanceListView
content = content.replace(
    "role_name = user.role.name if hasattr(user.role, 'name') else str(getattr(user, 'role', ''))\n        can_view_all = (user.is_superuser and role_name != 'manager') or role_name in ('admin', 'system_owner')",
    "eval_res = PermissionEngine.evaluate(user, 'attendance.view')\n        can_view_all = user.is_superuser or eval_res.scope == DataScope.GLOBAL\n        role_name = 'admin' if can_view_all else 'manager'"
)

content = content.replace(
    "role_name = user.role.name if hasattr(user.role, 'name') else str(getattr(user, 'role', ''))\n        can_view_all = user.is_superuser or role_name in ('admin', 'system_owner')",
    "eval_res = PermissionEngine.evaluate(user, 'attendance.view')\n        can_view_all = user.is_superuser or eval_res.scope == DataScope.GLOBAL\n        role_name = 'admin' if can_view_all else 'manager'"
)

# Replace role_name in Reports helper and export views
content = content.replace(
    "role_name = request.user.role.name if hasattr(request.user.role, 'name') else request.user.role\n    can_view_all = (role_name == 'admin')",
    "eval_res = PermissionEngine.evaluate(request.user, 'reports.view')\n    can_view_all = request.user.is_superuser or eval_res.scope == DataScope.GLOBAL\n    role_name = 'admin' if can_view_all else 'manager'"
)

content = content.replace(
    "role_name = request.user.role.name if hasattr(request.user.role, 'name') else request.user.role\n        can_view_all = (role_name == 'admin')",
    "eval_res = PermissionEngine.evaluate(request.user, 'reports.view')\n        can_view_all = request.user.is_superuser or eval_res.scope == DataScope.GLOBAL\n        role_name = 'admin' if can_view_all else 'manager'"
)

with open(VIEWS_PATH, 'w', encoding='utf-8') as f:
    f.write(content)

print("Updated apps/admin_panel/views.py successfully.")

# Update test_role_matrix_hierarchical.py
MATRIX_TEST_PATH = 'apps/admin_panel/tests/test_role_matrix_hierarchical.py'
with open(MATRIX_TEST_PATH, 'r', encoding='utf-8') as f:
    matrix_content = f.read()

matrix_content = matrix_content.replace(
    "        # Compatibility check: employees.create also allowed\n        res_create = PermissionEngine.evaluate(self.staff_user, 'employees.create')\n        self.assertTrue(res_create.allowed)",
    "        # Strict canonical check: unaliased employees.create fails closed\n        res_create = PermissionEngine.evaluate(self.staff_user, 'employees.create')\n        self.assertFalse(res_create.allowed)"
)

with open(MATRIX_TEST_PATH, 'w', encoding='utf-8') as f:
    f.write(matrix_content)

print("Updated test_role_matrix_hierarchical.py successfully.")
