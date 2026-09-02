import os
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

def _parse_comma_separated(value, default=None):
    if value is None:
        return list(default) if default else []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(',') if item.strip()]
    return list(default) if default else []


# ── ENVIRONMENT & RUNTIME FLAGS ──────────────────────────────────────────────

DEBUG = True

SECRET_KEY = 'django-insecure-development-only-key-fieldtrack-attendance-2026'

# Hosts Configuration
_dev_allowed_hosts = ['localhost', '127.0.0.1', 'testserver', '[::1]', '*']
ALLOWED_HOSTS = ['*']

# CSRF Trusted Origins
CSRF_TRUSTED_ORIGINS = ['http://localhost:8000', 'http://127.0.0.1:8000', 'http://127.0.0.1', 'http://localhost']


# ── SECURITY HARDENING & HEADERS ─────────────────────────────────────────────

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = 'Lax'
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
SECURE_REFERRER_POLICY = 'same-origin'

SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_HSTS_SECONDS = 0
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

_sqlite_path = (BASE_DIR / 'db.sqlite3').resolve()
_sqlite_timeout = 5.0

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

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
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

EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_USE_SSL = False
EMAIL_HOST_USER = ''
EMAIL_HOST_PASSWORD = ''
DEFAULT_FROM_EMAIL = 'noreply@fieldtrack.com'


# ── DJANGO COTTON & MULTI-TENANCY ────────────────────────────────────────────

COTTON_SNAKE_CASED_NAMES = False

TENANCY_ENABLED = True
TENANT_UI_ENABLED = False
DEFAULT_TENANT_SLUG = 'signtech'
