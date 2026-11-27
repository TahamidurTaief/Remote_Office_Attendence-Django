import datetime
from django.core.management.base import BaseCommand
from django.utils import timezone
from apps.attendance.transaction_service import auto_close_past_sessions
from apps.attendance.models import Attendance

class Command(BaseCommand):
    help = 'Reports past unclosed check-in sessions (fabricated auto-close is disabled)'

    def handle(self, *args, **options):
        today = timezone.localdate()
        open_count = Attendance.objects.filter(
            date__lt=today,
            attendance_type='check_in',
            check_out_time__isnull=True,
            is_expired=False
        ).count()
        self.stdout.write(
            self.style.WARNING(
                f"Fabricated auto-close is disabled. {open_count} past unclosed check-in session(s) remain open before {today}."
            )
        )
