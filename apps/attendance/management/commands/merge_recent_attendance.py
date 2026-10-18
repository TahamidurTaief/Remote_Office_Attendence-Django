"""
Management command to merge the exported recent attendance JSON into PostgreSQL (or default DB)
without losing any IDs, matching employee records accurately.
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.dateparse import parse_datetime, parse_date
import json
import os
from decimal import Decimal

from apps.attendance.models import Attendance, AttendanceLocation
from apps.employees.models import EmployeeProfile


class Command(BaseCommand):
    help = "Merge an exported recent attendance JSON dataset into target database."

    def add_arguments(self, parser):
        parser.add_argument(
            '--file',
            type=str,
            default='recent_attendance_backup.json',
            help='Path to the JSON file to merge.',
        )
        parser.add_argument(
            '--database',
            type=str,
            default='default',
            help='Target database to import into (default: default).',
        )

    def handle(self, *args, **options):
        file_path = options['file']
        db = options['database']

        if not os.path.exists(file_path):
            self.stdout.write(self.style.ERROR(f"File not found: {file_path}"))
            return

        with open(file_path, 'r', encoding='utf-8') as f:
            records = json.load(f)

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"==> Merging {len(records)} attendance records into DB '{db}'..."
        ))

        imported_atts = 0
        imported_locs = 0

        with transaction.atomic(using=db):
            for rec in records:
                # Match employee by employee_id or id
                emp = None
                if rec.get('employee_number'):
                    emp = EmployeeProfile.objects.using(db).filter(employee_id=rec['employee_number']).first()
                if not emp and rec.get('employee_id'):
                    emp = EmployeeProfile.objects.using(db).filter(id=rec['employee_id']).first()

                if not emp:
                    self.stdout.write(self.style.WARNING(
                        f"  -> Skipping attendance for unknown employee: {rec.get('employee_name')} ({rec.get('employee_number')})"
                    ))
                    continue

                att_date = parse_date(rec['date']) if isinstance(rec['date'], str) else rec['date']

                att_obj, created = Attendance.objects.using(db).update_or_create(
                    employee=emp,
                    date=att_date,
                    defaults={
                        'attendance_type': rec.get('attendance_type', 'check_in'),
                        'type': rec.get('type', 'office'),
                        'check_in_time': parse_datetime(rec['check_in_time']) if rec.get('check_in_time') else None,
                        'check_out_time': parse_datetime(rec['check_out_time']) if rec.get('check_out_time') else None,
                        'status': rec.get('status', 'on_time'),
                        'sync_uuid': rec.get('sync_uuid'),
                        'client_event_time': parse_datetime(rec['client_event_time']) if rec.get('client_event_time') else None,
                        'synced_at': parse_datetime(rec['synced_at']) if rec.get('synced_at') else None,
                        'note': rec.get('note', ''),
                    }
                )
                imported_atts += 1

                for loc in rec.get('locations', []):
                    AttendanceLocation.objects.using(db).get_or_create(
                        attendance=att_obj,
                        event=loc.get('event', 'check_in'),
                        defaults={
                            'latitude': Decimal(loc['latitude']),
                            'longitude': Decimal(loc['longitude']),
                            'accuracy': loc.get('accuracy', 0.0),
                            'address': loc.get('address', ''),
                            'is_mocked': loc.get('is_mocked', False),
                            'timestamp': parse_datetime(loc['timestamp']) if loc.get('timestamp') else timezone.now(),
                        }
                    )
                    imported_locs += 1

        self.stdout.write(self.style.SUCCESS(
            f"Successfully merged {imported_atts} attendance sessions and {imported_locs} locations into '{db}'!"
        ))
