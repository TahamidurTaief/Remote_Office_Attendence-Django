from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from apps.accounts.models import Module, Action, Permission, Role, RolePermission, UserRoleAssignment, DataScope
from apps.accounts.rbac_registry import RBACRegistryService

User = get_user_model()


class Command(BaseCommand):
    help = "Bootstrap default RBAC modules, actions, permissions, and protected System Owner role"

    def handle(self, *args, **options):
        # 1. Sync through canonical RBAC registry
        stats = RBACRegistryService.sync_database()

        # 2. Seed standard active RBAC roles
        default_roles = [
            {
                'code': 'admin',
                'name': 'Administrator',
                'description': 'Full operational administrative access across organization modules.',
                'is_active': True,
            },
            {
                'code': 'manager',
                'name': 'Branch Manager',
                'description': 'Branch and department supervisory access over team activities and approvals.',
                'is_active': True,
            },
            {
                'code': 'staff',
                'name': 'Staff',
                'description': 'Standard employee access for self-service attendance, leave, tasks, and profile.',
                'is_active': True,
            },
        ]
        created_or_updated_roles = {}
        for r_info in default_roles:
            role_obj, _ = Role.objects.update_or_create(
                code=r_info['code'],
                defaults={
                    'name': r_info['name'],
                    'description': r_info['description'],
                    'is_active': True,
                }
            )
            created_or_updated_roles[r_info['code']] = role_obj

        # Ensure any legacy 'employee' role is also active
        Role.objects.filter(code='employee').update(is_active=True)

        # 3. Assign superusers to System Owner and Admin roles
        sys_owner = Role.objects.filter(code='system_owner').first()
        admin_role = created_or_updated_roles.get('admin') or Role.objects.filter(code='admin').first()
        manager_role = created_or_updated_roles.get('manager') or Role.objects.filter(code='manager').first()
        staff_role = created_or_updated_roles.get('staff') or Role.objects.filter(code='staff').first()

        superusers = User.objects.filter(is_superuser=True)
        if sys_owner:
            for su in superusers:
                UserRoleAssignment.objects.get_or_create(user=su, role=sys_owner)
                if admin_role:
                    UserRoleAssignment.objects.get_or_create(user=su, role=admin_role)

        # 4. Populate production-ready default matrix permissions for saved roles
        all_perms = list(Permission.objects.all())
        
        # Matrix rules definition:
        # Admin: All permissions across all modules at COMPANY/GLOBAL scope
        if admin_role:
            for p in all_perms:
                RolePermission.objects.get_or_create(
                    role=admin_role,
                    permission=p,
                    defaults={'data_scope': DataScope.COMPANY}
                )

        # Manager: Supervisory operational access (Branch scope)
        # Full add/edit/update on attendance, leave, schedule, projects, tasks, employees directory
        # View/edit on payroll runs, expense review. Exclude core accounts security & destructive admin actions.
        if manager_role:
            for p in all_perms:
                code = p.codename
                mod_code = p.module.code
                is_destructive = p.action.is_destructive
                
                # Exclude administrative security & system credentials
                if mod_code.startswith('accounts'):
                    continue
                
                # Attendance, Leave, Projects, Tasks, Schedule, Employees: grant non-destructive or operational
                if any(mod_code.startswith(m) for m in ['attendance', 'leave', 'schedule', 'projects', 'tasks', 'employees', 'branches']):
                    # Managers can manage these with BRANCH scope
                    RolePermission.objects.get_or_create(
                        role=manager_role,
                        permission=p,
                        defaults={'data_scope': DataScope.BRANCH}
                    )
                elif any(mod_code.startswith(m) for m in ['payroll', 'expense', 'dashboard', 'ai_workspace']):
                    # Read/process permissions without raw record deletion
                    if not is_destructive:
                        RolePermission.objects.get_or_create(
                            role=manager_role,
                            permission=p,
                            defaults={'data_scope': DataScope.BRANCH}
                        )

        # Staff & Employee: Self-service capabilities (OWN / TEAM scope)
        # Attendance self check-in, Leave self-apply, Project/Task view & update own, Profile view
        self_service_roles = [r for r in [staff_role, Role.objects.filter(code='employee').first()] if r]
        for s_role in self_service_roles:
            for p in all_perms:
                code = p.codename
                mod_code = p.module.code
                act_code = p.action.code

                # Exclude destructive permissions for staff
                if p.action.is_destructive:
                    continue

                # Attendance: check-in/out, punch requests, view status
                if mod_code.startswith('attendance'):
                    if any(k in mod_code for k in ['attendance_operations', 'attendance_requests', 'live_attendance', 'attendance']) and act_code in ['add', 'edit', 'update']:
                        RolePermission.objects.get_or_create(
                            role=s_role,
                            permission=p,
                            defaults={'data_scope': DataScope.OWN}
                        )
                # Leave: submit leave applications, view balances
                elif mod_code.startswith('leave'):
                    if act_code in ['add', 'edit', 'update'] and 'admin' not in mod_code:
                        RolePermission.objects.get_or_create(
                            role=s_role,
                            permission=p,
                            defaults={'data_scope': DataScope.OWN}
                        )
                # Projects & Tasks: collaborate on tasks
                elif mod_code.startswith('projects') or mod_code.startswith('tasks'):
                    if act_code in ['edit', 'update', 'add']:
                        RolePermission.objects.get_or_create(
                            role=s_role,
                            permission=p,
                            defaults={'data_scope': DataScope.TEAM}
                        )
                # Expense: submit claims
                elif mod_code.startswith('expense'):
                    if act_code in ['add', 'edit', 'update'] and 'admin' not in mod_code:
                        RolePermission.objects.get_or_create(
                            role=s_role,
                            permission=p,
                            defaults={'data_scope': DataScope.OWN}
                        )
                # Schedules & Calendar: view schedule
                elif mod_code.startswith('schedule'):
                    if act_code in ['edit', 'update']:
                        RolePermission.objects.get_or_create(
                            role=s_role,
                            permission=p,
                            defaults={'data_scope': DataScope.BRANCH}
                        )
                # Dashboard: overview
                elif mod_code.startswith('dashboard'):
                    if act_code in ['edit', 'update']:
                        RolePermission.objects.get_or_create(
                            role=s_role,
                            permission=p,
                            defaults={'data_scope': DataScope.OWN}
                        )

        # 5. Backfill UserRoleAssignment for existing users based on user.role
        assigned_count = 0
        for u in User.objects.all():
            target_role = None
            if u.role == 'admin':
                target_role = admin_role
            elif u.role == 'manager':
                target_role = manager_role
            elif u.role in ('staff', 'employee'):
                target_role = staff_role

            if target_role:
                _, created = UserRoleAssignment.objects.get_or_create(user=u, role=target_role)
                if created:
                    assigned_count += 1

        self.stdout.write(self.style.SUCCESS(
            f"Successfully bootstrapped RBAC engine: {Module.objects.count()} modules, {Action.objects.count()} actions, "
            f"{Permission.objects.count()} permissions, {Role.objects.count()} roles ({Role.objects.filter(is_active=True).count()} active), "
            f"{RolePermission.objects.count()} total matrix role permissions populated, "
            f"and backfilled {assigned_count} user role assignments."
        ))
