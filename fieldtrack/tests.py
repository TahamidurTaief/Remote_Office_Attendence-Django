import importlib
import os
from unittest import mock
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase
from django.conf import settings


class SettingsSecurityTests(SimpleTestCase):
    """Focused tests for environment parsing, security defaults, and production guardrails."""

    def test_current_settings_sqlite_database(self):
        """Verify default active database is SQLite with timeout configured."""
        self.assertEqual(settings.DATABASES['default']['ENGINE'], 'django.db.backends.sqlite3')
        self.assertEqual(settings.DATABASES['default']['OPTIONS'].get('timeout'), 5.0)

    def test_current_settings_tenant_ui_hidden_by_default(self):
        """Verify multi-tenancy foundation is enabled but UI is hidden by default."""
        self.assertTrue(settings.TENANCY_ENABLED)
        self.assertFalse(settings.TENANT_UI_ENABLED)
        self.assertEqual(settings.DEFAULT_TENANT_SLUG, 'signtech')

    def test_current_settings_base_security_headers(self):
        """Verify essential base security headers and cookie flags."""
        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)
        self.assertEqual(settings.SESSION_COOKIE_SAMESITE, 'Lax')
        self.assertTrue(settings.CSRF_COOKIE_HTTPONLY)
        self.assertEqual(settings.CSRF_COOKIE_SAMESITE, 'Lax')
        self.assertTrue(settings.SECURE_CONTENT_TYPE_NOSNIFF)
        self.assertEqual(settings.X_FRAME_OPTIONS, 'DENY')
        self.assertEqual(settings.SECURE_REFERRER_POLICY, 'same-origin')

    @mock.patch.dict(
        os.environ,
        {
            'DJANGO_IGNORE_ENV_FILE': '1',
            'DEBUG': 'False',
            'DJANGO_SECRET_KEY': 'django-insecure-test-should-fail',
            'ALLOWED_HOSTS': 'example.com',
        },
        clear=True,
    )
    def test_production_rejects_insecure_secret_key(self):
        """Verify DEBUG=False raises ImproperlyConfigured when given an insecure fallback key."""
        from fieldtrack import settings as fieldtrack_settings
        with self.assertRaises(ImproperlyConfigured):
            importlib.reload(fieldtrack_settings)

    @mock.patch.dict(
        os.environ,
        {
            'DJANGO_IGNORE_ENV_FILE': '1',
            'DEBUG': 'False',
            'DJANGO_SECRET_KEY': '',
            'ALLOWED_HOSTS': 'example.com',
        },
        clear=True,
    )
    def test_production_rejects_empty_secret_key(self):
        """Verify DEBUG=False raises ImproperlyConfigured when DJANGO_SECRET_KEY is empty."""
        from fieldtrack import settings as fieldtrack_settings
        with self.assertRaises(ImproperlyConfigured):
            importlib.reload(fieldtrack_settings)

    @mock.patch.dict(
        os.environ,
        {
            'DJANGO_IGNORE_ENV_FILE': '1',
            'DEBUG': 'False',
            'DJANGO_SECRET_KEY': 'a-valid-production-secret-key-with-sufficient-entropy-12345',
            'ALLOWED_HOSTS': '',
        },
        clear=True,
    )
    def test_production_rejects_empty_allowed_hosts(self):
        """Verify DEBUG=False raises ImproperlyConfigured when ALLOWED_HOSTS is empty."""
        from fieldtrack import settings as fieldtrack_settings
        with self.assertRaises(ImproperlyConfigured):
            importlib.reload(fieldtrack_settings)

    @mock.patch.dict(
        os.environ,
        {
            'DJANGO_IGNORE_ENV_FILE': '1',
            'DEBUG': 'False',
            'DJANGO_SECRET_KEY': 'a-valid-production-secret-key-with-sufficient-entropy-12345',
            'ALLOWED_HOSTS': 'trackme.signtechlimited.com, app.example.com',
            'CSRF_TRUSTED_ORIGINS': 'https://trackme.signtechlimited.com, https://app.example.com',
        },
        clear=True,
    )
    def test_production_valid_configuration_loads(self):
        """Verify valid production settings parse correctly without error."""
        from fieldtrack import settings as fieldtrack_settings
        try:
            reloaded = importlib.reload(fieldtrack_settings)
            self.assertFalse(reloaded.DEBUG)
            self.assertEqual(
                reloaded.ALLOWED_HOSTS,
                ['trackme.signtechlimited.com', 'app.example.com'],
            )
            self.assertEqual(
                reloaded.CSRF_TRUSTED_ORIGINS,
                ['https://trackme.signtechlimited.com', 'https://app.example.com'],
            )
            self.assertTrue(reloaded.SECURE_SSL_REDIRECT)
            self.assertTrue(reloaded.SESSION_COOKIE_SECURE)
            self.assertTrue(reloaded.CSRF_COOKIE_SECURE)
            self.assertEqual(reloaded.SECURE_HSTS_SECONDS, 31536000)
            self.assertTrue(reloaded.SECURE_HSTS_INCLUDE_SUBDOMAINS)
            self.assertTrue(reloaded.SECURE_HSTS_PRELOAD)
            self.assertEqual(reloaded.SECURE_PROXY_SSL_HEADER, ('HTTP_X_FORWARDED_PROTO', 'https'))
        finally:
            # Restore development settings module
            os.environ['DEBUG'] = 'True'
            os.environ['DJANGO_SECRET_KEY'] = 'dev-secret-key-placeholder-32-characters-long'
            os.environ['ALLOWED_HOSTS'] = 'localhost,127.0.0.1'
            importlib.reload(fieldtrack_settings)
