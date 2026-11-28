from django.core.management.base import BaseCommand
from apps.attendance.retention import (
    RETENTION_DISABLED_MESSAGE,
    get_retention_stats,
)


class Command(BaseCommand):
    help = 'Report read-only attendance retention statistics (mutations disabled by default)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show candidate statistics without mutating or deleting records',
        )

    def handle(self, *args, **options):
        stats = get_retention_stats()
        if options['dry_run']:
            self.stdout.write(f'Active: {stats["active_count"]}')
            self.stdout.write(f'Expired: {stats["expired_count"]}')
            self.stdout.write(f'To delete: {stats["to_be_deleted"]}')
            return

        self.stdout.write(self.style.WARNING(RETENTION_DISABLED_MESSAGE))
        self.stdout.write(
            f'Read-only retention report — Active: {stats["active_count"]}, '
            f'Expired: {stats["expired_count"]}, '
            f'Candidate purge count: {stats["to_be_deleted"]}'
        )
