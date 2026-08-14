from django.urls import reverse

PINNABLE_MENUS = {
    "employee_directory": {
        "label": "Employee Directory",
        "url_name": "employees:employee_list",
        "icon": "users",
        "roles": ["admin", "system_owner", "hr", "manager"],
        "is_superuser_only": False,
    },
    "new_employee": {
        "label": "New Employee",
        "url_name": "employees:employee_add",
        "icon": "user-plus",
        "roles": ["admin", "system_owner", "hr"],
        "is_superuser_only": False,
    },
    "departments": {
        "label": "Departments",
        "url_name": "branches:branch_list",
        "icon": "building",
        "roles": ["admin", "system_owner", "hr"],
        "is_superuser_only": False,
        "url_params": "?type=department",
    },
    "designations": {
        "label": "Designations",
        "url_name": "admin_panel:role_list",
        "icon": "shield",
        "roles": ["admin", "system_owner", "hr"],
        "is_superuser_only": False,
        "url_params": "?type=designation",
    },
    "live_attendance": {
        "label": "Live Attendance",
        "url_name": "attendance:status",
        "icon": "clock",
        "roles": ["admin", "system_owner", "hr", "manager"],
        "is_superuser_only": False,
    },
    "attendance_logs": {
        "label": "Attendance Logs",
        "url_name": "admin_panel:attendance_list",
        "icon": "file-text",
        "roles": ["admin", "system_owner", "hr", "manager"],
        "is_superuser_only": False,
    },
    "manual_attendance": {
        "label": "Manual Attendance",
        "url_name": "admin_panel:manual_entry",
        "icon": "edit-3",
        "roles": ["admin", "system_owner", "hr", "manager"],
        "is_superuser_only": False,
    },
    "leave_requests": {
        "label": "Leave Requests",
        "url_name": "leave:admin_dashboard",
        "icon": "clipboard-check",
        "roles": ["admin", "system_owner", "hr", "manager"],
        "is_superuser_only": False,
    },
    "leave_types": {
        "label": "Leave Types",
        "url_name": "leave:admin_leave_types",
        "icon": "list",
        "roles": ["admin", "system_owner", "hr"],
        "is_superuser_only": False,
    },
    "leave_balance": {
        "label": "Leave Balance",
        "url_name": "leave:admin_balances",
        "icon": "award",
        "roles": ["admin", "system_owner", "hr"],
        "is_superuser_only": False,
    },
    "shift_schedule": {
        "label": "Shift Schedule",
        "url_name": "schedule:month_view",
        "icon": "calendar",
        "roles": ["admin", "system_owner", "hr", "manager"],
        "is_superuser_only": False,
    },
    "all_projects": {
        "label": "All Projects",
        "url_name": "projects:project_list",
        "icon": "briefcase",
        "roles": ["admin", "system_owner", "hr", "manager", "staff"],
        "is_superuser_only": False,
    },
    "payroll_runs": {
        "label": "Payroll Runs",
        "url_name": "payroll:payroll_run_list",
        "icon": "credit-card",
        "roles": ["admin", "system_owner", "hr", "finance", "accounts"],
        "is_superuser_only": False,
    },
    "salary_components": {
        "label": "Salary Components",
        "url_name": "payroll:salary_components",
        "icon": "settings",
        "roles": ["admin", "system_owner", "hr", "finance", "accounts"],
        "is_superuser_only": False,
    },
    "expenses": {
        "label": "Expenses",
        "url_name": "expense:admin_expense_list",
        "icon": "receipt",
        "roles": ["admin", "system_owner", "hr", "manager", "finance", "accounts", "staff"],
        "is_superuser_only": False,
    },
    "trash": {
        "label": "Trash",
        "url_name": "audit:trash_list",
        "icon": "trash-2",
        "roles": ["admin", "system_owner", "hr", "manager"],
        "is_superuser_only": False,
    },
}

def can_view_menu(user, menu_key):
    if menu_key not in PINNABLE_MENUS:
        return False
    if user.is_superuser:
        return True
    cfg = PINNABLE_MENUS[menu_key]
    if cfg.get("is_superuser_only", False):
        return False
    user_role = getattr(user, "role", "")
    return user_role in cfg.get("roles", [])

def get_menu_url(menu_key):
    cfg = PINNABLE_MENUS.get(menu_key)
    if not cfg:
        return "#"
    url = reverse(cfg["url_name"])
    if cfg.get("url_params"):
        url += cfg["url_params"]
    return url
