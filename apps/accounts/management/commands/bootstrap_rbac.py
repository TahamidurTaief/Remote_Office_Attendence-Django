from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from apps.accounts.models import Module, Action, Permission, Role, RolePermission, UserRoleAssignment, DataScope

User = get_user_model()

DEFAULT_MODULES = [
    {'code': 'attendance', 'name': 'Attendance', 'icon': 'clock', 'sort_order': 10},
    {'code': 'employees', 'name': 'Employees', 'icon': 'users', 'sort_order': 20},
    {'code': 'projects', 'name': 'Projects', 'icon': 'briefcase', 'sort_order': 30},
    {'code': 'leave', 'name': 'Leave Management', 'icon': 'clipboard-check', 'sort_order': 40},
    {'code': 'expense', 'name': 'Expenses', 'icon': 'receipt', 'sort_order': 50},
    {'code': 'schedule', 'name': 'Schedules & Roster', 'icon': 'calendar', 'sort_order': 60},
    {'code': 'branches', 'name': 'Branches & Locations', 'icon': 'building-2', 'sort_order': 70},
    {'code': 'notifications', 'name': 'Notifications & Logs', 'icon': 'bell', 'sort_order': 80},
    {'code': 'backups', 'name': 'System Backups', 'icon': 'database', 'sort_order': 90},
    {'code': 'accounts', 'name': 'Accounts & Security', 'icon': 'shield', 'sort_order': 100},
]

DEFAULT_ACTIONS = [
    {'code': 'view', 'name': 'View', 'is_destructive': False},
    {'code': 'create', 'name': 'Create', 'is_destructive': False},
    {'code': 'edit', 'name': 'Edit', 'is_destructive': False},
    {'code': 'delete', 'name': 'Delete', 'is_destructive': True},
    {'code': 'export', 'name': 'Export', 'is_destructive': False},
    {'code': 'approve', 'name': 'Approve', 'is_destructive': False},
]


class Command(BaseCommand):
    help = "Bootstrap default RBAC modules, actions, permissions, and protected System Owner role"

    def handle(self, *args, **options):
        # 1. Modules
        modules = {}
        for m_data in DEFAULT_MODULES:
            mod, _ = Module.objects.get_or_create(
                code=m_data['code'],
                defaults={
                    'name': m_data['name'],
                    'icon': m_data['icon'],
                    'sort_order': m_data['sort_order']
                }
            )
            modules[m_data['code']] = mod

        # 2. Actions
        actions = {}
        for a_data in DEFAULT_ACTIONS:
            act, _ = Action.objects.get_or_create(
                code=a_data['code'],
                defaults={
                    'name': a_data['name'],
                    'is_destructive': a_data['is_destructive']
                }
            )
            actions[a_data['code']] = act

        # 3. Permissions
        permissions = []
        for mod_code, mod_obj in modules.items():
            for act_code, act_obj in actions.items():
                perm, _ = Permission.objects.get_or_create(
                    module=mod_obj,
                    action=act_obj,
                    defaults={
                        'codename': f"{mod_code}.{act_code}",
                        'name': f"{act_obj.name} {mod_obj.name}"
                    }
                )
                permissions.append(perm)

        # 4. System Owner Protected Role
        sys_owner, created = Role.objects.get_or_create(
            code='system_owner',
            defaults={
                'name': 'System Owner',
                'description': 'Protected recovery role with full system privileges.',
                'is_system_protected': True,
                'is_active': True
            }
        )
        if not sys_owner.is_system_protected:
            sys_owner.is_system_protected = True
            sys_owner.save()

        # Grant all permissions to System Owner role
        for perm in permissions:
            RolePermission.objects.get_or_create(
                role=sys_owner,
                permission=perm,
                defaults={'data_scope': DataScope.GLOBAL}
            )

        # Assign superusers to System Owner role
        superusers = User.objects.filter(is_superuser=True)
        for su in superusers:
            UserRoleAssignment.objects.get_or_create(user=su, role=sys_owner)

        self.stdout.write(self.style.SUCCESS(
            f"Successfully bootstrapped RBAC engine: {len(modules)} modules, {len(actions)} actions, "
            f"{len(permissions)} permissions, and System Owner role assigned to {superusers.count()} superusers."
        ))
