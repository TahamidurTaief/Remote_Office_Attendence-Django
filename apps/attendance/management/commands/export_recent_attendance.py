"""
Management command to safely export valid recent attendance records from PostgreSQL (last N weeks)
and merge them back into the target database.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
import json

from apps.attendance.models import Attendance, AttendanceLocation
from apps.employees.models import EmployeeProfile


class Command(BaseCommand):
    help = "Export last N weeks (default: 3 weeks) of real attendance from PostgreSQL and prepare merge dataset."

    def add_arguments(self, parser):
        parser.add_argument(
            '--weeks',
            type=int,
            default=3,
            help='Number of weeks of recent attendance data to extract (default: 3).',
        )
        parser.add_argument(
            '--output',
            type=str,
            default='recent_attendance_backup.json',
            help='Path to output JSON file.',
        )
        parser.add_argument(
            '--database',
            type=str,
            default='default',
            help='Database to read from (default: default/PostgreSQL).',
        )

    def handle(self, *args, **options):
        weeks = options['weeks']
        output_file = options['output']
        db = options['database']

        today = timezone.localdate()
        cutoff_date = today - timedelta(weeks=weeks)

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"==> Extracting attendance data from {cutoff_date} to {today} ({weeks} weeks) from DB '{db}'..."
        ))

        # Filter out deactivated/resigned employee records from recent live data if any
        resigned_profiles = EmployeeProfile.objects.using(db).filter(
            full_name__iregex=r'Mutalef|Arafat|Mostafizur|Rafiqul|Ali|Rashik'
        ).values_list('id', flat=True)

        qs = Attendance.objects.using(db).filter(
            date__gte=cutoff_date
        ).exclude(
            employee_id__in=resigned_profiles
        ).select_related('employee').prefetch_related('locations').order_by('date', 'check_in_time')

        total_count = qs.count()
        self.stdout.write(f"  -> Found {total_count} valid attendance records in this period.")

        data_export = []
        for att in qs:
            locs = []
            for l in att.locations.all():
                locs.append({
                    'event': l.event,
                    'latitude': str(l.latitude),
                    'longitude': str(l.longitude),
                    'accuracy': l.accuracy,
                    'address': l.address,
                    'is_mocked': l.is_mocked,
                    'timestamp': l.timestamp.isoformat() if l.timestamp else None,
                })

            data_export.append({
                'employee_id': att.employee_id,
                'employee_uuid': str(att.employee.master_employee.uuid) if att.employee.master_employee else None,
                'employee_number': att.employee.employee_id,
                'employee_name': att.employee.full_name,
                'date': str(att.date),
                'attendance_type': att.attendance_type,
                'type': att.type,
                'check_in_time': att.check_in_time.isoformat() if att.check_in_time else None,
                'check_out_time': att.check_out_time.isoformat() if att.check_out_time else None,
                'status': att.status,
                'sync_uuid': str(att.sync_uuid) if att.sync_uuid else None,
                'client_event_time': att.client_event_time.isoformat() if att.client_event_time else None,
                'synced_at': att.synced_at.isoformat() if att.synced_at else None,
                'note': att.note,
                'locations': locs,
            })

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(data_export, f, indent=2, ensure_ascii=False)

        self.stdout.write(self.style.SUCCESS(
            f"Successfully exported {len(data_export)} attendance records with locations to {output_file}!"
        ))
