import os
from pathlib import Path
import environ

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Initialize environment variables
env = environ.Env(
    DEBUG=(bool, False)
)
environ.Env.read_env(os.path.join(BASE_DIR, '.env'))

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = env('SECRET_KEY', default='django-insecure-placehojkbkj-ssdfsadflder')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env('DEBUG')

ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=[])

CSRF_TRUSTED_ORIGINS = ["http://localhost:8000", "http://127.0.0.1:8000", "https://trackme.signtechlimited.com"]

# Production security hardening (skipped in local DEBUG mode so `runserver`
# over plain http still works during development).
if not DEBUG:
    # Trust the reverse proxy's X-Forwarded-Proto header (Coolify/nginx/Docker
    # setups terminate SSL in front of Django) so Django knows the original
    # request was HTTPS and doesn't create a redirect loop.
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    X_FRAME_OPTIONS = 'DENY'

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    # Third-party apps
    'tailwind',
    'theme',
    'imagekit',
    
    # Local apps
    'apps.accounts',
    'apps.employees',
    'apps.attendance',
    'apps.branches',
    'apps.staff',
    'apps.admin_panel',
    'apps.notifications',
    'apps.backups',
    'apps.leave',
    'apps.projects',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'fieldtrack.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'apps.accounts.context_processors.notifications',
            ],
        },
    },
]

WSGI_APPLICATION = 'fieldtrack.wsgi.application'

# Database
# https://docs.djangoproject.com/en/5.0/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
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

# Tailwind config (if using django-tailwind)
TAILWIND_APP_NAME = 'theme'

import platform
if platform.system() == 'Windows':
    # On Windows, use npm.cmd to bypass PowerShell script execution restrictions
    NPM_BIN_PATH = r"C:\Program Files\nodejs\npm.cmd"

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

