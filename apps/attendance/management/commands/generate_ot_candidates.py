import datetime
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.employees.models import EmployeeProfile
from apps.attendance.models import Attendance, OvertimeRequest


class Command(BaseCommand):
    help = 'Daily automated job to generate pending OvertimeRequest candidates from attendance data'

    def add_arguments(self, parser):
        parser.add_argument(
            '--date',
            type=str,
            help='Target date to process (YYYY-MM-DD). Defaults to yesterday.'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Simulate the run without creating database records'
        )

    def handle(self, *args, **options):
        target_date_str = options['date']
        today = timezone.localdate()

        if target_date_str:
            try:
                target_date = datetime.datetime.strptime(target_date_str, '%Y-%m-%d').date()
            except ValueError:
                raise CommandError('Invalid date format. Use YYYY-MM-DD.')
        else:
            target_date = today - datetime.timedelta(days=1)

        if target_date >= today:
            raise CommandError('Cannot process today or future dates.')

        self.stdout.write(f'Processing overtime candidates for date: {target_date}')

        # Find eligible employees with overtime enabled
        eligible_employees = EmployeeProfile.objects.filter(is_active=True, overtime_enabled=True)
        total_eligible = eligible_employees.count()

        created_count = 0
        skipped_no_ot = 0
        skipped_existing = 0

        dry_run = options.get('dry_run', False)

        for emp in eligible_employees:
            attendances = Attendance.objects.filter(
                employee=emp,
                date=target_date,
                overtime_minutes__gt=0
            ).order_by('-overtime_minutes')

            attendance = attendances.first()
            if not attendance:
                skipped_no_ot += 1
                continue

            if OvertimeRequest.objects.filter(employee=emp, date=target_date).exists():
                skipped_existing += 1
                self.stdout.write(f'OvertimeRequest already exists for {emp.full_name} on {target_date}. Skipping.')
                continue

            total_ot_minutes = sum(a.overtime_minutes for a in attendances)

            if not dry_run:
                OvertimeRequest.objects.create(
                    employee=emp,
                    date=target_date,
                    attendance=attendance,
                    ot_minutes=total_ot_minutes,
                    status='pending'
                )
            created_count += 1
            self.stdout.write(f'{"[DRY-RUN] Would create" if dry_run else "Created"} OvertimeRequest for {emp.full_name}: {total_ot_minutes} mins.')

        self.stdout.write(
            self.style.SUCCESS(
                f'Summary: Checked {total_eligible} eligible employees. '
                f'Created {created_count}, Skipped {skipped_no_ot} (no OT), '
                f'Skipped {skipped_existing} (already requested).'
            )
        )
