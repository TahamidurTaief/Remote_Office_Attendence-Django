"""
Proposed schema and migration changes for PasswordResetOTP (FOR REVIEW ONLY - DO NOT APPLY WITHOUT APPROVAL).

Audit finding:
  apps/accounts/models.py:223 currently defines:
      otp_code = models.CharField(max_length=6)
  This blocks storing PBKDF2/HMAC password hashes (64-128 chars) and tracking
  verification attempts, resend cooldowns, and session-bound single-use reset grants.

Proposed Model Fields on PasswordResetOTP:
  - otp_code = models.CharField(max_length=128)
  - failed_attempts = models.PositiveSmallIntegerField(default=0)
  - verified_at = models.DateTimeField(null=True, blank=True)
  - grant_expires_at = models.DateTimeField(null=True, blank=True)
  - grant_consumed_at = models.DateTimeField(null=True, blank=True)
  - session_key = models.CharField(max_length=64, blank=True, db_index=True)
  - last_sent_at = models.DateTimeField(default=timezone.now)
  - request_ip = models.GenericIPAddressField(null=True, blank=True)

Proposed Migration (apps/accounts/migrations/0019_passwordresetotp_hardening.py):
"""
from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0018_usersecurityprofile_workspace_password_hash_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="passwordresetotp",
            name="otp_code",
            field=models.CharField(max_length=128),
        ),
        migrations.AddField(
            model_name="passwordresetotp",
            name="failed_attempts",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="passwordresetotp",
            name="verified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="passwordresetotp",
            name="grant_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="passwordresetotp",
            name="grant_consumed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="passwordresetotp",
            name="session_key",
            field=models.CharField(blank=True, db_index=True, max_length=64),
        ),
        migrations.AddField(
            model_name="passwordresetotp",
            name="last_sent_at",
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
        migrations.AddField(
            model_name="passwordresetotp",
            name="request_ip",
            field=models.GenericIPAddressField(blank=True, null=True),
        ),
    ]
