import datetime
from django.core.management.base import BaseCommand
from django.utils import timezone
from apps.attendance.transaction_service import auto_close_past_sessions
from apps.attendance.models import Attendance

class Command(BaseCommand):
    help = 'Auto-closes dangling unclosed check-in sessions from dates prior to today'

    def handle(self, *args, **options):
        today = timezone.localdate()
        self.stdout.write(f"Checking for past unclosed check-in sessions before {today}...")
        closed_count = auto_close_past_sessions(employee=None, today=today)
        self.stdout.write(self.style.SUCCESS(f"Successfully auto-closed {closed_count} past unclosed session(s)."))
