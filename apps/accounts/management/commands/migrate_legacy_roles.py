from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import transaction
from apps.accounts.models import Role, UserRoleAssignment
from apps.accounts.services import RoleAssignmentService

User = get_user_model()


class Command(BaseCommand):
    help = "Explicit, idempotent, and auditable conversion of legacy CustomUser.role strings to active UserRoleAssignment records."

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            default=False,
            help='Persist role migrations to database. Without this flag, command runs in safe dry-run mode.',
        )

    def handle(self, *args, **options):
        apply_changes = options.get('apply', False)
        dry_run = not apply_changes

        if dry_run:
            self.stdout.write(self.style.WARNING("Running in DRY-RUN mode. Specify --apply to execute changes."))
        else:
            self.stdout.write(self.style.NOTICE("Executing legacy role migration with --apply..."))

        ROLE_DISPLAY_MAP = {
            'admin': 'Administrator',
            'manager': 'Branch Manager',
            'staff': 'Staff',
            'employee': 'Staff',
        }

        users = User.objects.all().order_by('id')
        total_users = users.count()
        newly_assigned = 0
        already_assigned = 0
        skipped = 0

        with transaction.atomic():
            for user in users:
                role_code = getattr(user, 'role', None)
                if not role_code or role_code not in ROLE_DISPLAY_MAP:
                    skipped += 1
                    continue

                canonical_code = 'staff' if role_code == 'employee' else role_code
                role_name = ROLE_DISPLAY_MAP[role_code]

                role_obj = Role.objects.filter(code=canonical_code).first()
                if not role_obj:
                    if apply_changes:
                        role_obj = Role.objects.create(
                            code=canonical_code,
                            name=role_name,
                            description=f"Dynamic role for legacy {role_name} persona.",
                            is_active=True,
                        )
                    else:
                        newly_assigned += 1
                        continue

                # Never reactivate inactive roles silently
                if not role_obj.is_active:
                    self.stdout.write(self.style.WARNING(
                        f"Skipping assignment for {user.email or user.phone}: role '{role_obj.code}' is inactive."
                    ))
                    skipped += 1
                    continue

                has_assignment = UserRoleAssignment.objects.filter(user=user, role=role_obj).exists()
                if has_assignment:
                    already_assigned += 1
                    continue

                if apply_changes:
                    # Use approved atomic assignment service which logs audit events and invalidates cache
                    RoleAssignmentService.sync_user_roles(
                        user=user,
                        target_roles=[role_obj],
                        actor=None,
                        trusted_internal=True
                    )
                    newly_assigned += 1
                else:
                    newly_assigned += 1

            if dry_run:
                self.stdout.write(self.style.WARNING("DRY RUN COMPLETE: Zero database modifications performed."))

        self.stdout.write(self.style.SUCCESS(
            f"Legacy role conversion summary: {total_users} users evaluated. "
            f"Eligible for assignment: {newly_assigned}, Already assigned: {already_assigned}, Skipped: {skipped}."
        ))
