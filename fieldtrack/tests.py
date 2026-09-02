import importlib
import os
import tempfile
from pathlib import Path
from unittest import mock
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase
from django.conf import settings


class SettingsSecurityTests(SimpleTestCase):
    """Focused tests for environment parsing, security defaults, SQLite path resolution, and test isolation."""

    def setUp(self):
        super().setUp()
        # Take an exact snapshot of os.environ prior to any test mutation
        self._orig_environ = os.environ.copy()

    def tearDown(self):
        # Guarantee exact original environment and settings module state are restored
        try:
            os.environ.clear()
            os.environ.update(self._orig_environ)
        finally:
            try:
                from fieldtrack import settings as fieldtrack_settings
                importlib.reload(fieldtrack_settings)
            finally:
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
            'SQLITE_PATH': '',
        },
        clear=True,
    )
    def test_sqlite_empty_path_resolves_to_base_dir_default(self):
        """Verify empty SQLITE_PATH resolves deterministically to BASE_DIR / 'db.sqlite3'."""
        from fieldtrack import settings as fieldtrack_settings
        reloaded = importlib.reload(fieldtrack_settings)
        expected = (fieldtrack_settings.BASE_DIR / 'db.sqlite3').resolve()
        self.assertEqual(reloaded.DATABASES['default']['NAME'], expected)

    @mock.patch.dict(
        os.environ,
        {
            'DJANGO_IGNORE_ENV_FILE': '1',
            'DEBUG': 'True',
            'SQLITE_PATH': 'custom_storage/test_custom.sqlite3',
        },
        clear=True,
    )
    def test_sqlite_relative_path_resolves_under_base_dir(self):
        """Verify relative SQLITE_PATH resolves deterministically underneath BASE_DIR."""
        from fieldtrack import settings as fieldtrack_settings
        reloaded = importlib.reload(fieldtrack_settings)
        expected = (fieldtrack_settings.BASE_DIR / 'custom_storage' / 'test_custom.sqlite3').resolve()
        self.assertEqual(reloaded.DATABASES['default']['NAME'], expected)

    def test_sqlite_absolute_path_preserved_without_base_dir_prefix(self):
        """Verify absolute SQLITE_PATH is preserved as an absolute path."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            abs_target = (Path(tmp_dir) / 'runtime_storage' / 'external.sqlite3').resolve()
            with mock.patch.dict(
                os.environ,
                {
                    'DJANGO_IGNORE_ENV_FILE': '1',
                    'DEBUG': 'True',
                    'SQLITE_PATH': str(abs_target),
                },
                clear=True,
            ):
                from fieldtrack import settings as fieldtrack_settings
                reloaded = importlib.reload(fieldtrack_settings)
                self.assertEqual(reloaded.DATABASES['default']['NAME'], abs_target)
                self.assertTrue(reloaded.DATABASES['default']['NAME'].is_absolute())

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

    def test_environment_values_restored_exactly_after_mutation(self):
        """Verify environment values are restored exactly and settings cleanly reloaded after mutation."""
        snapshot_before = os.environ.copy()

        # Mutate environment with multiple variables (add, modify)
        os.environ['TEST_ISOLATION_SENTINEL_VAR'] = 'temporary_val'
        os.environ['DEBUG'] = 'False'

        # Invoke tearDown directly
        self.tearDown()

        # Verify exact environment equality
        self.assertEqual(os.environ, snapshot_before)
        self.assertNotIn('TEST_ISOLATION_SENTINEL_VAR', os.environ)
        self.assertEqual(os.environ.get('DEBUG'), snapshot_before.get('DEBUG'))

    def test_environment_and_settings_restored_even_on_reload_failure(self):
        """Verify original environment is restored even when settings reload fails."""
        snapshot_before = os.environ.copy()

        # Inject invalid configuration that triggers ImproperlyConfigured
        os.environ['DEBUG'] = 'False'
        os.environ['DJANGO_SECRET_KEY'] = 'insecure'
        from fieldtrack import settings as fieldtrack_settings
        with self.assertRaises(ImproperlyConfigured):
            importlib.reload(fieldtrack_settings)

        # Invoke tearDown
        self.tearDown()

        # Verify exact environment equality
        self.assertEqual(os.environ, snapshot_before)
        # Verify settings module can now be reloaded without error
        reloaded = importlib.reload(fieldtrack_settings)
        self.assertIsNotNone(reloaded)
