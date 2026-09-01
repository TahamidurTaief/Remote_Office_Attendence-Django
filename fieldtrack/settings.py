import os
from pathlib import Path
import environ
from django.core.exceptions import ImproperlyConfigured

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False)
)
_env_file_path = os.environ.get('DJANGO_ENV_FILE', str(BASE_DIR / '.env'))
if os.path.exists(_env_file_path) and not os.environ.get('DJANGO_IGNORE_ENV_FILE'):
    environ.Env.read_env(_env_file_path, overwrite=False)



def _parse_comma_separated(value, default=None):
    if value is None:
        return list(default) if default else []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(',') if item.strip()]
    return list(default) if default else []


# ── ENVIRONMENT & RUNTIME FLAGS ──────────────────────────────────────────────

DEBUG = env.bool('DEBUG', default=False)

_INSECURE_DEV_SECRET = 'django-insecure-development-only-key-fieldtrack-attendance-2026'
_raw_secret_key = env.str('DJANGO_SECRET_KEY', default=env.str('SECRET_KEY', default=''))

if not DEBUG:
    if (
        not _raw_secret_key
        or _raw_secret_key.startswith('django-insecure')
        or 'change_me' in _raw_secret_key.lower()
        or len(_raw_secret_key) < 32
    ):
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY must be set to a secure, random string (min 32 characters) "
            "when running in production mode (DEBUG=False)."
        )
    SECRET_KEY = _raw_secret_key
else:
    SECRET_KEY = _raw_secret_key if _raw_secret_key else _INSECURE_DEV_SECRET

# Hosts Configuration
_dev_allowed_hosts = ['localhost', '127.0.0.1', 'testserver', '[::1]']
_raw_allowed_hosts = env.str('ALLOWED_HOSTS', default=','.join(_dev_allowed_hosts) if DEBUG else '')
ALLOWED_HOSTS = _parse_comma_separated(_raw_allowed_hosts, default=_dev_allowed_hosts if DEBUG else [])

if not DEBUG and not ALLOWED_HOSTS:
    raise ImproperlyConfigured(
        "ALLOWED_HOSTS must be configured as a comma-separated list of domain names "
        "when running in production mode (DEBUG=False)."
    )

# CSRF Trusted Origins
_dev_csrf_origins = ['http://localhost:8000', 'http://127.0.0.1:8000']
_raw_csrf_origins = env.str('CSRF_TRUSTED_ORIGINS', default=','.join(_dev_csrf_origins) if DEBUG else '')
CSRF_TRUSTED_ORIGINS = _parse_comma_separated(_raw_csrf_origins, default=_dev_csrf_origins if DEBUG else [])


# ── SECURITY HARDENING & HEADERS ─────────────────────────────────────────────

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = 'Lax'
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = env.str('X_FRAME_OPTIONS', default='DENY')
SECURE_REFERRER_POLICY = env.str('SECURE_REFERRER_POLICY', default='same-origin')

if not DEBUG:
    SECURE_SSL_REDIRECT = env.bool('SECURE_SSL_REDIRECT', default=True)
    SESSION_COOKIE_SECURE = env.bool('SESSION_COOKIE_SECURE', default=True)
    CSRF_COOKIE_SECURE = env.bool('CSRF_COOKIE_SECURE', default=True)
    SECURE_HSTS_SECONDS = env.int('SECURE_HSTS_SECONDS', default=31536000)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool('SECURE_HSTS_INCLUDE_SUBDOMAINS', default=True)
    SECURE_HSTS_PRELOAD = env.bool('SECURE_HSTS_PRELOAD', default=True)
    if env.bool('SECURE_PROXY_SSL_HEADER_ENABLED', default=True):
        SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
else:
    SECURE_SSL_REDIRECT = env.bool('SECURE_SSL_REDIRECT', default=False)
    SESSION_COOKIE_SECURE = env.bool('SESSION_COOKIE_SECURE', default=False)
    CSRF_COOKIE_SECURE = env.bool('CSRF_COOKIE_SECURE', default=False)
    SECURE_HSTS_SECONDS = env.int('SECURE_HSTS_SECONDS', default=0)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_HSTS_PRELOAD = False


# ── APPLICATION DEFINITION ───────────────────────────────────────────────────

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    # Third-party apps
    'django_tailwind_cli',
    'imagekit',
    'django_cotton',
    'django_browser_reload',
    
    # Local apps
    'apps.tenants',
    'apps.accounts',
    'apps.audit',
    'apps.employees',
    'apps.attendance',
    'apps.branches',
    'apps.staff',
    'apps.admin_panel',
    'apps.notifications',
    'apps.backups',
    'apps.leave',
    'apps.projects',
    'apps.schedule',
    'apps.expense',
    'apps.workflow',
    'apps.payroll',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'apps.tenants.middleware.TenantMiddleware',
    'apps.audit.middleware.AuditRequestContextMiddleware',
    'apps.accounts.middleware.SuspendedEmployeeMiddleware',
    'apps.accounts.middleware.SessionDeviceMiddleware',
    'apps.accounts.middleware.MFARequiredMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

if DEBUG:
    MIDDLEWARE.insert(3, 'django_browser_reload.middleware.BrowserReloadMiddleware')


ROOT_URLCONF = 'fieldtrack.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': False,
        'OPTIONS': {
            'loaders': [
                'django_cotton.cotton_loader.Loader',
                'django.template.loaders.filesystem.Loader',
                'django.template.loaders.app_directories.Loader',
            ],
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'apps.accounts.context_processors.notifications',
                'apps.audit.context_processors.audit_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'fieldtrack.wsgi.application'


# ── DATABASE CONFIGURATION (SQLITE RUNTIME LOCK) ──────────────────────────────
# Architecture Rule: SQLite is permanently locked as the active database engine
# for this delivery phase. Any injected DATABASE_URL is ignored to prevent
# accidental engine switching or runtime desynchronization.
#
# FUTURE POSTGRESQL MIGRATION GATE (INACTIVE):
# A future transition to PostgreSQL must follow the strict 4-stage gate:
#   1. Backup: Full offline snapshot of production SQLite data & media.
#   2. Staging Restore: Restore snapshot into staging PostgreSQL environment.
#   3. Consistency Validation: Run data integrity & end-to-end regression tests.
#   4. Production Rollout: Update engine setting, run verified migrations, and deploy.

_raw_sqlite_path = env.str('SQLITE_PATH', default='')
_sqlite_path = Path(_raw_sqlite_path) if _raw_sqlite_path else BASE_DIR / 'db.sqlite3'

_DEFAULT_SQLITE_TIMEOUT = 5.0
try:
    _sqlite_timeout = float(env.str('SQLITE_TIMEOUT', default=str(_DEFAULT_SQLITE_TIMEOUT)))
    if _sqlite_timeout <= 0:
        _sqlite_timeout = _DEFAULT_SQLITE_TIMEOUT
except (ValueError, TypeError):
    _sqlite_timeout = _DEFAULT_SQLITE_TIMEOUT

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': _sqlite_path,
        'OPTIONS': {
            'timeout': _sqlite_timeout,
        }
    }
}



# ── PASSWORD VALIDATION ──────────────────────────────────────────────────────

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# ── INTERNATIONALIZATION ─────────────────────────────────────────────────────

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Dhaka'  # Bangladesh Standard Time (UTC+6)
USE_I18N = True
USE_TZ = True

# Global Date and Datetime formats (d/m/y)
USE_L10N = False
DATE_FORMAT = 'd/m/Y'
SHORT_DATE_FORMAT = 'd/m/Y'
DATETIME_FORMAT = 'd/m/Y g:i A'
SHORT_DATETIME_FORMAT = 'd/m/Y g:i A'

APPEND_SLASH = True


# ── STATIC & MEDIA STORAGE ───────────────────────────────────────────────────

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

if DEBUG:
    STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }
else:
    STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage",
        },
    }

MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# ── AUTHENTICATION & SESSIONS ────────────────────────────────────────────────

LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'
SESSION_COOKIE_AGE = 86400  # 1 day

AUTH_USER_MODEL = 'accounts.CustomUser'

AUTHENTICATION_BACKENDS = [
    'apps.accounts.backends.PhoneOrEmailBackend',
    'django.contrib.auth.backends.ModelBackend',
]


# ── TAILWIND & ASSET COMPILATION ─────────────────────────────────────────────

_cli_bin = 'tailwindcss-3.4.13.exe' if os.name == 'nt' else 'tailwindcss-3.4.13'
TAILWIND_CLI_PATH = BASE_DIR / '.django_tailwind_cli' / _cli_bin
TAILWIND_CLI_SRC_CSS = 'static/css/source.css'
TAILWIND_CLI_DIST_CSS = 'css/dist/styles.css'


# ── IMAGE PROCESSING & WORKING SCHEDULE ──────────────────────────────────────

IMAGEKIT_CACHEFILE_DIR = 'cache'
IMAGEKIT_HASH_FILENAMES = True
IMAGEKIT_CACHEFILE_NAMER = 'imagekit.cachefiles.namers.hash'

# Working days (Saturday to Thursday, Friday = 4 is excluded by default)
WORKING_DAYS = [0, 1, 2, 3, 5, 6]


# ── EMAIL CONFIGURATION ──────────────────────────────────────────────────────

EMAIL_BACKEND = env.str(
    'EMAIL_BACKEND',
    default='django.core.mail.backends.console.EmailBackend' if DEBUG else 'django.core.mail.backends.smtp.EmailBackend'
)
EMAIL_HOST = env.str('EMAIL_HOST', default='smtp.gmail.com')
EMAIL_PORT = env.int('EMAIL_PORT', default=587)
EMAIL_USE_TLS = env.bool('EMAIL_USE_TLS', default=True)
EMAIL_USE_SSL = env.bool('EMAIL_USE_SSL', default=False)
EMAIL_HOST_USER = env.str('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = env.str('EMAIL_HOST_PASSWORD', default='')
DEFAULT_FROM_EMAIL = env.str('DEFAULT_FROM_EMAIL', default='noreply@fieldtrack.com')


# ── DJANGO COTTON & MULTI-TENANCY ────────────────────────────────────────────

COTTON_SNAKE_CASED_NAMES = False

# Multi-tenancy foundation enabled; tenant UI hidden by default for Signtech single-tenant deployment
TENANCY_ENABLED = env.bool('TENANCY_ENABLED', default=True)
TENANT_UI_ENABLED = env.bool('TENANT_UI_ENABLED', default=False)
DEFAULT_TENANT_SLUG = env.str('DEFAULT_TENANT_SLUG', default='signtech')
