import datetime
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.conf import settings
from django.db import transaction
from django.db.models import F

from apps.employees.models import EmployeeProfile
from apps.attendance.models import Attendance, AttendanceAbsentLog
from apps.leave.models import LeaveType, LeaveBalance, LeaveRequest


class Command(BaseCommand):
    help = 'Daily automated job to mark absent employees and deduct leave balance'

    def add_arguments(self, parser):
        parser.add_argument(
            '--date',
            type=str,
            help='Target date to process (YYYY-MM-DD). Defaults to yesterday.'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Simulate the run without modifying database records'
        )

    def handle(self, *args, **options):
        # 1. Parse and validate date
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

        self.stdout.write(f'Processing absences for date: {target_date}')

        # 2. Check if working day
        working_days = getattr(settings, 'WORKING_DAYS', [0, 1, 2, 3, 4, 5])
        if target_date.weekday() not in working_days:
            self.stdout.write(f'Date {target_date} is not a working day. Skipping.')
            return

        active_employees = EmployeeProfile.objects.filter(is_active=True)
        total_checked = active_employees.count()
        skipped_present = 0
        skipped_leave = 0
        skipped_already_logged = 0
        skipped_no_leavetype = 0
        deducted_count = 0

        # Pre-fetch deduction leave type
        leave_type = LeaveType.objects.filter(category='casual').first()
        if not leave_type:
            leave_type = LeaveType.objects.filter(category='sick').first()
        if not leave_type:
            leave_type = LeaveType.objects.order_by('id').first()

        for emp in active_employees:
            # Check if attendance exists
            if Attendance.objects.filter(employee=emp, date=target_date).exists():
                skipped_present += 1
                self.stdout.write(f'Employee {emp.full_name} had attendance. Skipping.')
                continue

            # Check if approved leave request covers target_date
            if LeaveRequest.objects.filter(
                employee=emp,
                status='approved',
                start_date__lte=target_date,
                end_date__gte=target_date
            ).exists():
                skipped_leave += 1
                self.stdout.write(f'Employee {emp.full_name} has approved leave. Skipping.')
                continue

            # Check if already logged (idempotency)
            if AttendanceAbsentLog.objects.filter(employee=emp, date=target_date).exists():
                skipped_already_logged += 1
                self.stdout.write(f'Employee {emp.full_name} already logged as absent. Skipping.')
                continue

            if not leave_type:
                skipped_no_leavetype += 1
                self.stdout.write(self.style.WARNING(
                    f'Employee {emp.full_name} has no LeaveType configured to deduct from. Skipping.'
                ))
                continue

            # Process deduction
            if options['dry_run']:
                deducted_count += 1
                self.stdout.write(
                    f'[DRY-RUN] Employee {emp.full_name} is ABSENT. Would deduct 1 day from {leave_type.name}.'
                )
            else:
                try:
                    with transaction.atomic():
                        # Get or create balance for employee / leave type / year
                        balance, _ = LeaveBalance.objects.get_or_create(
                            employee=emp,
                            leave_type=leave_type,
                            year=target_date.year,
                            defaults={'total_days': leave_type.default_days_per_year}
                        )
                        balance.used_days = F('used_days') + 1
                        balance.save()

                        # Create log entry
                        AttendanceAbsentLog.objects.create(
                            employee=emp,
                            date=target_date,
                            leave_type_deducted=leave_type
                        )

                        deducted_count += 1
                        self.stdout.write(
                            self.style.SUCCESS(
                                f'Employee {emp.full_name} marked ABSENT. Deducted 1 day from {leave_type.name}.'
                            )
                        )
                except Exception as e:
                    self.stdout.write(self.style.ERROR(
                        f'Failed to deduct leave for {emp.full_name}: {str(e)}'
                    ))

        # Output summary stats
        self.stdout.write('--- Summary ---')
        self.stdout.write(f'Total active employees checked: {total_checked}')
        self.stdout.write(f'Skipped (had attendance): {skipped_present}')
        self.stdout.write(f'Skipped (had approved leave): {skipped_leave}')
        self.stdout.write(f'Skipped (already logged absent): {skipped_already_logged}')
        if skipped_no_leavetype:
            self.stdout.write(f'Skipped (no LeaveType configured): {skipped_no_leavetype}')
        
        if options['dry_run']:
            self.stdout.write(f'Total employees who would be marked absent: {deducted_count}')
            self.stdout.write(self.style.SUCCESS('Dry run complete! No database changes were made.'))
        else:
            self.stdout.write(f'Total employees marked absent: {deducted_count}')
            self.stdout.write(self.style.SUCCESS('Absence processing complete!'))
