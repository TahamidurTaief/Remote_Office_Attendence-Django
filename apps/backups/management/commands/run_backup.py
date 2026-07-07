from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from apps.backups.utils import create_backup
from apps.backups.models import GoogleDriveConfig


class Command(BaseCommand):
    help = "Run scheduled backup"

    def add_arguments(self, parser):
        parser.add_argument(
            "--type",
            type=str,
            default="auto_daily",
            choices=["manual", "auto_daily", "auto_3day"],
            help="Backup type",
        )

    def handle(self, *args, **options):
        backup_type = options["type"]
        config = GoogleDriveConfig.get_config()

        if backup_type == "auto_daily":
            if not config.auto_daily_enabled:
                self.stdout.write("Daily backup disabled. Skipping.")
                return
        elif backup_type == "auto_3day":
            if not config.auto_3day_enabled:
                self.stdout.write("3-day backup disabled. Skipping.")
                return
            if config.last_backup_at:
                next_backup = config.last_backup_at + timedelta(days=3)
                if timezone.now() < next_backup:
                    self.stdout.write("Too soon for 3-day backup.")
                    return

        self.stdout.write(f"Running {backup_type} backup...")

        try:
            backup = create_backup(backup_type=backup_type)

            config.last_backup_at = timezone.now()
            config.save(update_fields=["last_backup_at", "updated_at"])

            self.stdout.write(
                self.style.SUCCESS(
                    "Backup complete: "
                    f"{backup.file_name} "
                    f"({backup.record_count} records, "
                    f"{backup.file_size / 1024:.1f} KB)"
                )
            )

            if backup.gdrive_uploaded:
                self.stdout.write("✓ Uploaded to Google Drive")

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f"Backup failed: {str(e)}")
            )
