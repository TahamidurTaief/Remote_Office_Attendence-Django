import os
from pathlib import Path
import environ

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False)
)
_env_file = BASE_DIR / '.env'
if _env_file.exists():
    environ.Env.read_env(str(_env_file))

# SECURITY WARNING: keep the secret key used in production secret!
# Production must set the DJANGO_SECRET_KEY environment variable.
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'django-insecure-placehojkbkj-ssdfsadflder')

# SECURITY WARNING: don't run with debug turned on in production!
# For Coolify/production deployment: set DEBUG=True in environment variables or .env for local/staging; default is False for production.
DEBUG = env.bool('DEBUG', default=False)

ALLOWED_HOSTS = ['demotrackme.signtechlimited.com', 'trackme.signtechlimited.com', 'localhost', '127.0.0.1', 'testserver', '192.168.10.191']

CSRF_TRUSTED_ORIGINS = ["http://localhost:8000", "http://127.0.0.1:8000", "https://demotrackme.signtechlimited.com", "https://trackme.signtechlimited.com"]

# Security hardening defaults
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = 'Lax'
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'

# Production security hardening (skipped in local DEBUG mode so `runserver`
# over plain http still works during development).
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

# Application definition
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

# Database
# https://docs.djangoproject.com/en/5.0/ref/settings/#databases

# Intentionally SQLite — see WAL/busy_timeout config below. Do not migrate to Postgres/MySQL without explicit sign-off.
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
        'OPTIONS': {
            'timeout': 5.0,
        }
    }
}

# Password validation
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

# Internationalization
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

# URL Configuration
APPEND_SLASH = True

# Static files (CSS, JavaScript, Images)
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

# Media files
MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Authentication URLs
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'
SESSION_COOKIE_AGE = 86400  # 1 day

# Tailwind CLI Configuration
_cli_bin = 'tailwindcss-3.4.13.exe' if os.name == 'nt' else 'tailwindcss-3.4.13'
TAILWIND_CLI_PATH = BASE_DIR / '.django_tailwind_cli' / _cli_bin
TAILWIND_CLI_SRC_CSS = 'static/css/source.css'
TAILWIND_CLI_DIST_CSS = 'css/dist/styles.css'

# Custom user model
AUTH_USER_MODEL = 'accounts.CustomUser'

AUTHENTICATION_BACKENDS = [
    'apps.accounts.backends.PhoneOrEmailBackend',
    'django.contrib.auth.backends.ModelBackend',
]

# ImageKit settings
IMAGEKIT_CACHEFILE_DIR = 'cache'
IMAGEKIT_HASH_FILENAMES = True
IMAGEKIT_CACHEFILE_NAMER = 'imagekit.cachefiles.namers.hash'

# Working days (Saturday to Thursday, Friday = 4 is excluded by default)
WORKING_DAYS = [0, 1, 2, 3, 5, 6]


# Email Settings
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend' if DEBUG else 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = ''
EMAIL_HOST_PASSWORD = ''
DEFAULT_FROM_EMAIL = 'noreply@fieldtrack.com'

# Django Cotton configuration (preserves hyphenated component filenames)
COTTON_SNAKE_CASED_NAMES = False

# Multi-tenancy configurations
TENANCY_ENABLED = True
DEFAULT_TENANT_SLUG = 'signtech'


