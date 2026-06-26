from pathlib import Path
from decouple import config
from datetime import timedelta

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = config('SECRET_KEY', default='local-dev-secret-key')

# ─── Environment ───────────────────────────────────────────────────────────────
# 'development' or 'production' — set explicitly per-server in .env
ENVIRONMENT = config('ENVIRONMENT', default='development')
IS_PRODUCTION = ENVIRONMENT == 'production'

# ─── Maintenance mode ──────────────────────────────────────────────────────────
MAINTENANCE_MODE = config('MAINTENANCE_MODE', default=False, cast=bool)

# ─── Database engine ───────────────────────────────────────────────────────────
# DB_ENGINE='mysql' (default) or 'postgresql', set per-environment in .env
_DB_ENGINES = {
    'mysql': {
        'ENGINE': 'django.db.backends.mysql',
        'PORT_DEFAULT': '3306',
        'OPTIONS': {
            'charset': 'utf8mb4',
            'init_command': "SET sql_mode='STRICT_TRANS_TABLES'",
        },
    },
    'postgresql': {
        'ENGINE': 'django.db.backends.postgresql',
        'PORT_DEFAULT': '5432',
    },
}


def build_database_config(defaults=None):
    """Build DATABASES['default'] from DB_ENGINE/DB_* env vars.

    `defaults` supplies fallback values for dev; omit a key (e.g. in prod)
    to make that env var required and fail loudly if it's missing.
    """
    defaults = defaults or {}
    engine_key = config('DB_ENGINE', default='mysql').lower()
    if engine_key not in _DB_ENGINES:
        raise ValueError(f"Unsupported DB_ENGINE '{engine_key}', expected 'mysql' or 'postgresql'")
    engine = _DB_ENGINES[engine_key]

    def field(env_key, default_key):
        return config(env_key, default=defaults[default_key]) if default_key in defaults else config(env_key)

    db_config = {
        'ENGINE':   engine['ENGINE'],
        'NAME':     field('DB_NAME', 'NAME'),
        'USER':     field('DB_USER', 'USER'),
        'PASSWORD': field('DB_PASSWORD', 'PASSWORD'),
        'HOST':     config('DB_HOST', default=defaults.get('HOST', 'localhost')),
        'PORT':     config('DB_PORT', default=engine['PORT_DEFAULT']),
    }
    if 'OPTIONS' in engine:
        db_config['OPTIONS'] = engine['OPTIONS']
    return db_config

INSTALLED_APPS = [
    'django.contrib.contenttypes',
    'django.contrib.auth',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Third-party
    'rest_framework',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    'corsheaders',

    # Project app
    'api',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'api.middleware.MaintenanceModeMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'core.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'core.wsgi.application'

AUTH_USER_MODEL = 'api.User'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = config('LANGUAGE_CODE', default='bn')
TIME_ZONE = 'Asia/Dhaka'
USE_I18N = True
USE_TZ = True

STATIC_URL  = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_URL   = '/media/'
MEDIA_ROOT  = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'EXCEPTION_HANDLER': 'api.utils.response.custom_exception_handler',
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME':  timedelta(hours=1),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS':  True,
    'BLACKLIST_AFTER_ROTATION': True,
    'AUTH_HEADER_TYPES': ('Bearer',),
}

LANGUAGES = [('bn', 'বাংলা'), ('en', 'English')]

# ─── URLs ─────────────────────────────────────────────────────────────────────
BACKEND_URL  = config('BACKEND_URL',  default='http://localhost:8000')
FRONTEND_URL = config('FRONTEND_URL', default='http://localhost:3000')

# ─── Password reset ────────────────────────────────────────────────────────────
PASSWORD_RESET_TIMEOUT = 1800  # 30 minutes

# ─── Google OAuth ─────────────────────────────────────────────────────────────
GOOGLE_CLIENT_ID = config('GOOGLE_CLIENT_ID', default='')

# ─── SSLCommerz ───────────────────────────────────────────────────────────────
SSLCOMMERZ_STORE_ID      = config('SSLCOMMERZ_STORE_ID',      default='')
SSLCOMMERZ_STORE_PASS    = config('SSLCOMMERZ_STORE_PASS',    default='')
SSLCOMMERZ_API_URL       = config('SSLCOMMERZ_API_URL',       default='https://sandbox.sslcommerz.com/gwprocess/v4/api.php')
SSLCOMMERZ_VALIDATION_URL = config('SSLCOMMERZ_VALIDATION_URL', default='https://sandbox.sslcommerz.com/validator/api/validationserverAPI.php')

# ─── Logging ───────────────────────────────────────────────────────────────────
# Rotates daily at midnight, keeping 7 days of history. Email-sending logs go to
# their own file (mail.log) so delivery issues are easy to find without digging
# through general app noise.
LOG_DIR = BASE_DIR / 'logs'
LOG_DIR.mkdir(exist_ok=True)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            '()': 'api.utils.logging_formatters.RelativePathFormatter',
            'format': '{asctime} {levelname} {name} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
        'app_file': {
            'class': 'logging.handlers.TimedRotatingFileHandler',
            'filename': LOG_DIR / 'app.log',
            'when': 'midnight',
            'backupCount': 7,
            'encoding': 'utf-8',
            'formatter': 'verbose',
        },
        'mail_file': {
            'class': 'logging.handlers.TimedRotatingFileHandler',
            'filename': LOG_DIR / 'mail.log',
            'when': 'midnight',
            'backupCount': 7,
            'encoding': 'utf-8',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console', 'app_file'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console', 'app_file'],
            'level': 'INFO',
            'propagate': False,
        },
        'api.services.mail_service': {
            'handlers': ['console', 'mail_file'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}
