from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import transaction
from apps.accounts.models import Role, UserRoleAssignment
from apps.accounts.engine import PermissionEngine
from apps.accounts.services import RoleAssignmentService

User = get_user_model()


class Command(BaseCommand):
    help = "Explicit, idempotent, and auditable conversion of legacy CustomUser.role strings to active UserRoleAssignment records."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Simulate migration and output summary without persisting changes.',
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        self.stdout.write(self.style.NOTICE(f"Starting legacy role conversion (dry_run={dry_run})..."))

        ROLE_DISPLAY_MAP = {
            'admin': 'Administrator',
            'manager': 'Branch Manager',
            'staff': 'Staff',
            'hr': 'HR Manager',
            'finance': 'Finance Manager',
            'accounts': 'Accounts Officer',
            'employee': 'Staff',
        }

        users = User.objects.all().select_related()
        total_users = users.count()
        newly_assigned = 0
        already_assigned = 0
        skipped = 0

        if not dry_run:
            from apps.accounts.rbac_models import Permission
            if not Permission.objects.exists():
                from apps.accounts.rbac_registry import RBACRegistryService
                RBACRegistryService.sync_database()

        with transaction.atomic():
            for user in users:
                role_code = getattr(user, 'role', None)
                if not role_code:
                    skipped += 1
                    continue

                canonical_code = 'staff' if role_code == 'employee' else role_code
                role_name = ROLE_DISPLAY_MAP.get(canonical_code, canonical_code.capitalize())

                if not dry_run:
                    role_obj, _ = Role.objects.get_or_create(
                        code=canonical_code,
                        defaults={
                            'name': role_name,
                            'description': f"Dynamic role for legacy {role_name} persona.",
                            'is_active': True,
                        }
                    )
                    # Activate if inactive
                    if not role_obj.is_active:
                        role_obj.is_active = True
                        role_obj.save(update_fields=['is_active'])

                    assignment, created = UserRoleAssignment.objects.get_or_create(
                        user=user,
                        role=role_obj,
                    )
                    if created:
                        newly_assigned += 1
                        PermissionEngine.invalidate_user_cache(user)
                    else:
                        already_assigned += 1
                else:
                    # Dry run check
                    exists = UserRoleAssignment.objects.filter(user=user, role__code=canonical_code).exists()
                    if not exists:
                        newly_assigned += 1
                    else:
                        already_assigned += 1

            if dry_run:
                self.stdout.write(self.style.WARNING("DRY RUN: No changes persisted to database."))

        self.stdout.write(self.style.SUCCESS(
            f"Legacy role conversion completed: {total_users} total users processed. "
            f"Newly assigned: {newly_assigned}, Already assigned: {already_assigned}, Skipped: {skipped}."
        ))
