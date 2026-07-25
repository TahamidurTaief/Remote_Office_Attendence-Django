from django.db import models
from django.conf import settings


class BackupRecord(models.Model):
    BACKUP_TYPES = [
        ("manual", "Manual"),
        ("auto_daily", "Auto Daily"),
        ("auto_3day", "Auto Every 3 Days"),
    ]
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("completed", "Completed"),
        ("failed", "Failed"),
    ]

    backup_type = models.CharField(
        max_length=20, choices=BACKUP_TYPES
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="pending"
    )
    file_name = models.CharField(max_length=255)
    file_size = models.IntegerField(
        default=0, help_text="Size in bytes"
    )
    record_count = models.IntegerField(default=0)

    gdrive_uploaded = models.BooleanField(default=False)
    gdrive_file_id = models.CharField(max_length=255, blank=True)
    gdrive_link = models.URLField(blank=True)

    file_path = models.CharField(max_length=500, blank=True)
    is_encrypted = models.BooleanField(
        default=False,
        help_text="Whether the on-disk backup file is Fernet-encrypted",
    )

    error_message = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="backup_records",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_backup_type_display()} - {self.file_name}"


class GoogleDriveConfig(models.Model):
    """Singleton model for Google Drive configuration."""
    is_enabled = models.BooleanField(default=False)
    service_account_json = models.TextField(
        blank=True,
        help_text="Paste Google Service Account JSON key here",
    )
    folder_id = models.CharField(
        max_length=255,
        blank=True,
        help_text="Google Drive folder ID for backups",
    )

    auto_daily_enabled = models.BooleanField(
        default=False,
        help_text="Run backup every day at 2 AM",
    )
    auto_3day_enabled = models.BooleanField(
        default=False,
        help_text="Run backup every 3 days",
    )
    auto_upload_to_drive = models.BooleanField(
        default=False,
        help_text="Auto upload backups to Google Drive",
    )

    keep_local_copies = models.IntegerField(
        default=7,
        help_text="Keep this many local backup files",
    )

    # ── Encryption ────────────────────────────────────────────────────────────
    encryption_enabled = models.BooleanField(
        default=False,
        help_text="Encrypt backup files at rest using a server-bound Fernet key",
    )
    master_key_wrapped = models.TextField(
        blank=True,
        help_text=(
            "Base64-encoded Fernet key wrapped with a KEK derived from SECRET_KEY. "
            "Never stores the raw key. Set via 'Generate Key' action."
        ),
    )

    last_backup_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Google Drive Config"
        permissions = [
            ('run_backup', 'Can run manual backup'),
        ]

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_config(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return "Google Drive Config"
