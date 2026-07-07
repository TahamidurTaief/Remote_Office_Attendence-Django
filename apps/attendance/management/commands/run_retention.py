from django.core.management.base import BaseCommand
from apps.attendance.retention import (
    mark_expired_records,
    delete_old_expired_records,
    get_retention_stats
)

class Command(BaseCommand):
    help = 'Run data retention: mark expired and delete old'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without deleting'
        )
    
    def handle(self, *args, **options):
        if options['dry_run']:
            stats = get_retention_stats()
            self.stdout.write(f'Active: {stats["active_count"]}')
            self.stdout.write(f'Expired: {stats["expired_count"]}')
            self.stdout.write(f'To delete: {stats["to_be_deleted"]}')
            return
            
        self.stdout.write('Running retention...')
        
        expired = mark_expired_records()
        self.stdout.write(
            f'Marked expired: {expired}')
        
        deleted_att, deleted_loc = (
            delete_old_expired_records())
        self.stdout.write(
            f'Deleted: {deleted_att} attendance, '
            f'{deleted_loc} locations'
        )
        
        self.stdout.write(
            self.style.SUCCESS('Retention complete!'))
