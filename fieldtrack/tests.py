import importlib
import os
from pathlib import Path
from unittest import mock
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase
from django.conf import settings


class SettingsSecurityTests(SimpleTestCase):
    """Focused tests for environment parsing, security defaults, and production guardrails."""

    def tearDown(self):
        # Guarantee development settings state is cleanly restored after every test
        from fieldtrack import settings as fieldtrack_settings
        os.environ['DJANGO_IGNORE_ENV_FILE'] = '1'
        os.environ['DEBUG'] = 'True'
        os.environ['DJANGO_SECRET_KEY'] = 'dev-secret-key-placeholder-32-characters-long'
        os.environ['ALLOWED_HOSTS'] = 'localhost,127.0.0.1'
        os.environ.pop('SQLITE_PATH', None)
        os.environ.pop('SQLITE_TIMEOUT', None)
        os.environ.pop('DATABASE_URL', None)
        importlib.reload(fieldtrack_settings)
        super().tearDown()

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

    @mock.patch.dict(
        os.environ,
        {
            'DJANGO_IGNORE_ENV_FILE': '1',
            'DEBUG': 'True',
            'DATABASE_URL': 'postgres://postgres_user:secret_pass@127.0.0.1:5432/postgres_db',
        },
        clear=True,
    )
    def test_injected_database_url_cannot_switch_engine(self):
        """Verify injected DATABASE_URL is ignored and engine remains locked to SQLite."""
        from fieldtrack import settings as fieldtrack_settings
        reloaded = importlib.reload(fieldtrack_settings)
        self.assertEqual(reloaded.DATABASES['default']['ENGINE'], 'django.db.backends.sqlite3')

    @mock.patch.dict(
        os.environ,
        {
            'DJANGO_IGNORE_ENV_FILE': '1',
            'DEBUG': 'True',
            'SQLITE_PATH': 'custom_storage/test_custom.sqlite3',
        },
        clear=True,
    )
    def test_sqlite_custom_path_parsing(self):
        """Verify SQLITE_PATH environment override parses safely into Path."""
        from fieldtrack import settings as fieldtrack_settings
        reloaded = importlib.reload(fieldtrack_settings)
        self.assertEqual(reloaded.DATABASES['default']['NAME'], Path('custom_storage/test_custom.sqlite3'))

    @mock.patch.dict(
        os.environ,
        {
            'DJANGO_IGNORE_ENV_FILE': '1',
            'DEBUG': 'True',
            'SQLITE_TIMEOUT': '12.5',
        },
        clear=True,
    )
    def test_sqlite_custom_timeout_parsing(self):
        """Verify SQLITE_TIMEOUT environment override parses as a valid float."""
        from fieldtrack import settings as fieldtrack_settings
        reloaded = importlib.reload(fieldtrack_settings)
        self.assertEqual(reloaded.DATABASES['default']['OPTIONS']['timeout'], 12.5)

    @mock.patch.dict(
        os.environ,
        {
            'DJANGO_IGNORE_ENV_FILE': '1',
            'DEBUG': 'True',
            'SQLITE_TIMEOUT': '-10.0',
        },
        clear=True,
    )
    def test_sqlite_negative_timeout_falls_back_to_safe_default(self):
        """Verify non-positive SQLITE_TIMEOUT safely falls back to default 5.0."""
        from fieldtrack import settings as fieldtrack_settings
        reloaded = importlib.reload(fieldtrack_settings)
        self.assertEqual(reloaded.DATABASES['default']['OPTIONS']['timeout'], 5.0)

    @mock.patch.dict(
        os.environ,
        {
            'DJANGO_IGNORE_ENV_FILE': '1',
            'DEBUG': 'True',
            'SQLITE_TIMEOUT': 'invalid_non_numeric',
        },
        clear=True,
    )
    def test_sqlite_invalid_timeout_falls_back_to_safe_default(self):
        """Verify malformed SQLITE_TIMEOUT safely falls back to default 5.0."""
        from fieldtrack import settings as fieldtrack_settings
        reloaded = importlib.reload(fieldtrack_settings)
        self.assertEqual(reloaded.DATABASES['default']['OPTIONS']['timeout'], 5.0)
