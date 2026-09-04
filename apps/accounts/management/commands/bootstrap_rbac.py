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

        # 2. Assign all superusers to System Owner role
        sys_owner = Role.objects.filter(code='system_owner').first()
        superusers = User.objects.filter(is_superuser=True)
        if sys_owner:
            for su in superusers:
                UserRoleAssignment.objects.get_or_create(user=su, role=sys_owner)

        self.stdout.write(self.style.SUCCESS(
            f"Successfully bootstrapped RBAC engine: {Module.objects.count()} modules, {Action.objects.count()} actions, "
            f"{Permission.objects.count()} permissions, and System Owner role assigned to {superusers.count()} superusers."
        ))
