import datetime
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.attendance.coverage_service import reconcile_attendance_coverage


class Command(BaseCommand):
    help = "Independent daily coverage reconciler attesting employee-day ingestion completeness"

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            type=str,
            help="Target date to reconcile (YYYY-MM-DD). Defaults to yesterday.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulate coverage reconciliation without writing to the database",
        )

    def handle(self, *args, **options):
        today = timezone.localdate()
        raw_date = options.get("date")
        if raw_date:
            try:
                target_date = datetime.datetime.strptime(raw_date, "%Y-%m-%d").date()
            except ValueError:
                raise CommandError("Invalid date format. Use YYYY-MM-DD.")
        else:
            target_date = today - datetime.timedelta(days=1)

        if target_date >= today:
            raise CommandError("Cannot reconcile coverage for today or future dates.")

        counts = reconcile_attendance_coverage(target_date, dry_run=bool(options.get("dry_run")))
        self.stdout.write(
            f"Coverage reconciliation ({target_date}, dry_run={bool(options.get('dry_run'))}): "
            f"complete={counts['complete']}, incomplete_activity={counts['incomplete_activity']}, "
            f"preserved={counts['preserved']}, pending_grace={counts['pending_grace']}"
        )
