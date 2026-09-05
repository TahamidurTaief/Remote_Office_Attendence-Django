import logging
import re
from typing import Dict, List, Optional, Set, Tuple
from django.urls import reverse, NoReverseMatch
from django.db.models import Q

logger = logging.getLogger(__name__)


# Bengali to English search keywords normalization
BANGLA_SYNONYMS = {
    'রোল': ['role', 'roles', 'system_roles'],
    'ভূমিকা': ['role', 'roles'],
    'পদবী': ['designation', 'role', 'roles'],
    'অনুমতি': ['permission', 'permissions'],
    'পারমিশন': ['permission', 'permissions'],
    'অ্যাক্সেস': ['access', 'permissions', 'roles'],
    'প্রবেশাধিকার': ['access'],
    'ম্যাট্রিক্স': ['matrix', 'access matrix', 'permission matrix'],
    'মডিউল': ['module', 'modules'],
    'মেনু': ['menu', 'menus'],
    'সাবমেনু': ['submenu', 'submenus'],
    'হাজিরা': ['attendance'],
    'উপস্থিতি': ['attendance'],
    'কর্মচারী': ['employee', 'employees'],
    'স্টাফ': ['staff', 'employee'],
    'ছুটি': ['leave'],
    'খরচ': ['expense', 'expenses'],
    'বেতন': ['payroll', 'salary', 'payslip'],
    'পেরোল': ['payroll'],
    'প্রকল্প': ['project', 'projects'],
    'টাস্ক': ['task', 'tasks'],
    'কাজ': ['task', 'tasks', 'work'],
    'নিরাপত্তা': ['security', 'policies'],
    'পলিসি': ['policy', 'policies'],
    'শাখা': ['branch', 'branches'],
    'লগ': ['log', 'logs', 'audit'],
    'অডিট': ['audit'],
    'ব্যাকআপ': ['backup', 'backups'],
    'সেটিংস': ['settings'],
    'ড্যাশবোর্ড': ['dashboard'],
    'পাসওয়ার্ড': ['password'],
    'সেশন': ['session', 'sessions'],
    'বিভাগ': ['department'],
    'অ্যাসেট': ['asset', 'hardware'],
}


class GlobalSearchService:
    """
    Unified, permission-aware Global Search service.
    - Searches System Roles, Access Matrix, Permissions, Modules, Submodules, Menus, and Submenus.
    - Preserves existing operational search (Employees, Projects, Tasks, Payroll, Leaves, Expenses).
    - Strictly enforces PermissionEngine and Role scoping (fails closed).
    - Protects sensitive internal identifiers, metadata, and protected roles (system_owner).
    - Handles query normalization (whitespace, punctuation, case, Bangla/English synonyms).
    - Enforces deduplication and intelligent relevance ranking.
    - Robust against broken route names or stale data.
    """

    @classmethod
    def safe_reverse(cls, viewname: str, args=None, kwargs=None, fallback: str = "") -> Optional[str]:
        """Safely resolve reverse URL name. If viewname is broken or stale, return fallback or None."""
        try:
            return reverse(viewname, args=args, kwargs=kwargs)
        except NoReverseMatch:
            return fallback if fallback else None
        except Exception as e:
            logger.warning("Error reversing route %s: %s", viewname, e)
            return fallback if fallback else None

    @classmethod
    def can_view_roles(cls, user) -> bool:
        """Check if user has effective permission to view and administer system roles."""
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return True

        from apps.accounts.engine import PermissionEngine
        eval_res = PermissionEngine.evaluate(user=user, codename='accounts.view', action_type='view')
        if eval_res.allowed:
            return True

        user_role_codes = set()
        if hasattr(user, 'role_assignments'):
            user_role_codes.update(
                user.role_assignments.filter(role__is_active=True).values_list('role__code', flat=True)
            )
        return bool(user_role_codes.intersection({'system_owner', 'super_admin', 'admin'}))

    @classmethod
    def can_edit_roles(cls, user) -> bool:
        """Check if user has effective permission to create or edit system roles."""
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return True

        from apps.accounts.engine import PermissionEngine
        eval_res = PermissionEngine.evaluate(user=user, codename='accounts.edit', action_type='edit')
        if eval_res.allowed:
            return True

        user_role_codes = set()
        if hasattr(user, 'role_assignments'):
            user_role_codes.update(
                user.role_assignments.filter(role__is_active=True).values_list('role__code', flat=True)
            )
        return bool(user_role_codes.intersection({'system_owner', 'super_admin', 'admin'}))

    @classmethod
    def has_module_view(cls, user, module_code: str) -> bool:
        """Check if user has view permission or appropriate role persona for given module."""
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return True

        from apps.accounts.engine import PermissionEngine
        eval_res = PermissionEngine.evaluate(user=user, codename=f"{module_code}.view", action_type='view')
        if eval_res.allowed:
            return True
        return False

    @classmethod
    def normalize_query(cls, query: str) -> Tuple[str, List[str]]:
        """Normalize raw query string and return clean query and expanded token list."""
        if not query:
            return "", []

        cleaned = re.sub(r'[\t\r\n]+', ' ', query).strip()
        cleaned_lower = cleaned.lower()
        tokens = [t for t in cleaned_lower.split() if t]

        # Expand tokens with Bangla synonyms
        expanded_tokens = list(tokens)
        for token in tokens:
            if token in BANGLA_SYNONYMS:
                expanded_tokens.extend(BANGLA_SYNONYMS[token])

        return cleaned, expanded_tokens

    @classmethod
    def get_navigation_catalog(cls, user) -> List[Dict]:
        """
        Builds the permission-filtered navigation catalog (menus, submenus, modules, and destinations).
        Fails closed on unauthorized destinations.
        """
        can_roles = cls.can_view_roles(user)
        can_roles_edit = cls.can_edit_roles(user)
        is_admin_or_mgr = user.is_superuser or cls.has_module_view(user, 'dashboard')
        is_payroll_auth = user.is_superuser or cls.has_module_view(user, 'payroll')
        is_security_auth = user.is_superuser or cls.has_module_view(user, 'accounts')

        items = []

        # ── 1. Roles & Access Controls ──────────────────────────────────
        if can_roles:
            url_roles = cls.safe_reverse('admin_panel:role_list', fallback='/admin-panel/roles/')
            if url_roles:
                items.append({
                    'label': 'System Roles & Access',
                    'href': url_roles,
                    'icon': 'shield',
                    'group': 'Roles & Access',
                    'description': 'Manage system roles, access permissions, and delegations',
                    'keywords': ['role', 'roles', 'system roles', 'roles and access', 'manage roles', 'access', 'rbac', 'menu', 'submenu', 'admin'],
                })

            url_matrix = cls.safe_reverse('admin_panel:permission_matrix', fallback='/admin-panel/permissions/matrix/')
            if url_matrix:
                items.append({
                    'label': 'Access Matrix',
                    'href': url_matrix,
                    'icon': 'grid',
                    'group': 'Roles & Access',
                    'description': 'Global permission matrix and role permission matrix overview',
                    'keywords': ['matrix', 'access matrix', 'permission matrix', 'role matrix', 'permissions matrix', 'rbac matrix', 'menu', 'submenu'],
                })

            if can_roles_edit:
                url_add_role = cls.safe_reverse('admin_panel:role_create', fallback='/admin-panel/roles/add/')
                if url_add_role:
                    items.append({
                        'label': 'Add New Role',
                        'href': url_add_role,
                        'icon': 'plus-circle',
                        'group': 'Roles & Access',
                        'description': 'Create and configure a new system role',
                        'keywords': ['add role', 'create role', 'new role', 'role', 'submenu'],
                    })

        if is_security_auth:
            url_sec_dash = cls.safe_reverse('admin_panel:security_dashboard', fallback='/admin-panel/security-dashboard/')
            if url_sec_dash:
                items.append({
                    'label': 'Security Dashboard',
                    'href': url_sec_dash,
                    'icon': 'shield-alert',
                    'group': 'Administration',
                    'description': 'System security overview, MFA status, and audit metrics',
                    'keywords': ['security', 'security dashboard', 'mfa overview', 'menu'],
                })

            url_sec_pol = cls.safe_reverse('accounts:admin_security_policies', fallback='/security/policies/')
            if url_sec_pol:
                items.append({
                    'label': 'Security Policies',
                    'href': url_sec_pol,
                    'icon': 'lock',
                    'group': 'Administration',
                    'description': 'Session timeout, password rules, and lockout policies',
                    'keywords': ['security policies', 'password policy', 'session policy', 'lockout policy', 'submenu'],
                })

            url_login_act = cls.safe_reverse('accounts:admin_login_activity', fallback='/login-activity/')
            if url_login_act:
                items.append({
                    'label': 'Login Activity',
                    'href': url_login_act,
                    'icon': 'activity',
                    'group': 'Administration',
                    'description': 'Audit user authentications, failures, and device sessions',
                    'keywords': ['login activity', 'logins', 'login logs', 'failed logins', 'submenu'],
                })

            url_audit_logs = cls.safe_reverse('admin_panel:admin_audit_logs', fallback='/admin-panel/audit-logs/')
            if url_audit_logs:
                items.append({
                    'label': 'Audit Logs',
                    'href': url_audit_logs,
                    'icon': 'file-clock',
                    'group': 'Administration',
                    'description': 'Platform security audit events and change records',
                    'keywords': ['audit logs', 'audit', 'compliance logs', 'menu', 'submenu'],
                })

            url_activity_logs = cls.safe_reverse('audit:activity_list', fallback='/audit/activity/')
            if url_activity_logs:
                items.append({
                    'label': 'Activity Logs',
                    'href': url_activity_logs,
                    'icon': 'history',
                    'group': 'Administration',
                    'description': 'Employee activity timeline and operation history',
                    'keywords': ['activity logs', 'user activity', 'activity feed', 'submenu'],
                })

        if user.is_superuser or cls.has_module_view(user, 'audit'):
            url_trash = cls.safe_reverse('audit:trash_list', fallback='/audit/trash/')
            if url_trash:
                items.append({
                    'label': 'Trash & Recycle Bin',
                    'href': url_trash,
                    'icon': 'trash-2',
                    'group': 'Administration',
                    'description': 'Soft-deleted records, restore queue, and recovery',
                    'keywords': ['trash', 'recycle bin', 'deleted items', 'restore', 'submenu'],
                })

        if user.is_superuser or cls.has_module_view(user, 'backups'):
            url_backups = cls.safe_reverse('backups:backup_list', fallback='/backups/')
            if url_backups:
                items.append({
                    'label': 'System Backups',
                    'href': url_backups,
                    'icon': 'hard-drive',
                    'group': 'Administration',
                    'description': 'Database backups, automated snapshots, and Google Drive sync',
                    'keywords': ['backups', 'backup', 'database backup', 'system backups', 'menu'],
                })

        # ── 2. User Account & Security ──────────────────────────────────
        url_user_sess = cls.safe_reverse('accounts:user_sessions', fallback='/account/sessions/')
        if url_user_sess:
            items.append({
                'label': 'User Sessions',
                'href': url_user_sess,
                'icon': 'monitor',
                'group': 'Account',
                'description': 'Review and terminate active device sessions',
                'keywords': ['user sessions', 'active sessions', 'sessions', 'devices', 'menu', 'submenu'],
            })

        url_sec_settings = cls.safe_reverse('accounts:security_settings', fallback='/account/security/')
        if url_sec_settings:
            items.append({
                'label': 'Security & MFA Settings',
                'href': url_sec_settings,
                'icon': 'key',
                'group': 'Account',
                'description': 'Configure two-factor authentication and security credentials',
                'keywords': ['security settings', 'mfa settings', '2fa', 'two factor', 'authenticator', 'menu', 'submenu'],
            })

        url_change_pass = cls.safe_reverse('accounts:change_password', fallback='/accounts/change-password/')
        if url_change_pass:
            items.append({
                'label': 'Change Password',
                'href': url_change_pass,
                'icon': 'lock',
                'group': 'Account',
                'description': 'Update your account login password',
                'keywords': ['change password', 'password', 'update password', 'reset password', 'menu', 'submenu'],
            })

        # ── 3. Overview & Dashboards ────────────────────────────────────
        if is_admin_or_mgr or cls.has_module_view(user, 'payroll'):
            url_dash = cls.safe_reverse('admin_panel:admin_dashboard', fallback='/admin-panel/dashboard/')
            if url_dash:
                items.append({
                    'label': 'Executive Dashboard',
                    'href': url_dash,
                    'icon': 'layout-dashboard',
                    'group': 'Overview',
                    'description': 'High-level operational metrics and organizational KPIs',
                    'keywords': ['dashboard', 'executive dashboard', 'admin dashboard', 'analytics', 'menu'],
                })

        url_staff_home = cls.safe_reverse('staff:home', fallback='/staff/home/')
        if url_staff_home:
            items.append({
                'label': 'Staff Home / Check In',
                'href': url_staff_home,
                'icon': 'user-check',
                'group': 'Overview',
                'description': 'Daily attendance check-in, shift info, and quick actions',
                'keywords': ['staff home', 'check in', 'attendance check in', 'clock in', 'my attendance', 'menu'],
            })

        url_notifs = cls.safe_reverse('notifications:list', fallback='/notifications/')
        if url_notifs:
            items.append({
                'label': 'Recent Activity & Notifications',
                'href': url_notifs,
                'icon': 'bell',
                'group': 'Overview',
                'description': 'System notifications, broadcast alerts, and activity feed',
                'keywords': ['notifications', 'recent activity', 'alerts', 'inbox', 'menu', 'submenu'],
            })

        # ── 4. People & Employees ───────────────────────────────────────
        if user.is_superuser or cls.has_module_view(user, 'employees'):
            url_emp_dir = cls.safe_reverse('employees:employee_list', fallback='/employees/')
            if url_emp_dir:
                items.append({
                    'label': 'Employee Directory',
                    'href': url_emp_dir,
                    'icon': 'users',
                    'group': 'People',
                    'description': 'Browse and manage all registered employees',
                    'keywords': ['employees', 'employee directory', 'staff directory', 'staff list', 'employee list', 'peoples', 'menu'],
                })

            if user.is_superuser or PermissionEngine.evaluate(user, 'employees.add').allowed:
                url_add_emp = cls.safe_reverse('employees:employee_wizard_create', fallback='/employees/add/')
                if url_add_emp:
                    items.append({
                        'label': 'Add New Employee',
                        'href': url_add_emp,
                        'icon': 'user-plus',
                        'group': 'People',
                        'description': 'Onboard a new employee with the 8-step wizard',
                        'keywords': ['add employee', 'create employee', 'new employee', 'hire employee', 'submenu'],
                    })

            url_depts = cls.safe_reverse('employees:department_list', fallback='/employees/departments/')
            if url_depts:
                items.append({
                    'label': 'Departments',
                    'href': url_depts,
                    'icon': 'building',
                    'group': 'People',
                    'description': 'Organizational departments and division hierarchy',
                    'keywords': ['departments', 'department list', 'depts', 'submenu'],
                })

            url_desigs = cls.safe_reverse('employees:designation_list', fallback='/employees/designations/')
            if url_desigs:
                items.append({
                    'label': 'Designations',
                    'href': url_desigs,
                    'icon': 'award',
                    'group': 'People',
                    'description': 'Official job titles, rankings, and pay levels',
                    'keywords': ['designations', 'designation list', 'job titles', 'positions', 'submenu'],
                })

            url_org = cls.safe_reverse('employees:org_chart', fallback='/employees/org-chart/')
            if url_org:
                items.append({
                    'label': 'Organization Chart',
                    'href': url_org,
                    'icon': 'git-fork',
                    'group': 'People',
                    'description': 'Visual organizational reporting tree and manager hierarchy',
                    'keywords': ['org chart', 'organization chart', 'hierarchy', 'reporting structure', 'submenu'],
                })

            url_delegations = cls.safe_reverse('employees:delegation_list', fallback='/employees/delegations/')
            if url_delegations:
                items.append({
                    'label': 'Employee Delegations',
                    'href': url_delegations,
                    'icon': 'user-check',
                    'group': 'People',
                    'description': 'Temporary authority delegations and approval routing',
                    'keywords': ['delegations', 'employee delegations', 'approval delegation', 'submenu'],
                })

            url_assets = cls.safe_reverse('employees:asset_list', fallback='/employees/assets/')
            if url_assets:
                items.append({
                    'label': 'Hardware & Assets',
                    'href': url_assets,
                    'icon': 'laptop',
                    'group': 'People',
                    'description': 'Company equipment, laptop inventory, and employee assignments',
                    'keywords': ['assets', 'hardware', 'asset list', 'devices', 'laptops', 'inventory', 'submenu'],
                })

        # ── 5. Operations & Scheduling ──────────────────────────────────
        if user.is_superuser or cls.has_module_view(user, 'attendance'):
            url_att_admin = cls.safe_reverse('admin_panel:attendance_list', fallback='/admin-panel/attendance/')
            if url_att_admin:
                items.append({
                    'label': 'Attendance Sessions',
                    'href': url_att_admin,
                    'icon': 'clock',
                    'group': 'Operations',
                    'description': 'Live check-in records, GPS geofencing, and shift logs',
                    'keywords': ['attendance', 'attendance sessions', 'attendance logs', 'timesheets', 'menu'],
                })

            url_att_reqs = cls.safe_reverse('attendance:attendance_requests_list', fallback='/attendance/requests/')
            if url_att_reqs:
                items.append({
                    'label': 'Attendance Requests',
                    'href': url_att_reqs,
                    'icon': 'calendar-check',
                    'group': 'Operations',
                    'description': 'Pending attendance correction and manual clock-in requests',
                    'keywords': ['attendance requests', 'correction requests', 'attendance correction', 'submenu'],
                })

        if user.is_superuser or cls.has_module_view(user, 'schedule'):
            url_shifts = cls.safe_reverse('schedule:shift_schedule', fallback='/schedule/shifts/')
            if url_shifts:
                items.append({
                    'label': 'Shift Schedule',
                    'href': url_shifts,
                    'icon': 'calendar-clock',
                    'group': 'Operations',
                    'description': 'Weekly and monthly shift rosters, patterns, and assignments',
                    'keywords': ['shifts', 'shift schedule', 'roster', 'work shifts', 'schedule', 'submenu'],
                })

        url_calendar = cls.safe_reverse('schedule:calendar_month', fallback='/schedule/calendar/')
        if url_calendar:
            items.append({
                'label': 'Calendar Month',
                'href': url_calendar,
                'icon': 'calendar',
                'group': 'Operations',
                'description': 'Company calendar view with holidays, shifts, and events',
                'keywords': ['calendar', 'calendar month', 'monthly calendar', 'schedule calendar', 'schedule', 'menu'],
            })

        # ── 6. Projects & Tasks ─────────────────────────────────────────
        url_projects = cls.safe_reverse('projects:project_list', fallback='/projects/')
        if url_projects:
            items.append({
                'label': 'Projects',
                'href': url_projects,
                'icon': 'briefcase',
                'group': 'Work',
                'description': 'Active client projects, deliverables, and progress tracking',
                'keywords': ['projects', 'project list', 'all projects', 'menu'],
            })

        url_tasks = cls.safe_reverse('projects:global_task_list', fallback='/projects/tasks/')
        if url_tasks:
            items.append({
                'label': 'Global Task List',
                'href': url_tasks,
                'icon': 'list-todo',
                'group': 'Work',
                'description': 'Cross-project task tracker, dependencies, and completion status',
                'keywords': ['tasks', 'task list', 'global tasks', 'project tasks', 'menu'],
            })

        if user.is_superuser or cls.has_module_view(user, 'projects'):
            url_proj_types = cls.safe_reverse('projects:project_type_list', fallback='/projects/types/')
            if url_proj_types:
                items.append({
                    'label': 'Project Types',
                    'href': url_proj_types,
                    'icon': 'folder-tree',
                    'group': 'Work',
                    'description': 'Project categories, contract styles, and billing templates',
                    'keywords': ['project types', 'types of projects', 'submenu'],
                })

            url_task_templates = cls.safe_reverse('projects:template_list', fallback='/projects/templates/')
            if url_task_templates:
                items.append({
                    'label': 'Task Templates',
                    'href': url_task_templates,
                    'icon': 'copy',
                    'group': 'Work',
                    'description': 'Reusable task workflows and standard milestone checklists',
                    'keywords': ['task templates', 'project templates', 'submenu'],
                })

            url_gantt = cls.safe_reverse('projects:project_gantt_global', fallback='/projects/gantt/')
            if url_gantt:
                items.append({
                    'label': 'Interactive Gantt Chart',
                    'href': url_gantt,
                    'icon': 'bar-chart-2',
                    'group': 'Work',
                    'description': 'Interactive timeline schedule and dependency critical path',
                    'keywords': ['gantt', 'gantt chart', 'project timeline', 'submenu'],
                })

        # ── 7. Leave & Expenses ─────────────────────────────────────────
        if is_admin_or_mgr or cls.has_module_view(user, 'leave'):
            url_leave_admin = cls.safe_reverse('leave:leave_admin_dashboard', fallback='/leave/admin/')
            if url_leave_admin:
                items.append({
                    'label': 'Leave Requests (Admin)',
                    'href': url_leave_admin,
                    'icon': 'clipboard-check',
                    'group': 'Leave',
                    'description': 'Approve or reject employee leave applications',
                    'keywords': ['leave requests', 'leave management', 'admin leave', 'leaves', 'menu'],
                })

        url_my_leave = cls.safe_reverse('staff:leave_dashboard', fallback='/leave/my-leave/')
        if url_my_leave:
            items.append({
                'label': 'My Leave Requests',
                'href': url_my_leave,
                'icon': 'calendar-heart',
                'group': 'Leave',
                'description': 'Apply for paid/unpaid leave and inspect balance',
                'keywords': ['my leave', 'my leave requests', 'apply leave', 'leave balance', 'menu'],
            })

        if is_admin_or_mgr or cls.has_module_view(user, 'expense'):
            url_exp_admin = cls.safe_reverse('expense:admin_expense_list', fallback='/expense/admin/')
            if url_exp_admin:
                items.append({
                    'label': 'Expense Claims (Admin)',
                    'href': url_exp_admin,
                    'icon': 'receipt',
                    'group': 'Expense',
                    'description': 'Review, verify, and approve employee reimbursement claims',
                    'keywords': ['expenses', 'expense claims', 'admin expenses', 'reimbursements', 'menu'],
                })

        url_my_exp = cls.safe_reverse('expense:my_expenses', fallback='/expense/my-expenses/')
        if url_my_exp:
            items.append({
                'label': 'My Expenses',
                'href': url_my_exp,
                'icon': 'wallet',
                'group': 'Expense',
                'description': 'Submit receipts and track reimbursement payments',
                'keywords': ['my expenses', 'claim expense', 'my reimbursement', 'menu'],
            })

        # ── 8. Payroll & Finance ────────────────────────────────────────
        if is_payroll_auth:
            url_payroll_runs = cls.safe_reverse('payroll:payroll_run_list', fallback='/payroll/runs/')
            if url_payroll_runs:
                items.append({
                    'label': 'Payroll Runs',
                    'href': url_payroll_runs,
                    'icon': 'database',
                    'group': 'Payroll',
                    'description': 'Generate and approve monthly staff salary batches',
                    'keywords': ['payroll', 'payroll runs', 'salary processing', 'salaries', 'menu'],
                })

            url_payroll_reg = cls.safe_reverse('payroll:payroll_register', fallback='/payroll/register/')
            if url_payroll_reg:
                items.append({
                    'label': 'Payroll Register',
                    'href': url_payroll_reg,
                    'icon': 'table',
                    'group': 'Payroll',
                    'description': 'Detailed salary breakdown, taxes, and deductions table',
                    'keywords': ['payroll register', 'salary register', 'salary sheet', 'submenu'],
                })

            url_bank_rep = cls.safe_reverse('payroll:bank_report', fallback='/payroll/reports/bank/')
            if url_bank_rep:
                items.append({
                    'label': 'Bank Salary Report',
                    'href': url_bank_rep,
                    'icon': 'landmark',
                    'group': 'Payroll',
                    'description': 'Export bank salary transfer orders and account details',
                    'keywords': ['bank report', 'salary bank transfer', 'bank export', 'submenu'],
                })

            url_cash_rep = cls.safe_reverse('payroll:cash_report', fallback='/payroll/reports/cash/')
            if url_cash_rep:
                items.append({
                    'label': 'Cash Salary Report',
                    'href': url_cash_rep,
                    'icon': 'banknote',
                    'group': 'Payroll',
                    'description': 'Cash payment disbursements and employee signoff sheets',
                    'keywords': ['cash report', 'salary cash payments', 'submenu'],
                })

        url_my_slips = cls.safe_reverse('payroll:my_payslips', fallback='/payroll/my-payslips/')
        if url_my_slips:
            items.append({
                'label': 'My Payslips',
                'href': url_my_slips,
                'icon': 'file-text',
                'group': 'Payroll',
                'description': 'View and download personal monthly salary payslips',
                'keywords': ['my payslips', 'salary slip', 'my salary', 'payslip', 'menu'],
            })

        # ── 9. Branches & Locations ─────────────────────────────────────
        if user.is_superuser or cls.has_module_view(user, 'branches'):
            url_branches = cls.safe_reverse('branches:branch_list', fallback='/branches/')
            if url_branches:
                items.append({
                    'label': 'Branches & Offices',
                    'href': url_branches,
                    'icon': 'building-2',
                    'group': 'Operations',
                    'description': 'Manage company physical branch offices and GPS coordinates',
                    'keywords': ['branches', 'branch list', 'locations', 'offices', 'menu'],
                })

        url_holidays = cls.safe_reverse('branches:holiday_list', fallback='/branches/holidays/')
        if url_holidays:
            items.append({
                'label': 'Holidays Calendar',
                'href': url_holidays,
                'icon': 'calendar-days',
                'group': 'Operations',
                'description': 'Official public, national, and company holidays calendar',
                'keywords': ['holidays', 'holiday calendar', 'public holidays', 'submenu'],
            })

        # ── 10. AI Workspace ────────────────────────────────────────────
        if is_admin_or_mgr:
            url_ai_assist = cls.safe_reverse('admin_panel:ai_assistant', fallback='/admin-panel/ai/assistant/')
            if url_ai_assist:
                items.append({
                    'label': 'AI Workspace: Assistant',
                    'href': url_ai_assist,
                    'icon': 'bot',
                    'group': 'AI Workspace',
                    'description': 'Conversational AI intelligence for analytics and assistance',
                    'keywords': ['ai', 'ai assistant', 'chatbot', 'fieldtrack ai', 'submodule'],
                })

            url_ai_att = cls.safe_reverse('admin_panel:ai_attendance_insights', fallback='/admin-panel/ai/attendance-insights/')
            if url_ai_att:
                items.append({
                    'label': 'AI Attendance Insights',
                    'href': url_ai_att,
                    'icon': 'sparkles',
                    'group': 'AI Workspace',
                    'description': 'Predictive absenteeism and anomaly analytics',
                    'keywords': ['attendance insights', 'ai attendance', 'anomaly detection', 'submodule'],
                })

            url_ai_proj = cls.safe_reverse('admin_panel:ai_project_insights', fallback='/admin-panel/ai/project-insights/')
            if url_ai_proj:
                items.append({
                    'label': 'AI Project Insights',
                    'href': url_ai_proj,
                    'icon': 'sparkles',
                    'group': 'AI Workspace',
                    'description': 'Schedule risk detection and completion forecast',
                    'keywords': ['project insights', 'ai projects', 'risk forecasting', 'submodule'],
                })

            url_ai_reports = cls.safe_reverse('admin_panel:ai_smart_reports', fallback='/admin-panel/ai/smart-reports/')
            if url_ai_reports:
                items.append({
                    'label': 'AI Smart Reports',
                    'href': url_ai_reports,
                    'icon': 'file-bar-chart',
                    'group': 'AI Workspace',
                    'description': 'Executive automated summaries and exportable dossiers',
                    'keywords': ['smart reports', 'ai reports', 'executive summary', 'submodule'],
                })

        return items

    @classmethod
    def get_dynamic_roles_results(cls, user, query: str, tokens: List[str]) -> List[Dict]:
        """
        Searches dynamic roles from Role model for authorized role administrators.
        - Strictly excludes protected roles (system_owner, is_system_protected=True).
        - Strictly excludes inactive roles.
        - Never reveals internal IDs, codes, or metadata in labels.
        """
        if not cls.can_view_roles(user):
            return []

        from apps.accounts.rbac_models import Role

        # Fails closed on inactive or protected roles
        roles = Role.objects.filter(
            is_active=True,
            is_system_protected=False
        ).exclude(code='system_owner')

        role_query = Q()
        for token in tokens:
            role_query |= Q(name__icontains=token) | Q(description__icontains=token)

        # If general keywords like "role" or "roles" are searched, include all active assignable roles
        wants_all_roles = any(t in ('role', 'roles', 'system_roles', 'সিস্টেম রোল') for t in tokens)
        if not wants_all_roles:
            roles = roles.filter(role_query)

        results = []
        for r in roles[:10]:
            edit_url = cls.safe_reverse('admin_panel:role_edit', args=[r.pk], fallback=f"/admin-panel/roles/{r.pk}/edit/")
            matrix_url = cls.safe_reverse('admin_panel:role_matrix', args=[r.pk], fallback=f"/admin-panel/roles/{r.pk}/matrix/")
            members_url = cls.safe_reverse('admin_panel:role_members', args=[r.pk], fallback=f"/admin-panel/roles/{r.pk}/members/")

            # Main Role destination (edit / view permissions)
            results.append({
                'label': f"Role: {r.name}",
                'href': edit_url or matrix_url,
                'icon': 'shield',
                'group': 'Roles & Access',
                'description': r.description or f"Manage permissions and settings for {r.name}",
                'keywords': ['role', 'roles', r.name.lower(), 'edit role', 'manage role'],
            })

            # Role Matrix destination
            if any(t in ('matrix', 'permission', 'permissions', 'access', 'ম্যাট্রিক্স') for t in tokens) or r.name.lower() in query.lower():
                results.append({
                    'label': f"Matrix: {r.name}",
                    'href': matrix_url,
                    'icon': 'grid',
                    'group': 'Roles & Access',
                    'description': f"Permission and scope matrix for {r.name}",
                    'keywords': ['matrix', 'permission matrix', 'role matrix', r.name.lower(), 'scope'],
                })

            # Role Members destination
            if any(t in ('member', 'members', 'users', 'assigned') for t in tokens):
                results.append({
                    'label': f"Members: {r.name}",
                    'href': members_url,
                    'icon': 'users',
                    'group': 'Roles & Access',
                    'description': f"Assigned users with {r.name} role",
                    'keywords': ['members', 'users', r.name.lower(), 'assigned users'],
                })

        return results

    @classmethod
    def get_dynamic_modules_results(cls, user, query: str, tokens: List[str]) -> List[Dict]:
        """
        Searches dynamic Module model entries.
        Fails closed if the module is disabled or user lacks module view permissions.
        """
        from apps.accounts.rbac_models import Module

        modules = Module.objects.filter(is_active=True)
        mod_query = Q()
        for token in tokens:
            mod_query |= Q(name__icontains=token) | Q(description__icontains=token)

        wants_all_modules = any(t in ('module', 'modules', 'মডিউল') for t in tokens)
        if not wants_all_modules:
            modules = modules.filter(mod_query)

        MODULE_URL_MAP = {
            'attendance': '/admin-panel/attendance/' if cls.can_view_roles(user) else '/staff/home/',
            'employees': '/employees/',
            'projects': '/projects/',
            'leave': '/leave/admin/' if cls.can_view_roles(user) else '/leave/my-leave/',
            'expense': '/expense/admin/' if cls.can_view_roles(user) else '/expense/my-expenses/',
            'schedule': '/schedule/',
            'branches': '/branches/',
            'notifications': '/notifications/',
            'backups': '/backups/',
            'accounts': '/admin-panel/roles/' if cls.can_view_roles(user) else '/account/security/',
        }

        results = []
        for m in modules[:10]:
            if not cls.has_module_view(user, m.code):
                continue

            target_href = MODULE_URL_MAP.get(m.code, '/admin-panel/dashboard/')
            results.append({
                'label': f"Module: {m.name}",
                'href': target_href,
                'icon': m.icon or 'box',
                'group': 'Modules',
                'description': m.description or f"Access {m.name} module and submodules",
                'keywords': ['module', 'modules', m.name.lower(), m.code.lower(), 'submodule'],
            })

        return results

    @classmethod
    def get_dynamic_permissions_results(cls, user, query: str, tokens: List[str]) -> List[Dict]:
        """
        Searches dynamic Permission model entries for authorized role administrators.
        Links to the permission matrix or relevant module destination.
        Never reveals permission codenames or raw internal codes.
        """
        if not cls.can_view_roles(user):
            return []

        from apps.accounts.rbac_models import Permission

        # Fails closed on disabled modules
        perms = Permission.objects.filter(
            module__is_active=True
        ).select_related('module', 'action')

        perm_query = Q()
        for token in tokens:
            perm_query |= Q(name__icontains=token) | Q(description__icontains=token) | Q(action__name__icontains=token)

        wants_all_perms = any(t in ('permission', 'permissions', 'অনুমতি', 'পারমিশন') for t in tokens)
        if not wants_all_perms:
            perms = perms.filter(perm_query)

        matrix_url = cls.safe_reverse('admin_panel:permission_matrix', fallback='/admin-panel/permissions/matrix/')

        results = []
        for p in perms[:10]:
            results.append({
                'label': f"Permission: {p.name}",
                'href': matrix_url,
                'icon': 'shield-check',
                'group': 'Permissions',
                'description': f"Configure access in {p.module.name} ({p.action.name})",
                'keywords': ['permission', 'permissions', p.name.lower(), p.module.name.lower(), p.action.name.lower(), 'access'],
            })

        return results

    @classmethod
    def get_operational_data_results(cls, user, query: str, tokens: List[str]) -> List[Dict]:
        """
        Executes branch and permission-scoped operational search for Employees, Projects, Tasks,
        Payroll Runs, Payslips, Leave Requests, and Expense Claims.
        Preserves 100% contract fidelity with existing UI and tests.
        """
        from apps.employees.models import Employee
        from apps.projects.models import Project, ProjectTask
        from apps.payroll.models import PayrollRun, EmployeePayrollCalculation
        from apps.leave.models import LeaveRequest
        from apps.expense.models import Expense

        results = []
        emp = getattr(user, 'employee_master', None)
        user_branch = emp.branch if emp else None
        data_scope = emp.data_scope if emp else 'branch'

        def filter_branch(qs, branch_field='branch'):
            if not user.is_superuser and data_scope == 'branch' and user_branch:
                return qs.filter(**{f"{branch_field}": user_branch})
            return qs

        # 1. Employees / HR (Only admin, system_owner, hr, manager)
        if cls.has_module_view(user, 'employees'):
            employees = Employee.objects.select_related('branch', 'department', 'designation')
            employees = filter_branch(employees, 'branch')
            emp_filter = Q()
            for t in tokens:
                emp_filter |= Q(first_name__icontains=t) | Q(last_name__icontains=t) | Q(employee_number__icontains=t)
            employees = employees.filter(emp_filter).order_by('first_name', 'last_name')[:5]
            for e in employees:
                results.append({
                    'label': f"Employee: {e.first_name} {e.last_name} ({e.employee_number})",
                    'href': f"/employees/{e.pk}/",
                    'icon': 'user',
                    'group': 'People',
                    'description': f"{e.designation.name if e.designation else 'Staff'} - {e.department.name if e.department else ''}",
                    'keywords': ['employee', e.first_name.lower(), e.last_name.lower(), e.employee_number.lower()],
                })

        # 2. Projects (All authenticated users can search matching projects in scope)
        projects = Project.objects.select_related('branch')
        projects = filter_branch(projects, 'branch')
        proj_filter = Q()
        for t in tokens:
            proj_filter |= Q(name__icontains=t) | Q(client_name__icontains=t)
        projects = projects.filter(proj_filter).order_by('name')[:5]
        for p in projects:
            results.append({
                'label': f"Project: {p.name} ({p.client_name})",
                'href': f"/projects/{p.pk}/",
                'icon': 'briefcase',
                'group': 'Work',
                'description': f"Client: {p.client_name}",
                'keywords': ['project', p.name.lower(), p.client_name.lower()],
            })

        # 3. Project Tasks
        tasks = ProjectTask.objects.select_related('project', 'project__branch')
        tasks = filter_branch(tasks, 'project__branch')
        task_filter = Q()
        for t in tokens:
            task_filter |= Q(activity__icontains=t) | Q(project__name__icontains=t)
        tasks = tasks.filter(task_filter).order_by('activity')[:5]
        for t in tasks:
            results.append({
                'label': f"Task: {t.activity} (Project: {t.project.name})",
                'href': f"/projects/{t.project.pk}/",
                'icon': 'check-square',
                'group': 'Work',
                'description': f"Project: {t.project.name}",
                'keywords': ['task', t.activity.lower(), t.project.name.lower()],
            })

        # 4. Payroll Runs & calculations (Only admin, system_owner, finance, accounts)
        if cls.has_module_view(user, 'payroll'):
            runs = PayrollRun.objects.filter(
                Q(status__icontains=query) | Q(status__in=tokens)
            ).order_by('-period_start')[:5]
            for r in runs:
                results.append({
                    'label': f"Payroll Run: {r.period_start} to {r.period_end} ({r.status})",
                    'href': f"/payroll/runs/{r.pk}/",
                    'icon': 'database',
                    'group': 'Payroll',
                    'description': f"Status: {r.status.title()}",
                    'keywords': ['payroll', 'payroll run', r.status.lower()],
                })

            calcs = EmployeePayrollCalculation.objects.select_related('employee', 'employee__branch', 'payroll_run')
            calcs = filter_branch(calcs, 'employee__branch')
            calc_filter = Q()
            for t in tokens:
                calc_filter |= (
                    Q(employee__first_name__icontains=t) |
                    Q(employee__last_name__icontains=t) |
                    Q(employee__employee_number__icontains=t)
                )
            calcs = calcs.filter(calc_filter).order_by('-payroll_run__period_start')[:5]
            for c in calcs:
                results.append({
                    'label': f"Payslip: {c.employee.first_name} {c.employee.last_name} ({c.payroll_run.period_start})",
                    'href': f"/payroll/payslips/{c.pk}/",
                    'icon': 'file-text',
                    'group': 'Payroll',
                    'description': f"Net Payable: {c.net_payable}",
                    'keywords': ['payslip', 'salary', c.employee.first_name.lower(), c.employee.last_name.lower()],
                })

        # 5. Leave Requests
        leaves = LeaveRequest.objects.select_related('employee', 'employee__branch', 'leave_type')
        can_manage_leaves = user.is_superuser or cls.has_module_view(user, 'leave')
        if not can_manage_leaves:
            leaves = leaves.filter(employee__user=user)
        else:
            leaves = filter_branch(leaves, 'employee__branch')
        leave_filter = Q()
        for t in tokens:
            leave_filter |= Q(employee__full_name__icontains=t) | Q(leave_type__name__icontains=t) | Q(status__icontains=t)
        leaves = leaves.filter(leave_filter).order_by('-start_date')[:5]
        for l in leaves:
            results.append({
                'label': f"Leave: {l.employee.full_name} ({l.leave_type.name} - {l.status})",
                'href': "/leave/admin/" if can_manage_leaves else "/leave/my-leave/",
                'icon': 'calendar-heart',
                'group': 'Leave',
                'description': f"{l.start_date} to {l.end_date} ({l.status.title()})",
                'keywords': ['leave', l.employee.full_name.lower(), l.leave_type.name.lower()],
            })

        # 6. Expenses
        expenses = Expense.objects.select_related('employee', 'employee__branch')
        can_manage_expenses = user.is_superuser or cls.has_module_view(user, 'expense')
        if not can_manage_expenses:
            expenses = expenses.filter(employee__user=user)
        else:
            expenses = filter_branch(expenses, 'employee__branch')
        exp_filter = Q()
        for t in tokens:
            exp_filter |= Q(employee__full_name__icontains=t) | Q(description__icontains=t) | Q(status__icontains=t)
        expenses = expenses.filter(exp_filter).order_by('-requested_at')[:5]
        for ex in expenses:
            results.append({
                'label': f"Expense: {ex.employee.full_name} (${ex.amount} - {ex.status})",
                'href': "/expense/admin/" if can_manage_expenses else "/expense/my-expenses/",
                'icon': 'receipt',
                'group': 'Expense',
                'description': f"Amount: {ex.amount} ({ex.status.title()})",
                'keywords': ['expense', ex.employee.full_name.lower(), ex.description.lower() if ex.description else ''],
            })

        return results

    @classmethod
    def calculate_score(cls, item: Dict, query: str, tokens: List[str]) -> int:
        """
        Calculates relevance ranking score.
        Direct exact matches on title score highest, followed by prefixes, word hits, and keyword aliases.
        """
        score = 0
        label_lower = item['label'].lower()
        desc_lower = item.get('description', '').lower()
        keywords = [k.lower() for k in item.get('keywords', [])]

        # 1. Exact query match
        if label_lower == query:
            score += 150
        elif label_lower.startswith(query):
            score += 100
        elif query in label_lower:
            score += 70

        # 2. Token matches in title
        for token in tokens:
            if token == label_lower:
                score += 80
            elif label_lower.startswith(token):
                score += 50
            elif f" {token}" in label_lower or f": {token}" in label_lower:
                score += 40
            elif token in label_lower:
                score += 25

        # 3. Exact query match in keywords/aliases
        if query in keywords:
            score += 90

        # 4. Token matches in keywords
        for token in tokens:
            for kw in keywords:
                if token == kw:
                    score += 45
                elif kw.startswith(token):
                    score += 30
                elif token in kw:
                    score += 15

        # 5. Token matches in description
        for token in tokens:
            if token in desc_lower:
                score += 10

        return score

    @classmethod
    def search(cls, user, query: str) -> List[Dict]:
        """
        Main entry point: returns sorted, deduplicated, permission-filtered search results.
        """
        clean_query, tokens = cls.normalize_query(query)
        if not user or not user.is_authenticated:
            return []

        # When query is empty, return default high-priority routes based on user's access
        if not clean_query:
            catalog = cls.get_navigation_catalog(user)
            # Pick primary overview items for initial display
            initial_items = []
            for it in catalog:
                if it['group'] in ('Overview', 'Roles & Access', 'Work', 'People', 'Account'):
                    initial_items.append(it)
                if len(initial_items) >= 12:
                    break
            return initial_items

        candidates: List[Dict] = []

        # 1. Navigation catalog items
        catalog = cls.get_navigation_catalog(user)
        for item in catalog:
            score = cls.calculate_score(item, clean_query.lower(), tokens)
            if score > 0:
                item_copy = dict(item)
                item_copy['score'] = score
                candidates.append(item_copy)

        # 2. Dynamic Roles from Database
        dynamic_roles = cls.get_dynamic_roles_results(user, clean_query, tokens)
        for r_item in dynamic_roles:
            score = cls.calculate_score(r_item, clean_query.lower(), tokens)
            if score > 0:
                r_item['score'] = score
                candidates.append(r_item)

        # 3. Dynamic Modules from Database
        dynamic_modules = cls.get_dynamic_modules_results(user, clean_query, tokens)
        for m_item in dynamic_modules:
            score = cls.calculate_score(m_item, clean_query.lower(), tokens)
            if score > 0:
                m_item['score'] = score
                candidates.append(m_item)

        # 4. Dynamic Permissions from Database
        dynamic_perms = cls.get_dynamic_permissions_results(user, clean_query, tokens)
        for p_item in dynamic_perms:
            score = cls.calculate_score(p_item, clean_query.lower(), tokens)
            if score > 0:
                p_item['score'] = score
                candidates.append(p_item)

        # 5. Operational Data (Employees, Projects, Tasks, Payroll, Leaves, Expenses)
        op_data = cls.get_operational_data_results(user, clean_query, tokens)
        for op_item in op_data:
            score = cls.calculate_score(op_item, clean_query.lower(), tokens)
            op_item['score'] = max(score, 20)  # Baseline for operational search match
            candidates.append(op_item)

        # ── Deduplication and Ranking ────────────────────────────────────
        seen_keys: Set[Tuple[str, str]] = set()
        deduped: List[Dict] = []

        # Sort candidates descending by score, then ascending by label
        candidates.sort(key=lambda x: (-x.get('score', 0), x['label']))

        for item in candidates:
            key = (item['href'].strip().rstrip('/'), item['label'].strip().lower())
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduped.append(item)
            if len(deduped) >= 30:
                break

        return deduped
