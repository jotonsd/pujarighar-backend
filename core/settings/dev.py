from .base import *

DEBUG = config('DEBUG', default=True, cast=bool)

ALLOWED_HOSTS = ['*']

DATABASES = {
    'default': {
        'ENGINE':   'django.db.backends.mysql',
        'NAME':     config('DB_NAME',     default='pujarighar'),
        'USER':     config('DB_USER',     default='root'),
        'PASSWORD': config('DB_PASSWORD', default=''),
        'HOST':     config('DB_HOST',     default='localhost'),
        'PORT':     config('DB_PORT',     default='3306'),
        'OPTIONS': {
            'charset': 'utf8mb4',
        },
    }
}

CORS_ALLOWED_ORIGINS = ['http://localhost:3000', 'https://dev.pujarighar.com/']
CORS_ALLOW_CREDENTIALS = True
