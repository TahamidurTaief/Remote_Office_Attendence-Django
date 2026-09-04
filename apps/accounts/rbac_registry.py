import logging
from typing import Dict, List, Optional, Set, Tuple, Any
from django.urls import reverse, NoReverseMatch
from django.db import transaction
from django.core.exceptions import PermissionDenied, ValidationError

logger = logging.getLogger(__name__)


class RBACRegistryService:
    """
    Canonical RBAC Permission & Navigation Hierarchy Registry.
    Defines the unified 4-level hierarchy:
      Module -> Submodule -> Menu -> Submenu
    Provides safe, additive database synchronization and matrix state resolution.
    """

    # -------------------------------------------------------------------------
    # Canonical Action Definitions
    # -------------------------------------------------------------------------
    ACTIONS = [
        {'code': 'add', 'name': 'Add', 'description': 'Create new records and initial resources', 'is_destructive': False},
        {'code': 'edit', 'name': 'Edit', 'description': 'Access editing workflows, forms, and modification views', 'is_destructive': False},
        {'code': 'delete', 'name': 'Delete', 'description': 'Permanently delete or deactivate records', 'is_destructive': True},
        {'code': 'update', 'name': 'Update', 'description': 'Persist and commit changes to existing records', 'is_destructive': False},
    ]

    # Legacy action compatibility mapping
    COMPATIBILITY_ACTION_MAP = {
        'create': 'add',
        'add': 'add',
        'edit': 'edit',
        'update': 'update',
        'delete': 'delete',
        'view': 'edit',  # view capability satisfied if user holds edit or update
    }

    # -------------------------------------------------------------------------
    # Canonical Hierarchy: 12 Modules covering all real app sections
    # -------------------------------------------------------------------------
    HIERARCHY_DEFINITION = [
        # 1. Dashboard
        {
            'code': 'dashboard',
            'name': 'Dashboard & Overview',
            'icon': 'layout-dashboard',
            'sort_order': 10,
            'submodules': [
                {
                    'code': 'dashboard_views',
                    'name': 'Executive Dashboards',
                    'icon': 'layout-grid',
                    'menus': [
                        {
                            'code': 'executive_dashboard',
                            'name': 'Executive Dashboard',
                            'route': 'admin_panel:dashboard',
                            'fallback_url': '/admin-panel/dashboard/',
                            'description': 'High-level operational metrics and organizational KPIs',
                            'submenus': []
                        },
                        {
                            'code': 'analytics_hub',
                            'name': 'Analytics & Reports Hub',
                            'route': 'admin_panel:reports_main',
                            'fallback_url': '/admin-panel/reports/',
                            'description': 'Executive analytics dashboard and metrics reports',
                            'submenus': []
                        },
                        {
                            'code': 'recent_activity',
                            'name': 'Recent Activity & Notifications',
                            'route': 'notifications:list',
                            'fallback_url': '/notifications/',
                            'description': 'Live notification feed and system audit broadcasts',
                            'submenus': []
                        },
                    ]
                }
            ]
        },

        # 2. People & Employees
        {
            'code': 'employees',
            'name': 'People & Employees',
            'icon': 'users',
            'sort_order': 20,
            'submodules': [
                {
                    'code': 'employee_records',
                    'name': 'Employee Records',
                    'icon': 'user-check',
                    'menus': [
                        {
                            'code': 'employee_directory',
                            'name': 'Employee Directory',
                            'route': 'employees:employee_list',
                            'fallback_url': '/employees/',
                            'description': 'Browse and manage registered employees and master files',
                            'submenus': []
                        },
                        {
                            'code': 'employee_add',
                            'name': 'Add New Employee (Wizard)',
                            'route': 'employees:employee_add',
                            'fallback_url': '/employees/add/',
                            'description': 'Onboard new staff through multi-step employment wizard',
                            'submenus': []
                        },
                    ]
                },
                {
                    'code': 'org_structure',
                    'name': 'Organizational Structure',
                    'icon': 'building',
                    'menus': [
                        {
                            'code': 'departments',
                            'name': 'Departments',
                            'route': 'employees:department_list',
                            'fallback_url': '/employees/departments/',
                            'description': 'Department hierarchies, divisions, and cost centers',
                            'submenus': []
                        },
                        {
                            'code': 'designations',
                            'name': 'Designations',
                            'route': 'employees:designation_list',
                            'fallback_url': '/employees/designations/',
                            'description': 'Official job titles, pay grade bands, and rankings',
                            'submenus': []
                        },
                        {
                            'code': 'org_chart',
                            'name': 'Organization Chart',
                            'route': 'employees:org_chart',
                            'fallback_url': '/employees/org-chart/',
                            'description': 'Visual reporting chain and organizational tree',
                            'submenus': []
                        },
                        {
                            'code': 'delegations',
                            'name': 'Employee Delegations',
                            'route': 'employees:delegation_list',
                            'fallback_url': '/employees/delegations/',
                            'description': 'Temporary authority delegations and approval routing',
                            'submenus': []
                        },
                        {
                            'code': 'hardware_assets',
                            'name': 'Hardware & Assets',
                            'route': 'employees:asset_list',
                            'fallback_url': '/employees/assets/',
                            'description': 'Company equipment inventory and employee hardware assignments',
                            'submenus': []
                        },
                    ]
                }
            ]
        },

        # 3. Attendance & Time Tracking
        {
            'code': 'attendance',
            'name': 'Attendance & Time Tracking',
            'icon': 'clock',
            'sort_order': 30,
            'submodules': [
                {
                    'code': 'attendance_operations',
                    'name': 'Attendance Operations',
                    'icon': 'clock-3',
                    'menus': [
                        {
                            'code': 'live_attendance',
                            'name': 'Live Attendance Monitor',
                            'route': 'attendance:status',
                            'fallback_url': '/attendance/status/',
                            'description': 'Real-time staff check-in tracking and geofence verification',
                            'submenus': []
                        },
                        {
                            'code': 'attendance_logs',
                            'name': 'Attendance Sessions & Logs',
                            'route': 'admin_panel:attendance_list',
                            'fallback_url': '/admin-panel/attendance/',
                            'description': 'Detailed attendance punch records, IP logs, and GPS coords',
                            'submenus': []
                        },
                        {
                            'code': 'manual_attendance',
                            'name': 'Manual Attendance Entry',
                            'route': 'admin_panel:manual_entry',
                            'fallback_url': '/admin-panel/attendance/manual/',
                            'description': 'Administrative retroactive attendance punch entry and override',
                            'submenus': []
                        },
                        {
                            'code': 'attendance_requests',
                            'name': 'Attendance Correction Requests',
                            'route': 'attendance:attendance_requests_list',
                            'fallback_url': '/attendance/requests/',
                            'description': 'Review employee time adjustment and missed punch requests',
                            'submenus': []
                        },
                    ]
                },
                {
                    'code': 'attendance_reports',
                    'name': 'Attendance Reports',
                    'icon': 'file-text',
                    'menus': [
                        {
                            'code': 'daily_report',
                            'name': 'Daily Attendance Report',
                            'route': 'admin_panel:reports_daily',
                            'fallback_url': '/admin-panel/reports/daily/',
                            'description': 'Daily branch-level attendance and punctuality summaries',
                            'submenus': []
                        },
                        {
                            'code': 'monthly_report',
                            'name': 'Monthly Attendance Timesheet',
                            'route': 'admin_panel:reports_monthly',
                            'fallback_url': '/admin-panel/reports/monthly/',
                            'description': 'Aggregated monthly attendance, late hours, and overtime',
                            'submenus': []
                        },
                        {
                            'code': 'absence_report',
                            'name': 'Absence & Truancy Report',
                            'route': 'admin_panel:reports_absent',
                            'fallback_url': '/admin-panel/reports/absent/',
                            'description': 'Unscheduled absenteeism and pattern anomaly reports',
                            'submenus': []
                        },
                    ]
                }
            ]
        },

        # 4. Leave Management
        {
            'code': 'leave',
            'name': 'Leave Management',
            'icon': 'clipboard-check',
            'sort_order': 40,
            'submodules': [
                {
                    'code': 'leave_operations',
                    'name': 'Leave Processing',
                    'icon': 'calendar-heart',
                    'menus': [
                        {
                            'code': 'leave_requests_admin',
                            'name': 'Leave Applications (Admin)',
                            'route': 'leave:admin_dashboard',
                            'fallback_url': '/leave/admin/',
                            'description': 'Approve or reject employee paid and unpaid leave claims',
                            'submenus': []
                        },
                        {
                            'code': 'leave_balances',
                            'name': 'Leave Balances & Allocations',
                            'route': 'leave:admin_balances',
                            'fallback_url': '/leave/admin/balances/',
                            'description': 'Staff annual leave balances, accruals, and carry-over',
                            'submenus': []
                        },
                        {
                            'code': 'leave_types',
                            'name': 'Leave Policy & Types',
                            'route': 'leave:admin_leave_types',
                            'fallback_url': '/leave/admin/types/',
                            'description': 'Configure leave categories, quotas, and accrual policies',
                            'submenus': []
                        },
                    ]
                }
            ]
        },

        # 5. Schedules & Roster
        {
            'code': 'schedule',
            'name': 'Schedules & Roster',
            'icon': 'calendar',
            'sort_order': 50,
            'submodules': [
                {
                    'code': 'roster_planning',
                    'name': 'Shift Rostering',
                    'icon': 'calendar-clock',
                    'menus': [
                        {
                            'code': 'calendar_view',
                            'name': 'Company Calendar View',
                            'route': 'schedule:month_view',
                            'fallback_url': '/schedule/',
                            'description': 'Monthly enterprise calendar view with shifts and events',
                            'submenus': []
                        },
                        {
                            'code': 'shift_schedule',
                            'name': 'Shift Rosters & Patterns',
                            'route': 'schedule:shift_schedule',
                            'fallback_url': '/schedule/shifts/',
                            'description': 'Configure rotating shifts, timings, and department rosters',
                            'submenus': []
                        },
                    ]
                }
            ]
        },

        # 6. Projects & Work
        {
            'code': 'projects',
            'name': 'Projects & Work',
            'icon': 'briefcase',
            'sort_order': 60,
            'submodules': [
                {
                    'code': 'project_management',
                    'name': 'Project Management',
                    'icon': 'folder-kanban',
                    'menus': [
                        {
                            'code': 'all_projects',
                            'name': 'All Projects Directory',
                            'route': 'projects:project_list',
                            'fallback_url': '/projects/',
                            'description': 'Client deliverables, project milestones, and status',
                            'submenus': []
                        },
                        {
                            'code': 'project_types',
                            'name': 'Project Types & Categories',
                            'route': 'projects:project_type_list',
                            'fallback_url': '/projects/types/',
                            'description': 'Project categorization, contract templates, and billing',
                            'submenus': []
                        },
                        {
                            'code': 'project_gantt',
                            'name': 'Interactive Gantt Chart',
                            'route': 'projects:project_gantt_global',
                            'fallback_url': '/projects/gantt/',
                            'description': 'Interactive timeline schedule and dependency critical path',
                            'submenus': []
                        },
                    ]
                }
            ]
        },

        # 7. Tasks & Milestones
        {
            'code': 'tasks',
            'name': 'Tasks & Deliverables',
            'icon': 'check-square',
            'sort_order': 70,
            'submodules': [
                {
                    'code': 'task_operations',
                    'name': 'Task Coordination',
                    'icon': 'list-todo',
                    'menus': [
                        {
                            'code': 'global_task_list',
                            'name': 'Team Tasks Tracker',
                            'route': 'projects:global_task_list',
                            'fallback_url': '/projects/tasks/',
                            'description': 'Cross-project task tracker, dependencies, and completion',
                            'submenus': []
                        },
                        {
                            'code': 'task_templates',
                            'name': 'Task Templates & Checklists',
                            'route': 'projects:template_list',
                            'fallback_url': '/projects/templates/',
                            'description': 'Standard operating procedure checklists and workflow templates',
                            'submenus': []
                        },
                    ]
                }
            ]
        },

        # 8. Payroll & Finance
        {
            'code': 'payroll',
            'name': 'Payroll & Compensation',
            'icon': 'calculator',
            'sort_order': 80,
            'submodules': [
                {
                    'code': 'payroll_runs_mgmt',
                    'name': 'Payroll Processing',
                    'icon': 'badge-dollar-sign',
                    'menus': [
                        {
                            'code': 'payroll_runs',
                            'name': 'Payroll Batches & Runs',
                            'route': 'payroll:payroll_run_list',
                            'fallback_url': '/payroll/runs/',
                            'description': 'Generate, calculate, verify, and approve monthly staff payroll',
                            'submenus': []
                        },
                        {
                            'code': 'payroll_register',
                            'name': 'Payroll Register',
                            'route': 'payroll:payroll_register',
                            'fallback_url': '/payroll/register/',
                            'description': 'Consolidated gross-to-net breakdown, tax, and deduction table',
                            'submenus': []
                        },
                        {
                            'code': 'bank_report',
                            'name': 'Bank Salary Transfer Report',
                            'route': 'payroll:bank_report',
                            'fallback_url': '/payroll/reports/bank/',
                            'description': 'Export bank salary transfer batch orders and routing details',
                            'submenus': []
                        },
                        {
                            'code': 'cash_report',
                            'name': 'Cash Disbursement Report',
                            'route': 'payroll:cash_report',
                            'fallback_url': '/payroll/reports/cash/',
                            'description': 'Physical cash pay vouchers and employee signoff registers',
                            'submenus': []
                        },
                    ]
                },
                {
                    'code': 'salary_configuration',
                    'name': 'Salary Structure & Setup',
                    'icon': 'layers',
                    'menus': [
                        {
                            'code': 'salary_components',
                            'name': 'Salary Components',
                            'route': 'payroll:salary_components',
                            'fallback_url': '/payroll/components/',
                            'description': 'Earnings, allowances, deductions, and tax calculation rules',
                            'submenus': []
                        },
                        {
                            'code': 'salary_structures',
                            'name': 'Salary Structures',
                            'route': 'payroll:salary_structures',
                            'fallback_url': '/payroll/structures/',
                            'description': 'Salary structure templates by grade and employment level',
                            'submenus': []
                        },
                        {
                            'code': 'employee_salary_setup',
                            'name': 'Employee Compensation Setup',
                            'route': 'payroll:employee_salary_setup',
                            'fallback_url': '/payroll/setup/',
                            'description': 'Individual employee base pay, bank accounts, and benefits',
                            'submenus': []
                        },
                    ]
                }
            ]
        },

        # 9. Expense Management
        {
            'code': 'expense',
            'name': 'Expense Claims & Reimbursements',
            'icon': 'receipt',
            'sort_order': 90,
            'submodules': [
                {
                    'code': 'expense_processing',
                    'name': 'Expense Approvals',
                    'icon': 'wallet',
                    'menus': [
                        {
                            'code': 'admin_expenses',
                            'name': 'Expense Claims (Admin Review)',
                            'route': 'expense:admin_expense_list',
                            'fallback_url': '/expense/admin/',
                            'description': 'Audit receipts, verify business expenses, and approve payouts',
                            'submenus': []
                        },
                    ]
                }
            ]
        },

        # 10. Organization & Locations
        {
            'code': 'branches',
            'name': 'Branches & Locations',
            'icon': 'building-2',
            'sort_order': 100,
            'submodules': [
                {
                    'code': 'facilities_management',
                    'name': 'Offices & Holidays',
                    'icon': 'map-pin',
                    'menus': [
                        {
                            'code': 'branch_list',
                            'name': 'Branch Offices & Geofences',
                            'route': 'branches:branch_list',
                            'fallback_url': '/branches/',
                            'description': 'Company branch locations, GPS coordinates, and radius settings',
                            'submenus': []
                        },
                        {
                            'code': 'holiday_calendar',
                            'name': 'Public & Company Holidays',
                            'route': 'branches:holiday_list',
                            'fallback_url': '/branches/holidays/',
                            'description': 'Official public, government, and corporate holiday calendar',
                            'submenus': []
                        },
                    ]
                }
            ]
        },

        # 11. Administration & Security
        {
            'code': 'accounts',
            'name': 'Administration & System Security',
            'icon': 'shield',
            'sort_order': 110,
            'submodules': [
                {
                    'code': 'roles_and_access',
                    'name': 'Roles & Access Control',
                    'icon': 'shield-check',
                    'menus': [
                        {
                            'code': 'role_list',
                            'name': 'System Roles & Access',
                            'route': 'admin_panel:role_list',
                            'fallback_url': '/admin-panel/roles/',
                            'description': 'Administer dynamic roles, multi-level scopes, and permissions',
                            'submenus': []
                        },
                        {
                            'code': 'permission_matrix',
                            'name': 'Access Matrix Overview',
                            'route': 'admin_panel:permission_matrix',
                            'fallback_url': '/admin-panel/permissions/matrix/',
                            'description': 'Comprehensive matrix of permissions across system roles',
                            'submenus': []
                        },
                    ]
                },
                {
                    'code': 'system_security',
                    'name': 'Security Policies & Audits',
                    'icon': 'lock',
                    'menus': [
                        {
                            'code': 'security_dashboard',
                            'name': 'Security Dashboard',
                            'route': 'admin_panel:security_dashboard',
                            'fallback_url': '/admin-panel/security-dashboard/',
                            'description': 'Security status, MFA enforcement, and authentication metrics',
                            'submenus': []
                        },
                        {
                            'code': 'security_policies',
                            'name': 'Security Policies',
                            'route': 'accounts:admin_security_policies',
                            'fallback_url': '/security/policies/',
                            'description': 'Session expiration, password complexity, and lockout policies',
                            'submenus': []
                        },
                        {
                            'code': 'admin_audit_logs',
                            'name': 'Audit Logs',
                            'route': 'admin_panel:admin_audit_logs',
                            'fallback_url': '/admin-panel/audit-logs/',
                            'description': 'Cryptographically verified security event log and change trail',
                            'submenus': []
                        },
                        {
                            'code': 'activity_logs',
                            'name': 'Activity Logs',
                            'route': 'audit:activity_list',
                            'fallback_url': '/audit/activity/',
                            'description': 'Employee system actions, updates, and operational history',
                            'submenus': []
                        },
                        {
                            'code': 'login_activity',
                            'name': 'Login Activity Monitor',
                            'route': 'accounts:admin_login_activity',
                            'fallback_url': '/login-activity/',
                            'description': 'Audit user authentication attempts, IP origins, and failures',
                            'submenus': []
                        },
                        {
                            'code': 'user_sessions',
                            'name': 'Active User Sessions',
                            'route': 'accounts:user_sessions',
                            'fallback_url': '/account/sessions/',
                            'description': 'Inspect and terminate active device sessions across all staff',
                            'submenus': []
                        },
                        {
                            'code': 'trash_bin',
                            'name': 'Trash & Soft-Delete Queue',
                            'route': 'audit:trash_list',
                            'fallback_url': '/audit/trash/',
                            'description': 'Restore accidentally deleted records and inspect purge queue',
                            'submenus': []
                        },
                        {
                            'code': 'system_backups',
                            'name': 'Database Backups',
                            'route': 'backups:backup_list',
                            'fallback_url': '/backups/',
                            'description': 'Database snapshots, cloud backups, and restore operations',
                            'submenus': []
                        },
                    ]
                }
            ]
        },

        # 12. AI Workspace
        {
            'code': 'ai_workspace',
            'name': 'AI Intelligence Workspace',
            'icon': 'bot',
            'sort_order': 120,
            'submodules': [
                {
                    'code': 'ai_intelligence',
                    'name': 'AI Features & Insights',
                    'icon': 'sparkles',
                    'menus': [
                        {
                            'code': 'ai_assistant',
                            'name': 'AI Executive Assistant',
                            'route': 'admin_panel:ai_assistant',
                            'fallback_url': '/admin-panel/ai/assistant/',
                            'description': 'Conversational AI intelligence for business queries',
                            'submenus': []
                        },
                        {
                            'code': 'ai_attendance_insights',
                            'name': 'AI Attendance Insights',
                            'route': 'admin_panel:ai_attendance_insights',
                            'fallback_url': '/admin-panel/ai/attendance-insights/',
                            'description': 'Predictive absenteeism and anomaly detection analytics',
                            'submenus': []
                        },
                        {
                            'code': 'ai_project_insights',
                            'name': 'AI Project Risk Insights',
                            'route': 'admin_panel:ai_project_insights',
                            'fallback_url': '/admin-panel/ai/project-insights/',
                            'description': 'Project schedule risk detection and completion forecast',
                            'submenus': []
                        },
                        {
                            'code': 'ai_payroll_insights',
                            'name': 'AI Payroll & HR Insights',
                            'route': 'admin_panel:ai_payroll_insights',
                            'fallback_url': '/admin-panel/ai/payroll-insights/',
                            'description': 'Salary expenditure trends, overtime flags, and forecasts',
                            'submenus': []
                        },
                        {
                            'code': 'ai_smart_reports',
                            'name': 'AI Smart Reports',
                            'route': 'admin_panel:ai_smart_reports',
                            'fallback_url': '/admin-panel/ai/smart-reports/',
                            'description': 'Automated narrative executive summaries and dossiers',
                            'submenus': []
                        },
                        {
                            'code': 'ai_settings',
                            'name': 'AI Model & Context Settings',
                            'route': 'admin_panel:ai_settings',
                            'fallback_url': '/admin-panel/ai/settings/',
                            'description': 'Configure Gemini API keys, prompt context, and AI features',
                            'submenus': []
                        },
                    ]
                }
            ]
        },
    ]

    @classmethod
    def safe_resolve_url(cls, route: str, fallback: str = "") -> str:
        """Safely resolves route name to URL path without raising NoReverseMatch."""
        try:
            return reverse(route)
        except (NoReverseMatch, Exception):
            return fallback

    @classmethod
    def get_canonical_hierarchy(cls) -> List[Dict[str, Any]]:
        """
        Builds the normalized 4-level canonical hierarchy tree.
        Every node contains verified URLs, capability specs, and unique node IDs.
        """
        tree = []
        for mod in cls.HIERARCHY_DEFINITION:
            mod_id = f"mod_{mod['code']}"
            mod_node = {
                'id': mod_id,
                'level': 'module',
                'code': mod['code'],
                'name': mod['name'],
                'icon': mod.get('icon', 'box'),
                'sort_order': mod.get('sort_order', 0),
                'perm_prefix': mod['code'],
                'capabilities': ['add', 'edit', 'delete', 'update'],
                'submodules': [],
            }

            for sub in mod.get('submodules', []):
                sub_id = f"sub_{sub['code']}"
                sub_node = {
                    'id': sub_id,
                    'parent_id': mod_id,
                    'level': 'submodule',
                    'code': sub['code'],
                    'name': sub['name'],
                    'icon': sub.get('icon', 'folder'),
                    'perm_prefix': f"{mod['code']}_{sub['code']}",
                    'capabilities': ['add', 'edit', 'delete', 'update'],
                    'menus': [],
                }

                for menu in sub.get('menus', []):
                    menu_id = f"menu_{menu['code']}"
                    menu_url = cls.safe_resolve_url(menu['route'], menu.get('fallback_url', ''))
                    menu_node = {
                        'id': menu_id,
                        'parent_id': sub_id,
                        'level': 'menu',
                        'code': menu['code'],
                        'name': menu['name'],
                        'route': menu.get('route', ''),
                        'url': menu_url,
                        'description': menu.get('description', ''),
                        'perm_prefix': f"{mod['code']}_{menu['code']}",
                        'capabilities': ['add', 'edit', 'delete', 'update'],
                        'submenus': [],
                    }

                    for submenu in menu.get('submenus', []):
                        submenu_id = f"smenu_{submenu['code']}"
                        submenu_url = cls.safe_resolve_url(submenu['route'], submenu.get('fallback_url', ''))
                        submenu_node = {
                            'id': submenu_id,
                            'parent_id': menu_id,
                            'level': 'submenu',
                            'code': submenu['code'],
                            'name': submenu['name'],
                            'route': submenu.get('route', ''),
                            'url': submenu_url,
                            'description': submenu.get('description', ''),
                            'perm_prefix': f"{mod['code']}_{submenu['code']}",
                            'capabilities': ['add', 'edit', 'delete', 'update'],
                        }
                        menu_node['submenus'].append(submenu_node)

                    sub_node['menus'].append(menu_node)

                mod_node['submodules'].append(sub_node)

            tree.append(mod_node)

        return tree

    @classmethod
    def get_all_nodes_flat(cls) -> List[Dict[str, Any]]:
        """Returns flat list of all nodes in the hierarchy with parent and child links."""
        tree = cls.get_canonical_hierarchy()
        flat_list = []

        def walk(node, parent_id=None):
            node_copy = dict(node)
            node_copy['parent_id'] = parent_id
            children_ids = []

            if 'submodules' in node:
                for s in node['submodules']:
                    children_ids.append(s['id'])
                    walk(s, node['id'])
            elif 'menus' in node:
                for m in node['menus']:
                    children_ids.append(m['id'])
                    walk(m, node['id'])
            elif 'submenus' in node:
                for sm in node['submenus']:
                    children_ids.append(sm['id'])
                    walk(sm, node['id'])

            node_copy['children_ids'] = children_ids
            # Remove nested children collections from flat representation
            node_copy.pop('submodules', None)
            node_copy.pop('menus', None)
            node_copy.pop('submenus', None)
            flat_list.append(node_copy)

        for mod in tree:
            walk(mod, None)

        return flat_list

    @classmethod
    def get_node_lookup_maps(cls) -> Tuple[Dict[str, Dict], Dict[str, str], Dict[str, List[str]], Dict[str, Set[str]]]:
        """
        Builds lookup dictionaries:
        - nodes_by_id: { node_id: node_dict }
        - parent_map: { node_id: parent_id }
        - children_map: { node_id: [child_ids] }
        - descendants_map: { node_id: {all_descendant_ids} }
        """
        flat = cls.get_all_nodes_flat()
        nodes_by_id = {n['id']: n for n in flat}
        parent_map = {n['id']: n['parent_id'] for n in flat if n.get('parent_id')}
        children_map = {n['id']: n.get('children_ids', []) for n in flat}

        descendants_map = {}
        def collect_descendants(nid):
            if nid in descendants_map:
                return descendants_map[nid]
            res = set()
            for cid in children_map.get(nid, []):
                res.add(cid)
                res.update(collect_descendants(cid))
            descendants_map[nid] = res
            return res

        for n in flat:
            collect_descendants(n['id'])

        return nodes_by_id, parent_map, children_map, descendants_map

    @classmethod
    def sync_database(cls) -> Dict[str, int]:
        """
        Synchronizes the canonical hierarchy into database Module, Action, and Permission records.
        - Strictly additive and idempotent.
        - Preserves existing permissions, role assignments, and custom IDs.
        - Creates System Owner protected role with GLOBAL scope across all permissions.
        Returns counts: {'modules': count, 'actions': count, 'permissions': count}.
        """
        from apps.accounts.rbac_models import Module, Action, Permission, Role, RolePermission, DataScope

        modules_created = 0
        actions_created = 0
        perms_created = 0

        # 1. Sync Actions (add, edit, delete, update, + legacy view, create, export, approve)
        actions_map = {}
        all_action_defs = cls.ACTIONS + [
            {'code': 'create', 'name': 'Create', 'description': 'Legacy create', 'is_destructive': False},
            {'code': 'view', 'name': 'View', 'description': 'Legacy view', 'is_destructive': False},
            {'code': 'export', 'name': 'Export', 'description': 'Data export', 'is_destructive': False},
            {'code': 'approve', 'name': 'Approve', 'description': 'Workflow approval', 'is_destructive': False},
        ]
        for a_def in all_action_defs:
            act, created = Action.objects.get_or_create(
                code=a_def['code'],
                defaults={'name': a_def['name'], 'description': a_def['description'], 'is_destructive': a_def['is_destructive']}
            )
            if created:
                actions_created += 1
            actions_map[a_def['code']] = act

        # 2. Sync Modules
        modules_map = {}
        for mod_def in cls.HIERARCHY_DEFINITION:
            m_obj, created = Module.objects.get_or_create(
                code=mod_def['code'],
                defaults={
                    'name': mod_def['name'],
                    'icon': mod_def.get('icon', 'box'),
                    'sort_order': mod_def.get('sort_order', 0),
                    'is_active': True
                }
            )
            if created:
                modules_created += 1
            modules_map[mod_def['code']] = m_obj

        # 3. Sync Module and Permissions for every node in hierarchy
        flat_nodes = cls.get_all_nodes_flat()
        all_perms_to_grant = []

        for node in flat_nodes:
            mod_code = node['perm_prefix']
            mod_name = node['name']
            module_obj, created = Module.objects.get_or_create(
                code=mod_code,
                defaults={
                    'name': mod_name,
                    'icon': node.get('icon', 'box'),
                    'sort_order': node.get('sort_order', 50) if node.get('level') == 'module' else 100,
                    'is_active': True
                }
            )
            if created:
                modules_created += 1

            for act_code in ['add', 'edit', 'delete', 'update']:
                act_obj = actions_map.get(act_code)
                if not act_obj:
                    continue

                codename = f"{mod_code}.{act_code}"
                perm_name = f"{act_obj.name} {mod_name}"

                perm, created = Permission.objects.get_or_create(
                    module=module_obj,
                    action=act_obj,
                    defaults={
                        'codename': codename,
                        'name': perm_name,
                        'description': f"Permission to {act_obj.name.lower()} in {mod_name}"
                    }
                )
                if created:
                    perms_created += 1
                all_perms_to_grant.append(perm)

        # 4. Ensure System Owner Protected Role exists with GLOBAL permissions
        sys_owner, _ = Role.objects.get_or_create(
            code='system_owner',
            defaults={
                'name': 'System Owner',
                'description': 'Protected recovery role with full system privileges across all modules.',
                'is_system_protected': True,
                'is_active': True
            }
        )
        if not sys_owner.is_system_protected:
            sys_owner.is_system_protected = True
            sys_owner.save(update_fields=['is_system_protected'])

        for p in all_perms_to_grant:
            RolePermission.objects.get_or_create(
                role=sys_owner,
                permission=p,
                defaults={'data_scope': DataScope.GLOBAL}
            )

        return {
            'modules': modules_created,
            'actions': actions_created,
            'permissions': perms_created
        }

    @classmethod
    def ensure_permission(cls, codename: str):
        """Finds or creates a permission by codename additively."""
        from apps.accounts.rbac_models import Module, Action, Permission

        perm = Permission.objects.filter(codename=codename).first()
        if perm:
            return perm

        if '.' in codename:
            prefix, act_code = codename.rsplit('.', 1)
        else:
            prefix, act_code = codename, 'edit'

        mod, _ = Module.objects.get_or_create(
            code=prefix,
            defaults={'name': prefix.replace('_', ' ').title(), 'icon': 'box', 'sort_order': 99}
        )

        act, _ = Action.objects.get_or_create(
            code=act_code,
            defaults={'name': act_code.capitalize(), 'is_destructive': (act_code == 'delete')}
        )

        perm, _ = Permission.objects.get_or_create(
            module=mod,
            action=act,
            defaults={
                'codename': codename,
                'name': f"{act.name} {mod.name}",
                'description': f"Permission {codename}"
            }
        )
        return perm

    @classmethod
    def get_canonical_permissions_catalog(cls) -> List[Dict[str, Any]]:
        """
        Returns a sorted, deduplicated catalog of all permissions available across
        modules and nodes in the system.
        """
        catalog = []
        seen = set()

        # 1. Existing Permission records in DB
        from apps.accounts.rbac_models import Permission
        try:
            for p in Permission.objects.select_related('module', 'action').order_by('codename'):
                if p.codename not in seen:
                    seen.add(p.codename)
                    catalog.append({
                        'id': p.id,
                        'codename': p.codename,
                        'name': p.name or p.codename,
                        'module': p.module.name if p.module else 'General',
                        'action': p.action.code if p.action else 'general'
                    })
        except Exception:
            pass

        # 2. Canonical hierarchy nodes
        flat_nodes = cls.get_all_nodes_flat()
        for node in flat_nodes:
            mod_code = node['perm_prefix']
            mod_name = node['name']
            for act in ['add', 'edit', 'delete', 'update']:
                codename = f"{mod_code}.{act}"
                if codename not in seen:
                    seen.add(codename)
                    catalog.append({
                        'id': None,
                        'codename': codename,
                        'name': f"{act.capitalize()} {mod_name}",
                        'module': mod_name,
                        'action': act
                    })

        catalog.sort(key=lambda x: (x['module'], x['name']))
        return catalog

