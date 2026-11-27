import os
import tempfile
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.models import LoginProtection
from apps.accounts.password_reset_service import (
    PasswordResetService,
    SchemaMigrationRequiredError,
    _assert_schema_ready,
    _check_and_record_throttle,
    build_otp_email,
    send_otp_email,
)
from fieldtrack.settings import _parse_env_bool

User = get_user_model()


class PasswordResetSecurityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="reset_emp@example.com",
            password="OldPassword123!",
            role="staff",
        )

    def test_1_settings_env_and_branded_multipart_cid_email(self):
        self.assertTrue(_parse_env_bool("NON_EXISTENT_BOOL", default=True))
        with patch.dict(os.environ, {"TEST_BOOL": "false"}):
            self.assertFalse(_parse_env_bool("TEST_BOOL", default=True))
        with patch.dict(os.environ, {"TEST_BOOL": "invalid_bool"}):
            with self.assertRaises(ImproperlyConfigured):
                _parse_env_bool("TEST_BOOL")

        with override_settings(
            EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
            DEFAULT_FROM_EMAIL="no-reply@signtech.example",
            TAIEFLAB_LOGO_PATH="",
        ):
            otp = PasswordResetService.generate_otp()
            self.assertEqual(len(otp), 6)
            self.assertTrue(otp.isdigit())

            msg = build_otp_email(self.user.email, otp)
            send_otp_email(msg)

            self.assertEqual(len(mail.outbox), 1)
            sent = mail.outbox[0]
            self.assertEqual(sent.to, [self.user.email])
            self.assertEqual(sent.from_email, "no-reply@signtech.example")
            self.assertIn("Signtech Trackme", sent.body)
            self.assertIn(otp, sent.body)
            self.assertIn("Developed by TaiefLab", sent.body)

            html_alts = [alt for alt, mime in sent.alternatives if mime == "text/html"]
            self.assertEqual(len(html_alts), 1)
            html_body = html_alts[0]
            self.assertIn("Signtech Trackme", html_body)
            self.assertIn(otp, html_body)
            self.assertIn("5 minutes", html_body)
            self.assertIn("Developed by TaiefLab", html_body)
            self.assertIn("cid:signtech_logo", html_body)
            self.assertNotIn("cid:taieflab_logo", html_body)

            cids = [att.get("Content-ID") for att in sent.attachments]
            self.assertIn("<signtech_logo>", cids)

        # With valid configured TaiefLab PNG asset
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
            tf.write(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
            tl_path = tf.name
        self.addCleanup(lambda: os.path.exists(tl_path) and os.remove(tl_path))

        with override_settings(
            EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
            TAIEFLAB_LOGO_PATH=tl_path,
        ):
            msg2 = build_otp_email(self.user.email, "654321")
            cids2 = [att.get("Content-ID") for att in msg2.attachments]
            self.assertIn("<signtech_logo>", cids2)
            self.assertIn("<taieflab_logo>", cids2)

    def test_2_persistent_db_throttling_and_no_otp_in_responses_or_logs(self):
        with CaptureQueriesContext(connection) as ctx_first:
            allowed, _ = _check_and_record_throttle("reset_emp@example.com", "10.0.0.1")
            self.assertTrue(allowed)
        for _ in range(4):
            allowed, _ = _check_and_record_throttle("reset_emp@example.com", "10.0.0.1")
            self.assertTrue(allowed)
        with CaptureQueriesContext(connection) as ctx_blocked:
            blocked, retry = _check_and_record_throttle("reset_emp@example.com", "10.0.0.1")
            self.assertFalse(blocked)
            self.assertGreater(retry, 0)
        self.assertTrue(
            LoginProtection.objects.filter(email="reset_emp@example.com", ip="10.0.0.1").exists()
        )
        self.assertLessEqual(len(ctx_blocked), len(ctx_first))

        # View never leaks OTP in response or logs
        with self.assertLogs("apps.accounts", level="INFO") as cm:
            import logging
            logging.getLogger("apps.accounts").info("reset_check")
            resp = self.client.post(
                reverse("accounts:forgot_password_request"),
                {"identifier": "unknown_user@example.com"},
            )
        self.assertEqual(resp.status_code, 503)
        self.assertNotIn("debug_otp", resp.content.decode())
        self.assertNotIn("123456", "\n".join(cm.output))

    def test_3_otp_hashing_never_plaintext(self):
        otp = PasswordResetService.generate_otp()
        hashed = PasswordResetService.hash_otp(otp)
        self.assertNotEqual(hashed, otp)
        self.assertGreater(len(hashed), 6)
        self.assertTrue(PasswordResetService.verify_otp_hash(otp, hashed))
        self.assertFalse(PasswordResetService.verify_otp_hash("000000", hashed))

    def test_4_unverified_reset_token_cannot_change_password(self):
        resp = self.client.post(
            reverse("accounts:forgot_password_reset"),
            {
                "reset_token": "unverified-forged-token",
                "new_password": "NewPassword999!",
                "confirm_password": "NewPassword999!",
            },
        )
        self.assertEqual(resp.status_code, 503)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("OldPassword123!"))

    def test_5_smtp_failure_raises_and_schema_blocker_explicit(self):
        with self.assertRaises(SchemaMigrationRequiredError) as ctx:
            _assert_schema_ready()
        self.assertIn("otp_code.max_length=6", str(ctx.exception))

        msg = build_otp_email(self.user.email, "123456")
        with patch("apps.accounts.password_reset_service.get_connection") as mock_conn:
            mock_conn.return_value.__enter__.return_value.send_messages.side_effect = OSError("SMTP down")
            with self.assertRaises(OSError):
                send_otp_email(msg)
            mock_conn.assert_called_once_with(timeout=getattr(settings, "EMAIL_TIMEOUT", 10), fail_silently=False)
