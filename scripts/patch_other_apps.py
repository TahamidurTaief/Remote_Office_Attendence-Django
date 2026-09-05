import re

# 1. apps/attendance/transaction_service.py
with open('apps/attendance/transaction_service.py', 'r', encoding='utf-8') as f:
    c = f.read()
c = c.replace(
    "is_admin_or_hr = user.is_superuser or getattr(user, 'role', '') in ('admin', 'hr')",
    "from apps.accounts.engine import PermissionEngine\n            is_admin_or_hr = user.is_superuser or PermissionEngine.evaluate(user, 'attendance.override').allowed or PermissionEngine.evaluate(user, 'attendance.edit').allowed"
)
with open('apps/attendance/transaction_service.py', 'w', encoding='utf-8') as f:
    f.write(c)
print("Patched attendance transaction_service.py")

# 2. apps/attendance/views.py
with open('apps/attendance/views.py', 'r', encoding='utf-8') as f:
    c = f.read()

# check_role
c = re.sub(
    r"def check_role\(user\):\s+if not user or not user\.is_authenticated:\s+return False\s+from apps\.accounts\.engine import PermissionEngine\s+if PermissionEngine\.evaluate\(user, 'attendance\.view'\)\.allowed and hasattr\(user, 'employee_profile'\):\s+return True\s+return getattr\(user, 'role', ''\) in \('staff', 'manager', 'admin', 'hr'\)",
    """def check_role(user):
    if not user or not user.is_authenticated:
        return False
    from apps.accounts.engine import PermissionEngine
    return PermissionEngine.evaluate(user, 'attendance.view').allowed and hasattr(user, 'employee_profile')""",
    c
)

# live_locations
c = c.replace(
    "if not (request.user.is_authenticated and (request.user.is_superuser or PermissionEngine.evaluate(request.user, 'attendance.view').allowed or getattr(request.user, 'role', '') == 'admin')):",
    "if not (request.user.is_authenticated and (request.user.is_superuser or PermissionEngine.evaluate(request.user, 'attendance.view').allowed)):"
)

# check_approval_permissions
c = c.replace(
    "if res.allowed or user.is_superuser or getattr(user, 'role', '') == 'admin':",
    "if res.allowed or user.is_superuser:"
)

# AdminAttendanceRequestsView
c = re.sub(
    r"class AdminAttendanceRequestsView\(RoleRequiredMixin, ListView\):\s+allowed_roles = \['admin', 'manager', 'system_owner', 'hr'\]",
    """class AdminAttendanceRequestsView(RoleRequiredMixin, ListView):
    required_permission = 'attendance.view'
    action_type = 'view'""",
    c
)

# employee_timeline
old_timeline = """def employee_timeline(request):
    is_staff = request.user.role == 'staff'
    is_manager = request.user.role in ('manager', 'admin') or request.user.is_superuser
    
    if not (is_staff or is_manager):
        return redirect('accounts:login')"""
new_timeline = """def employee_timeline(request):
    from apps.accounts.engine import PermissionEngine
    eval_res = PermissionEngine.evaluate(request.user, 'attendance.view')
    if not (request.user.is_superuser or eval_res.allowed):
        from django.http import HttpResponseForbidden
        from django.template.loader import render_to_string
        return HttpResponseForbidden(render_to_string('cotton/permission_denied_hx.html', {'message': 'You do not have permission to view the timeline.'}, request=request))
    is_manager = request.user.is_superuser or eval_res.scope in ('global', 'branch', 'department', 'team')
    is_staff = True"""
c = c.replace(old_timeline, new_timeline)

with open('apps/attendance/views.py', 'w', encoding='utf-8') as f:
    f.write(c)
print("Patched attendance views.py")

# 3. apps/backups/views.py
with open('apps/backups/views.py', 'r', encoding='utf-8') as f:
    c = f.read()
c = c.replace(
    "if not (request.user.is_superuser or PermissionEngine.evaluate(request.user, 'accounts.edit').allowed or getattr(request.user, 'role', '') == 'admin'):",
    "if not (request.user.is_superuser or PermissionEngine.evaluate(request.user, 'accounts.edit').allowed or PermissionEngine.evaluate(request.user, 'settings.edit').allowed):"
)
with open('apps/backups/views.py', 'w', encoding='utf-8') as f:
    f.write(c)
print("Patched backups views.py")

# 4. apps/audit
with open('apps/audit/media_service.py', 'r', encoding='utf-8') as f:
    c = f.read()
old_media = """        user_role = getattr(user, "role", "staff")
        if asset.module == "employees" and user_role not in ["admin", "system_owner", "hr"]:
            # Staff can view their own documents
            if not asset.object_id or asset.object_id != str(getattr(user, "employee_master_id", "")):
                raise PermissionError("Access denied to private document.")"""
new_media = """        from apps.accounts.engine import PermissionEngine
        has_manage = user.is_superuser or PermissionEngine.evaluate(user, f"{asset.module}.view").allowed
        if asset.module == "employees" and not has_manage:
            # Staff can view their own documents
            if not asset.object_id or asset.object_id != str(getattr(user, "employee_master_id", "")):
                raise PermissionError("Access denied to private document.")"""
c = c.replace(old_media, new_media)
with open('apps/audit/media_service.py', 'w', encoding='utf-8') as f:
    f.write(c)
print("Patched audit media_service.py")

with open('apps/audit/menu_registry.py', 'r', encoding='utf-8') as f:
    c = f.read()
old_can_view = """def can_view_menu(user, menu_key):
    if menu_key not in PINNABLE_MENUS:
        return False
    if user.is_superuser:
        return True
    cfg = PINNABLE_MENUS[menu_key]
    if cfg.get("is_superuser_only", False):
        return False
    user_role = getattr(user, "role", "")
    return user_role in cfg.get("roles", [])"""
new_can_view = """def can_view_menu(user, menu_key):
    if menu_key not in PINNABLE_MENUS:
        return False
    if user.is_superuser:
        return True
    cfg = PINNABLE_MENUS[menu_key]
    if cfg.get("is_superuser_only", False):
        return False
    url_name = cfg.get("url_name", "")
    app_label = url_name.split(":")[0] if ":" in url_name else "dashboard"
    if app_label == "admin_panel":
        perm = "attendance.view" if "attendance" in url_name else ("reports.view" if "report" in url_name else "dashboard.view")
    else:
        perm = f"{app_label}.view"
    from apps.accounts.engine import PermissionEngine
    return PermissionEngine.evaluate(user, perm).allowed"""
c = c.replace(old_can_view, new_can_view)
with open('apps/audit/menu_registry.py', 'w', encoding='utf-8') as f:
    f.write(c)
print("Patched audit menu_registry.py")

with open('apps/audit/services.py', 'r', encoding='utf-8') as f:
    c = f.read()
c = c.replace('return getattr(user, "role", "") or ""', 'return "unassigned"')
c = c.replace('role_codes.add(getattr(user, "role", ""))', '# role fallback removed')
with open('apps/audit/services.py', 'w', encoding='utf-8') as f:
    f.write(c)
print("Patched audit services.py")

with open('apps/audit/views.py', 'r', encoding='utf-8') as f:
    c = f.read()
c = re.sub(
    r"class TrashListView\(LoginRequiredMixin, RoleRequiredMixin, View\):\s+allowed_roles = \[\"admin\", \"system_owner\", \"manager\", \"hr\"\]",
    """class TrashListView(LoginRequiredMixin, RoleRequiredMixin, View):
    required_permission = 'audit.view'
    action_type = 'view'""",
    c
)
c = re.sub(
    r"class TrashRestoreView\(LoginRequiredMixin, RoleRequiredMixin, View\):\s+allowed_roles = \[\"admin\", \"system_owner\", \"manager\", \"hr\"\]",
    """class TrashRestoreView(LoginRequiredMixin, RoleRequiredMixin, View):
    required_permission = 'audit.edit'
    action_type = 'edit'""",
    c
)
c = re.sub(
    r"class TrashBulkActionView\(LoginRequiredMixin, RoleRequiredMixin, View\):\s+allowed_roles = \[\"admin\", \"system_owner\", \"manager\", \"hr\"\]",
    """class TrashBulkActionView(LoginRequiredMixin, RoleRequiredMixin, View):
    required_permission = 'audit.edit'
    action_type = 'edit'""",
    c
)
with open('apps/audit/views.py', 'w', encoding='utf-8') as f:
    f.write(c)
print("Patched audit views.py")

# 5. apps/employees/views.py
with open('apps/employees/views.py', 'r', encoding='utf-8') as f:
    c = f.read()

# CBVs permission mappings
EMPLOYEE_CBV_PERMS = {
    'EmployeeListView': ('employees.view', 'view'),
    'EmployeeCreateView': ('employees.add', 'add'),
    'EmployeeEditView': ('employees.edit', 'edit'),
    'ToggleStatusView': ('employees.edit', 'edit'),
    'EmployeeDocumentCreateView': ('employees.add', 'add'),
    'EmployeeDocumentEditView': ('employees.edit', 'edit'),
    'EmployeeDocumentDeleteView': ('employees.delete', 'delete'),
    'EmployeeDocumentVerifyView': ('employees.approve', 'approve'),
    'EmployeeDocumentArchiveView': ('employees.edit', 'edit'),
    'EmployeeMasterListView': ('employees.view', 'view'),
    'EmployeeMasterDetailView': ('employees.view', 'view'),
    'EmployeeMasterCreateView': ('employees.add', 'add'),
    'EmployeeMasterEditView': ('employees.edit', 'edit'),
    'EmployeeMasterArchiveView': ('employees.edit', 'edit'),
    'EmployeeMasterDeleteView': ('employees.delete', 'delete'),
    'EmployeeDocumentUploadView': ('employees.add', 'add'),
    'AssetListView': ('employees.view', 'view'),
    'AssetCreateView': ('employees.add', 'add'),
    'AssetAssignView': ('employees.edit', 'edit'),
    'AssetReturnView': ('employees.edit', 'edit'),
    'AssetReassignView': ('employees.edit', 'edit'),
    'LifecycleActionView': ('employees.edit', 'edit'),
    'LifecyclePendingListView': ('employees.view', 'view'),
    'LifecycleReviewView': ('employees.approve', 'approve'),
    'EmployeeWizardView': ('employees.add', 'add'),
    'EmployeeDocumentDownloadView': ('employees.view', 'view'),
    'EmployeeTimelineView': ('employees.view', 'view'),
    'EmployeeSuspendToggleView': ('employees.edit', 'edit'),
    'EmployeeSuspendModalView': ('employees.view', 'view'),
    'EmployeeAuditLogView': ('audit.view', 'view'),
    'OrgChartView': ('employees.view', 'view'),
    'OrgChartNodeView': ('employees.view', 'view'),
    'ManagerDelegationListView': ('employees.view', 'view'),
    'ManagerDelegationCreateView': ('employees.edit', 'edit'),
    'ManagerDelegationEndView': ('employees.edit', 'edit'),
    'EmployeeReportsView': ('reports.view', 'view'),
    'DepartmentListView': ('employees.view', 'view'),
    'DepartmentCreateView': ('employees.add', 'add'),
    'DepartmentEditView': ('employees.edit', 'edit'),
    'DepartmentDeleteView': ('employees.delete', 'delete'),
    'DepartmentExportCSVView': ('employees.export', 'export'),
    'DepartmentImportCSVView': ('employees.add', 'add'),
    'DesignationListView': ('employees.view', 'view'),
    'DesignationCreateView': ('employees.add', 'add'),
    'DesignationEditView': ('employees.edit', 'edit'),
    'DepartmentsForBranchAPIView': ('employees.view', 'view'),
    'DesignationsForDepartmentAPIView': ('employees.view', 'view'),
    'DesignationExportCSVView': ('employees.export', 'export'),
    'DesignationImportCSVView': ('employees.add', 'add'),
    'EmployeeExportCSVView': ('employees.export', 'export'),
    'EmployeeImportCSVView': ('employees.add', 'add'),
}

for cls_name, (perm, act) in EMPLOYEE_CBV_PERMS.items():
    if f"class {cls_name}" in c:
        pattern = rf"(class\s+{cls_name}\([^)]*\):)"
        if not re.search(rf"class\s+{cls_name}\([^)]*\):(?:\s+[\"'].*?[\"'])?\s+required_permission", c):
            replacement = rf"\1\n    required_permission = '{perm}'\n    action_type = '{act}'"
            c = re.sub(pattern, replacement, c, count=1)

# Remove allowed_roles
c = re.sub(r"\s+allowed_roles\s*=\s*\[[^\]]+\]", "", c)

# Fix payroll gating check
c = c.replace(
    "can_view_payroll = user.is_superuser or PermissionEngine.evaluate(user, 'employees.view_payroll').allowed or getattr(user, 'role', '') in ('admin', 'hr', 'hr_manager', 'hr_admin')",
    "can_view_payroll = user.is_superuser or PermissionEngine.evaluate(user, 'payroll.view').allowed or PermissionEngine.evaluate(user, 'employees.view_payroll').allowed"
)

# Fix has_perm delete check
c = re.sub(
    r"if res\.allowed:\s+return True\s+if user\.has_perm\('employees\.delete_employee'\):\s+return True",
    "if res.allowed:\n            return True",
    c
)

with open('apps/employees/views.py', 'w', encoding='utf-8') as f:
    f.write(c)
print("Patched employees views.py")

# 6. apps/expense/views.py
with open('apps/expense/views.py', 'r', encoding='utf-8') as f:
    c = f.read()

c = re.sub(
    r"class StaffOrManagerMixin\(RoleRequiredMixin\):\s+allowed_roles = \['staff', 'manager', 'admin'\]",
    """class StaffOrManagerMixin(RoleRequiredMixin):
    required_permission = 'expense.view'
    action_type = 'view'""",
    c
)

c = c.replace(
    "can_manage = self.request.user.is_superuser or PermissionEngine.evaluate(self.request.user, 'expense.approve').allowed or getattr(self.request.user, 'role', '') == 'admin'",
    "can_manage = self.request.user.is_superuser or PermissionEngine.evaluate(self.request.user, 'expense.approve').allowed"
)

c = c.replace(
    "can_manage = (\n        request.user.is_superuser\n        or PermissionEngine.evaluate(request.user, 'expense.approve').allowed\n        or getattr(request.user, 'role', '') in ('admin', 'manager', 'finance', 'accounts')\n    )",
    "can_manage = (\n        request.user.is_superuser\n        or PermissionEngine.evaluate(request.user, 'expense.approve').allowed\n    )"
)

# Dispatch in ExpenseWorkflowActionView
c = c.replace(
    "if user.is_superuser or getattr(user, 'role', '') == 'admin':",
    "from apps.accounts.rbac_models import DataScope\n            if user.is_superuser or (PermissionEngine.evaluate(user, 'expense.approve').allowed and PermissionEngine.get_effective_scope(user, 'expense.approve') == DataScope.GLOBAL):"
)
c = c.replace(
    "if getattr(user, 'role', '') == 'manager':\n                        return super().dispatch(request, *args, **kwargs)",
    "if PermissionEngine.evaluate(user, 'expense.approve').allowed:\n                        return super().dispatch(request, *args, **kwargs)"
)
c = c.replace(
    "if getattr(user, 'role', '') != 'finance' and not res.allowed:",
    "if not res.allowed:"
)
c = c.replace(
    "if getattr(user, 'role', '') != 'accounts':",
    "res_acc = PermissionEngine.evaluate(user, 'expense.approve')\n                if not res_acc.allowed:"
)

with open('apps/expense/views.py', 'w', encoding='utf-8') as f:
    f.write(c)
print("Patched expense views.py")

# 7. apps/leave/views.py
with open('apps/leave/views.py', 'r', encoding='utf-8') as f:
    c = f.read()

c = re.sub(
    r"class StaffOrManagerMixin\(RoleRequiredMixin\):\s+\"\"\"[^\"]*\"\"\"\s+allowed_roles = \['staff'\]",
    """class StaffOrManagerMixin(RoleRequiredMixin):
    required_permission = 'leave.view'
    action_type = 'view'""",
    c
)

old_leave_dispatch = """        from apps.accounts.engine import PermissionEngine
        res = PermissionEngine.evaluate(request.user, 'leave.approve')
        if not res.allowed and not request.user.is_superuser and getattr(request.user, 'role', '') not in ('admin', 'manager'):
            if PermissionEngine.evaluate(request.user, 'accounts.view').allowed or getattr(request.user, 'role', '') == 'admin':
                return redirect('/admin-panel/dashboard/')
            return redirect('/staff/home/')"""

new_leave_dispatch = """        from apps.accounts.engine import PermissionEngine
        res = PermissionEngine.evaluate(request.user, 'leave.approve')
        if not (res.allowed or request.user.is_superuser):
            from django.http import HttpResponseForbidden
            from django.template.loader import render_to_string
            content = render_to_string('cotton/permission_denied_hx.html', {'message': 'You do not have permission to approve leave requests.'}, request=request)
            return HttpResponseForbidden(content, content_type='text/html')"""

c = c.replace(old_leave_dispatch, new_leave_dispatch)

with open('apps/leave/views.py', 'w', encoding='utf-8') as f:
    f.write(c)
print("Patched leave views.py")

# 8. apps/notifications/views.py
with open('apps/notifications/views.py', 'r', encoding='utf-8') as f:
    c = f.read()
c = c.replace(
    "if not (request.user.is_superuser or PermissionEngine.evaluate(request.user, 'notifications.view').allowed or getattr(request.user, 'role', '') == 'admin'):",
    "if not (request.user.is_superuser or PermissionEngine.evaluate(request.user, 'notifications.view').allowed):"
)
c = c.replace(
    "is_admin = request.user.is_superuser or PermissionEngine.evaluate(request.user, 'notifications.view').allowed or getattr(request.user, 'role', '') == 'admin'",
    "is_admin = request.user.is_superuser or PermissionEngine.evaluate(request.user, 'notifications.view').allowed"
)
c = c.replace(
    "is_admin = request.user.is_superuser or PermissionEngine.evaluate(request.user, 'notifications.view').allowed or getattr(request.user, 'role', '') in ('admin', 'system_owner')",
    "is_admin = request.user.is_superuser or PermissionEngine.evaluate(request.user, 'notifications.view').allowed"
)
with open('apps/notifications/views.py', 'w', encoding='utf-8') as f:
    f.write(c)
print("Patched notifications views.py")

# 9. apps/payroll/services.py
with open('apps/payroll/services.py', 'r', encoding='utf-8') as f:
    c = f.read()
c = c.replace(
    "# We check if user is admin (checking is_staff or customized logic, since it's Django, we can check user.is_staff or role=='admin')\n        if not (user.is_staff or getattr(user, 'role', '') == 'admin' or user.is_superuser):",
    "from apps.accounts.engine import PermissionEngine\n        if not (user.is_superuser or PermissionEngine.evaluate(user, 'payroll.approve').allowed):"
)
with open('apps/payroll/services.py', 'w', encoding='utf-8') as f:
    f.write(c)
print("Patched payroll services.py")

# 10. apps/schedule/views.py
with open('apps/schedule/views.py', 'r', encoding='utf-8') as f:
    c = f.read()

SCHEDULE_CBV_PERMS = {
    'CalendarMonthView': ('schedule.view', 'view'),
    'ShiftScheduleView': ('schedule.view', 'view'),
    'ScheduleEventCreateView': ('schedule.add', 'add'),
    'ScheduleEventUpdateView': ('schedule.edit', 'edit'),
    'ScheduleEventDeleteView': ('schedule.delete', 'delete'),
}

for cls_name, (perm, act) in SCHEDULE_CBV_PERMS.items():
    if f"class {cls_name}" in c:
        pattern = rf"(class\s+{cls_name}\([^)]*\):)"
        if not re.search(rf"class\s+{cls_name}\([^)]*\):(?:\s+[\"'].*?[\"'])?\s+required_permission", c):
            replacement = rf"\1\n    required_permission = '{perm}'\n    action_type = '{act}'"
            c = re.sub(pattern, replacement, c, count=1)

c = re.sub(r"\s+allowed_roles\s*=\s*\[[^\]]+\]", "", c)

c = c.replace(
    "is_admin_or_manager = request.user.is_superuser or res.allowed or getattr(request.user, 'role', '') in ('admin', 'system_owner', 'manager')",
    "is_admin_or_manager = request.user.is_superuser or res.allowed"
)
c = c.replace(
    "is_admin = request.user.is_superuser or getattr(request.user, 'role', '') in ('admin', 'system_owner')",
    "from apps.accounts.rbac_models import DataScope\n        is_admin = request.user.is_superuser or (PermissionEngine.evaluate(request.user, 'schedule.edit').allowed and PermissionEngine.get_effective_scope(request.user, 'schedule.edit') == DataScope.GLOBAL)"
)
c = c.replace(
    "is_admin_or_manager = (\n            request.user.is_superuser or\n            getattr(request.user, 'role', '') in ('admin', 'system_owner', 'manager')\n        )",
    "is_admin_or_manager = (\n            request.user.is_superuser or\n            PermissionEngine.evaluate(request.user, 'schedule.manage').allowed or\n            PermissionEngine.evaluate(request.user, 'schedule.edit').allowed\n        )"
)

with open('apps/schedule/views.py', 'w', encoding='utf-8') as f:
    f.write(c)
print("Patched schedule views.py")

# 11. apps/staff/views.py
with open('apps/staff/views.py', 'r', encoding='utf-8') as f:
    c = f.read()
c = c.replace(
    "elif request.user.is_superuser or getattr(request.user, 'role', '') in ('admin', 'manager'):",
    "elif request.user.is_superuser or PermissionEngine.evaluate(request.user, 'projects.view').allowed:"
)
with open('apps/staff/views.py', 'w', encoding='utf-8') as f:
    f.write(c)
print("Patched staff views.py")

# 12. apps/workflow/services.py
with open('apps/workflow/services.py', 'r', encoding='utf-8') as f:
    c = f.read()
c = c.replace(
    "if actor != instance.initiated_by and not actor.is_superuser and getattr(actor, 'role', '') != 'admin':",
    "from apps.accounts.engine import PermissionEngine\n    if actor != instance.initiated_by and not actor.is_superuser and not PermissionEngine.evaluate(actor, 'workflow.edit').allowed:"
)
with open('apps/workflow/services.py', 'w', encoding='utf-8') as f:
    f.write(c)
print("Patched workflow services.py")

# 13. Templates
# templates/cotton/doc-list.html
with open('templates/cotton/doc-list.html', 'r', encoding='utf-8') as f:
    c = f.read()
c = c.replace(
    "{% if request.user.is_superuser or request.user.role == 'admin' or request.user.role == 'hr_manager' or request.user.role == 'hr' %}",
    "{% if request.user.is_superuser or 'employees.approve' in request.user.resolved_permissions or 'employees.edit' in request.user.resolved_permissions %}"
)
with open('templates/cotton/doc-list.html', 'w', encoding='utf-8') as f:
    f.write(c)

# templates/employees/master_detail.html
with open('templates/employees/master_detail.html', 'r', encoding='utf-8') as f:
    c = f.read()
c = c.replace(
    "{% if request.user.is_superuser or request.user.role == 'admin' or request.user.role == 'hr_manager' or request.user.role == 'hr' %}",
    "{% if request.user.is_superuser or 'employees.edit' in request.user.resolved_permissions %}"
)
c = c.replace(
    "{% if request.user.is_superuser or request.user.role == 'admin' %}",
    "{% if request.user.is_superuser or 'audit.view' in request.user.resolved_permissions %}"
)
with open('templates/employees/master_detail.html', 'w', encoding='utf-8') as f:
    f.write(c)

# templates/notifications/list.html & partial
with open('templates/notifications/list.html', 'r', encoding='utf-8') as f:
    c = f.read()
c = c.replace(
    "{% if request.user.is_superuser or request.user.role == 'admin' or 'notifications.view' in request.user.resolved_permissions %}",
    "{% if request.user.is_superuser or 'notifications.view' in request.user.resolved_permissions %}"
)
with open('templates/notifications/list.html', 'w', encoding='utf-8') as f:
    f.write(c)

with open('templates/notifications/partials/list_partial.html', 'r', encoding='utf-8') as f:
    c = f.read()
c = c.replace(
    "{% if request.user.is_superuser or request.user.role == 'admin' or 'notifications.view' in request.user.resolved_permissions %}",
    "{% if request.user.is_superuser or 'notifications.view' in request.user.resolved_permissions %}"
)
with open('templates/notifications/partials/list_partial.html', 'w', encoding='utf-8') as f:
    f.write(c)

# templates/payroll/payroll_run_detail.html
with open('templates/payroll/payroll_run_detail.html', 'r', encoding='utf-8') as f:
    c = f.read()
c = c.replace(
    "{% if request.user.is_superuser or request.user.role == 'admin' or request.user.role == 'system_owner' %}",
    "{% if request.user.is_superuser or 'payroll.approve' in request.user.resolved_permissions %}"
)
with open('templates/payroll/payroll_run_detail.html', 'w', encoding='utf-8') as f:
    f.write(c)

# templates/projects/global_task_list.html
with open('templates/projects/global_task_list.html', 'r', encoding='utf-8') as f:
    c = f.read()
c = c.replace(
    "{% if request.user.is_superuser or request.user.role == 'admin' or request.user.role == 'manager' or 'projects.edit' in request.user.resolved_permissions %}",
    "{% if request.user.is_superuser or 'projects.approve' in request.user.resolved_permissions or 'projects.edit' in request.user.resolved_permissions %}"
)
with open('templates/projects/global_task_list.html', 'w', encoding='utf-8') as f:
    f.write(c)

# templates/staff/timeline.html
with open('templates/staff/timeline.html', 'r', encoding='utf-8') as f:
    c = f.read()
c = c.replace(
    "{% if request.user.role in 'admin,manager' or request.user.is_superuser %}",
    "{% if 'attendance.view' in request.user.resolved_permissions or request.user.is_superuser %}"
)
with open('templates/staff/timeline.html', 'w', encoding='utf-8') as f:
    f.write(c)

print("All application and template patches successfully applied.")
