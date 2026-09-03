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
        "url_name": "employees:department_list",
        "icon": "building",
        "roles": ["admin", "system_owner", "hr"],
        "is_superuser_only": False,
    },
    "designations": {
        "label": "Designations",
        "url_name": "employees:designation_list",
        "icon": "shield",
        "roles": ["admin", "system_owner", "hr"],
        "is_superuser_only": False,
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
        "label": "Calendar",
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
    "reports_hub": {
        "label": "Reports Hub",
        "url_name": "admin_panel:reports_main",
        "icon": "bar-chart-2",
        "roles": ["admin", "system_owner", "hr", "manager"],
        "is_superuser_only": False,
    }, 
    "daily_report": {
        "label": "Daily Report",
        "url_name": "admin_panel:reports_daily",
        "icon": "file-text",
        "roles": ["admin", "system_owner", "hr", "manager"],
        "is_superuser_only": False,
    },
    "monthly_report": {
        "label": "Monthly Report",
        "url_name": "admin_panel:reports_monthly",
        "icon": "calendar",
        "roles": ["admin", "system_owner", "hr", "manager"],
        "is_superuser_only": False,
    },
    "employee_report": {
        "label": "Employee Report",
        "url_name": "admin_panel:reports_main",
        "icon": "user",
        "roles": ["admin", "system_owner", "hr", "manager"],
        "is_superuser_only": False,
    },
    "absence_report": {
        "label": "Absence Report",
        "url_name": "admin_panel:reports_absent",
        "icon": "user-minus",
        "roles": ["admin", "system_owner", "hr", "manager"],
        "is_superuser_only": False,
    },
    "active_projects": {
        "label": "Active Projects",
        "url_name": "projects:project_list",
        "icon": "folder",
        "roles": ["admin", "system_owner", "hr", "manager", "staff"],
        "is_superuser_only": False,
        "url_params": "?status=active",
    },
    "completed_projects": {
        "label": "Completed Projects",
        "url_name": "projects:project_list",
        "icon": "folder-check",
        "roles": ["admin", "system_owner", "hr", "manager", "staff"],
        "is_superuser_only": False,
        "url_params": "?status=completed",
    },
    "my_tasks": {
        "label": "My Tasks",
        "url_name": "staff:my_tasks",
        "icon": "check-square",
        "roles": ["admin", "system_owner", "hr", "manager", "staff"],
        "is_superuser_only": False,
    },
    "team_tasks": {
        "label": "Team Tasks",
        "url_name": "projects:global_task_list",
        "icon": "users",
        "roles": ["admin", "system_owner", "hr", "manager", "staff"],
        "is_superuser_only": False,
    },
    "task_board": {
        "label": "Task Board",
        "url_name": "projects:global_task_list",
        "icon": "grid",
        "roles": ["admin", "system_owner", "hr", "manager", "staff"],
        "is_superuser_only": False,
        "url_params": "?view=kanban",
    },
    "project_types": {
        "label": "Project Types",
        "url_name": "projects:project_type_list",
        "icon": "tag",
        "roles": ["admin", "system_owner", "hr", "manager", "staff"],
        "is_superuser_only": False,
    },
    "task_templates": {
        "label": "Task Templates",
        "url_name": "projects:template_list",
        "icon": "clipboard-list",
        "roles": ["admin", "system_owner", "hr", "manager", "staff"],
        "is_superuser_only": False,
    },
    "salary_structures": {
        "label": "Salary Structures",
        "url_name": "payroll:salary_structures",
        "icon": "layers",
        "roles": ["admin", "system_owner", "hr", "finance", "accounts"],
        "is_superuser_only": False,
    },
    "employee_salary_setup": {
        "label": "Employee Salary Setup",
        "url_name": "payroll:employee_salary_setup",
        "icon": "user-check",
        "roles": ["admin", "system_owner", "hr", "finance", "accounts"],
        "is_superuser_only": False,
    },
    "payroll_reports": {
        "label": "Payroll Reports",
        "url_name": "payroll:reports_hub",
        "icon": "file-bar-chart",
        "roles": ["admin", "system_owner", "hr", "finance", "accounts"],
        "is_superuser_only": False,
    },
    "my_payslips": {
        "label": "My Payslips",
        "url_name": "payroll:my_payslips",
        "icon": "file-text",
        "roles": ["admin", "system_owner", "hr", "finance", "accounts", "staff", "manager"],
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
