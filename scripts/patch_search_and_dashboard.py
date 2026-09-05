import re

# 1. apps/accounts/context_processors.py
with open('apps/accounts/context_processors.py', 'r', encoding='utf-8') as f:
    c = f.read()
c = c.replace(
    "if getattr(request.user, 'role', '') == 'admin':",
    "from apps.accounts.engine import PermissionEngine\n        if request.user.is_superuser or PermissionEngine.evaluate(request.user, 'dashboard.view').allowed:"
)
with open('apps/accounts/context_processors.py', 'w', encoding='utf-8') as f:
    f.write(c)
print("Patched accounts context_processors.py")

# 2. apps/admin_panel/dashboard_services.py
with open('apps/admin_panel/dashboard_services.py', 'r', encoding='utf-8') as f:
    c = f.read()

old_variant = """    # Admin check (superuser or accounts.view/edit permission or role=='admin')
    if user.is_superuser or PermissionEngine.evaluate(user, 'accounts.view').allowed or getattr(user, 'role', '') == 'admin':
        return 'admin'

    # HR check (hr permission or role in hr roles)
    if PermissionEngine.evaluate(user, 'employees.view').allowed and PermissionEngine.evaluate(user, 'leave.approve').allowed:
        return 'hr'
    if getattr(user, 'role', '') in ('hr', 'hr_manager', 'hr_admin'):
        return 'hr'

    # Manager check (projects/leave approve permission or role=='manager' or user has direct reports)
    is_manager_role = getattr(user, 'role', '') == 'manager' or PermissionEngine.evaluate(user, 'leave.approve').allowed"""

new_variant = """    from apps.accounts.rbac_models import DataScope
    # Admin check (superuser or accounts.view/edit permission or global dashboard scope)
    if user.is_superuser or PermissionEngine.evaluate(user, 'accounts.view').allowed or (PermissionEngine.evaluate(user, 'dashboard.view').allowed and PermissionEngine.get_effective_scope(user, 'dashboard.view') == DataScope.GLOBAL):
        return 'admin'

    # HR check (employees.view and leave.approve permission)
    if PermissionEngine.evaluate(user, 'employees.view').allowed and PermissionEngine.evaluate(user, 'leave.approve').allowed:
        return 'hr'

    # Manager check (projects/leave approve permission or user has direct reports)
    is_manager_role = PermissionEngine.evaluate(user, 'leave.approve').allowed or PermissionEngine.evaluate(user, 'projects.approve').allowed"""

c = c.replace(old_variant, new_variant)
with open('apps/admin_panel/dashboard_services.py', 'w', encoding='utf-8') as f:
    f.write(c)
print("Patched admin_panel dashboard_services.py")

# 3. apps/admin_panel/roles_views.py
with open('apps/admin_panel/roles_views.py', 'r', encoding='utf-8') as f:
    c = f.read()
c = c.replace(
    "can_edit = user.is_superuser or PermissionEngine.evaluate(user, 'accounts.edit').allowed or getattr(user, 'role', '') in ('admin', 'system_owner')",
    "can_edit = user.is_superuser or PermissionEngine.evaluate(user, 'accounts.edit').allowed"
)
with open('apps/admin_panel/roles_views.py', 'w', encoding='utf-8') as f:
    f.write(c)
print("Patched admin_panel roles_views.py")

# 4. apps/employees/api_views.py
with open('apps/employees/api_views.py', 'r', encoding='utf-8') as f:
    c = f.read()
c = c.replace(
    "if requesting_user.is_superuser or getattr(requesting_user, 'role', '') == 'admin':\n        return True",
    "if requesting_user.is_superuser:\n        return True"
)
with open('apps/employees/api_views.py', 'w', encoding='utf-8') as f:
    f.write(c)
print("Patched employees api_views.py")

# 5. apps/admin_panel/ai_service.py
with open('apps/admin_panel/ai_service.py', 'r', encoding='utf-8') as f:
    c = f.read()
old_ai_fallback = """    # Fallback to direct role attribute
    role = getattr(user, 'role', None)
    if role in ('admin', 'system_owner'):
        return 'admin'
    if role == 'manager':
        return 'manager'
    if role in ('staff', 'employee'):
        return role"""

new_ai_fallback = """    # Fallback to PermissionEngine resolved permissions
    from apps.accounts.engine import PermissionEngine
    if PermissionEngine.evaluate(user, 'dashboard.view').allowed:
        return 'admin'
    if PermissionEngine.evaluate(user, 'projects.view').allowed or PermissionEngine.evaluate(user, 'attendance.approve').allowed:
        return 'manager'"""

c = c.replace(old_ai_fallback, new_ai_fallback)
with open('apps/admin_panel/ai_service.py', 'w', encoding='utf-8') as f:
    f.write(c)
print("Patched admin_panel ai_service.py")

# 6. apps/accounts/search_service.py
with open('apps/accounts/search_service.py', 'r', encoding='utf-8') as f:
    c = f.read()

# can_view_roles
old_cvr = """        user_role_codes = set()
        if hasattr(user, 'role') and user.role:
            user_role_codes.add(user.role)
        if hasattr(user, 'role_assignments'):
            user_role_codes.update(
                user.role_assignments.filter(role__is_active=True).values_list('role__code', flat=True)
            )

        return bool(user_role_codes.intersection({'system_owner', 'super_admin', 'admin'}))"""

new_cvr = """        user_role_codes = set()
        if hasattr(user, 'role_assignments'):
            user_role_codes.update(
                user.role_assignments.filter(role__is_active=True).values_list('role__code', flat=True)
            )
        return bool(user_role_codes.intersection({'system_owner', 'super_admin', 'admin'}))"""
c = c.replace(old_cvr, new_cvr)

# can_edit_roles
old_cer = """        user_role_codes = set()
        if hasattr(user, 'role') and user.role:
            user_role_codes.add(user.role)
        if hasattr(user, 'role_assignments'):
            user_role_codes.update(
                user.role_assignments.filter(role__is_active=True).values_list('role__code', flat=True)
            )

        return bool(user_role_codes.intersection({'system_owner', 'super_admin', 'admin'}))"""
c = c.replace(old_cer, new_cvr)

# has_module_view
old_hmv = """        role = getattr(user, 'role', '')
        if role in ('admin', 'system_owner'):
            return True
        if module_code in ('attendance', 'leave', 'expense', 'schedule', 'projects'):
            return True
        if module_code == 'employees' and role in ('manager', 'hr'):
            return True
        if module_code == 'payroll' and role in ('finance', 'accounts'):
            return True
        if module_code in ('branches', 'backups') and role == 'manager':
            return True
        return False"""

new_hmv = """        return False"""
c = c.replace(old_hmv, new_hmv)

# build_navigation_catalog
c = c.replace(
    "role = getattr(user, 'role', '')\n        is_admin_or_mgr = user.is_superuser or role in ('admin', 'system_owner', 'manager', 'hr')\n        is_payroll_auth = user.is_superuser or cls.has_module_view(user, 'payroll') or role in ('admin', 'system_owner', 'finance', 'accounts')\n        is_security_auth = user.is_superuser or cls.has_module_view(user, 'accounts') or role in ('admin', 'system_owner')",
    "is_admin_or_mgr = user.is_superuser or cls.has_module_view(user, 'dashboard')\n        is_payroll_auth = user.is_superuser or cls.has_module_view(user, 'payroll')\n        is_security_auth = user.is_superuser or cls.has_module_view(user, 'accounts')"
)

# global_search
c = c.replace("role = getattr(user, 'role', '')\n        emp = getattr(user, 'employee_master', None)", "emp = getattr(user, 'employee_master', None)")
c = c.replace(
    "if role in ('admin', 'system_owner', 'hr', 'manager') or user.is_superuser:",
    "if cls.has_module_view(user, 'employees'):"
)
c = c.replace(
    "if role in ('admin', 'system_owner', 'finance', 'accounts') or user.is_superuser:",
    "if cls.has_module_view(user, 'payroll'):"
)

with open('apps/accounts/search_service.py', 'w', encoding='utf-8') as f:
    f.write(c)
print("Patched accounts search_service.py")
