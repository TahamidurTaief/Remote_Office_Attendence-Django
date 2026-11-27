from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.attendance.models import Attendance
from apps.attendance.reminder_service import evaluate_and_send_missing_checkout_reminder
from apps.employees.models import EmployeeProfile


class Command(BaseCommand):
    help = 'Send deduplicated missing-checkout reminders for open attendance sessions.'

    def handle(self, *args, **options):
        now = timezone.now()
        emp_ids = (
            Attendance.objects.filter(
                attendance_type='check_in',
                check_out_time__isnull=True,
                is_expired=False,
            )
            .values_list('employee_id', flat=True)
            .distinct()
        )
        sent_count = 0
        for emp in EmployeeProfile.objects.filter(pk__in=emp_ids, is_active=True).select_related('user', 'branch', 'master_employee'):
            res = evaluate_and_send_missing_checkout_reminder(emp, now=now, send_push=True)
            if res['sent']:
                sent_count += 1
        self.stdout.write(self.style.SUCCESS(f'Missing-checkout reminders sent: {sent_count}'))
