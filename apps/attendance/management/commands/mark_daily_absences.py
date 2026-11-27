import datetime
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.attendance.models import Attendance, AttendanceAbsentLog
from apps.attendance.reporting_service import (
    BD_TZ,
    OptimizedSchedule,
    get_daily_report_data,
)
from apps.attendance.models import AttendancePolicy
from apps.employees.models import EmployeeLeaveRule, EmployeeProfile
from apps.leave.models import LeaveBalance, LeaveRequest, LeaveType


class Command(BaseCommand):
    help = 'Daily automated job to mark absent employees and deduct leave balance using canonical employee-day decisions'

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

        active_employees = EmployeeProfile.objects.filter(is_active=True).select_related(
            'branch', 'branch__schedule', 'master_employee', 'master_employee__department'
        )
        emp_ids = list(active_employees.values_list('id', flat=True))
        total_checked = len(emp_ids)

        if not emp_ids:
            self._write_summary(0, 0, 0, 0, 0, 0, 0, 0, options['dry_run'])
            return

        # Bulk canonical employee-day decisions
        report = get_daily_report_data(report_date=target_date, employee_queryset=active_employees)
        rows = report['rows']

        # Bulk auxiliary lookups
        logged_emp_ids = set(
            AttendanceAbsentLog.objects.filter(
                employee_id__in=emp_ids, date=target_date
            ).values_list('employee_id', flat=True)
        )
        pending_leave_emp_ids = set(
            LeaveRequest.objects.filter(
                employee_id__in=emp_ids,
                status__in=['pending', 'manager_approved', 'returned'],
                start_date__lte=target_date,
                end_date__gte=target_date,
            ).values_list('employee_id', flat=True)
        )
        prev_day_open_emp_ids = set(
            Attendance.objects.filter(
                employee_id__in=emp_ids,
                date=target_date - datetime.timedelta(days=1),
                attendance_type='check_in',
                check_out_time__isnull=True,
                is_expired=False,
            ).values_list('employee_id', flat=True)
        )

        # Explicit absence-to-leave deduction policy: requires is_default=True
        default_leave_type = LeaveType.objects.filter(is_active=True, is_default=True).first()
        rules_by_emp_id = {}
        if default_leave_type:
            rules_by_emp_id = {
                r.employee_id: r
                for r in EmployeeLeaveRule.objects.filter(
                    employee_id__in=emp_ids,
                    leave_type=default_leave_type,
                )
            }

        policies = list(AttendancePolicy.objects.all())
        policies_by_branch = {p.branch_id: p for p in policies if p.branch_id is not None}
        global_policy = next((p for p in policies if p.branch_id is None), None)
        sync_grace_minutes = int(getattr(settings, 'ATTENDANCE_SYNC_GRACE_MINUTES', 0) or 0)
        now_local = timezone.localtime(timezone.now(), BD_TZ)

        skipped_present = 0
        skipped_leave = 0
        skipped_holiday = 0
        skipped_not_employed = 0
        skipped_already_logged = 0
        review_needed_count = 0
        deducted_count = 0

        for row in rows:
            emp = row['employee']
            status = row['status']

            if status == 'not_employed':
                skipped_not_employed += 1
                continue

            if status == 'holiday':
                skipped_holiday += 1
                continue

            if row.get('is_present'):
                skipped_present += 1
                continue

            if status == 'on_leave':
                skipped_leave += 1
                continue

            if row.get('needs_review') or row.get('eligibility_unresolved'):
                review_needed_count += 1
                self.stdout.write(self.style.WARNING(
                    f'Employee {emp.full_name} requires review (unresolved eligibility). Skipping deduction.'
                ))
                continue

            if not row.get('is_absent'):
                continue

            # Overnight open shift or unfinished overnight shift + grace window
            if emp.id in prev_day_open_emp_ids:
                skipped_present += 1
                continue

            schedule = OptimizedSchedule(emp, policies_by_branch, global_policy)
            if schedule.office_end_time < schedule.office_start_time:
                end_dt = timezone.make_aware(
                    datetime.datetime.combine(target_date + datetime.timedelta(days=1), schedule.office_end_time),
                    BD_TZ,
                ) + datetime.timedelta(minutes=sync_grace_minutes)
                if now_local < end_dt:
                    skipped_present += 1
                    continue

            # Idempotency check
            if emp.id in logged_emp_ids:
                skipped_already_logged += 1
                continue

            # Pending/returned leave: do not silently override canonical status, flag for review
            if emp.id in pending_leave_emp_ids:
                review_needed_count += 1
                self.stdout.write(self.style.WARNING(
                    f'Employee {emp.full_name} has pending/returned leave on {target_date}; review needed.'
                ))
                continue

            # Explicit deduction policy required
            if not default_leave_type:
                review_needed_count += 1
                self.stdout.write(self.style.WARNING(
                    f'No explicit default LeaveType (is_default=True) configured for {emp.full_name}; review needed.'
                ))
                continue

            deduction_days = Decimal('1.00')
            try:
                LeaveRequest._assert_fractional_balance_supported(deduction_days)
            except Exception as exc:
                review_needed_count += 1
                self.stdout.write(self.style.WARNING(str(exc)))
                continue

            if options['dry_run']:
                deducted_count += 1
                self.stdout.write(
                    f'[DRY-RUN] Employee {emp.full_name} is ABSENT. Would deduct 1 day from {default_leave_type.name}.'
                )
                continue

            try:
                with transaction.atomic():
                    # Recheck state inside transaction
                    if Attendance.objects.filter(employee=emp, date=target_date, is_expired=False).exists():
                        review_needed_count += 1
                        continue
                    if LeaveRequest.objects.filter(
                        employee=emp, status='approved', start_date__lte=target_date, end_date__gte=target_date
                    ).exists():
                        skipped_leave += 1
                        continue
                    if AttendanceAbsentLog.objects.select_for_update().filter(employee=emp, date=target_date).exists():
                        skipped_already_logged += 1
                        continue

                    rule = rules_by_emp_id.get(emp.id)
                    limit = rule.days_per_year if rule else default_leave_type.default_days_per_year
                    balance, _ = LeaveBalance.objects.get_or_create(
                        employee=emp,
                        leave_type=default_leave_type,
                        year=target_date.year,
                        defaults={'total_days': limit},
                    )
                    LeaveBalance.objects.select_for_update().filter(pk=balance.pk).update(
                        used_days=F('used_days') + deduction_days
                    )
                    AttendanceAbsentLog.objects.create(
                        employee=emp,
                        date=target_date,
                        leave_type_deducted=default_leave_type,
                    )
                    deducted_count += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Employee {emp.full_name} marked ABSENT. Deducted 1 day from {default_leave_type.name}.'
                        )
                    )
            except Exception as e:
                self.stdout.write(self.style.ERROR(
                    f'Failed to deduct leave for {emp.full_name}: {str(e)}'
                ))

        self._write_summary(
            total_checked,
            skipped_present,
            skipped_leave,
            skipped_holiday,
            skipped_not_employed,
            skipped_already_logged,
            review_needed_count,
            deducted_count,
            options['dry_run'],
        )

    def _write_summary(
        self,
        total_checked,
        skipped_present,
        skipped_leave,
        skipped_holiday,
        skipped_not_employed,
        skipped_already_logged,
        review_needed_count,
        deducted_count,
        dry_run,
    ):
        self.stdout.write('--- Summary ---')
        self.stdout.write(f'Total active employees checked: {total_checked}')
        self.stdout.write(f'Skipped (present/field/open): {skipped_present}')
        self.stdout.write(f'Skipped (approved leave): {skipped_leave}')
        self.stdout.write(f'Skipped (holiday/off-day): {skipped_holiday}')
        self.stdout.write(f'Skipped (outside employment): {skipped_not_employed}')
        self.stdout.write(f'Skipped (already logged absent): {skipped_already_logged}')
        self.stdout.write(f'Review needed: {review_needed_count}')
        if dry_run:
            self.stdout.write(f'Total employees who would be marked absent: {deducted_count}')
            self.stdout.write(self.style.SUCCESS('Dry run complete! No database changes were made.'))
        else:
            self.stdout.write(f'Total employees marked absent: {deducted_count}')
            self.stdout.write(self.style.SUCCESS('Absence processing complete!'))
