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
            'init_command': "SET sql_mode='STRICT_TRANS_TABLES'",
        },
    }
}

# DATABASES = {
#     'default': {
#         'ENGINE':   'django.db.backends.postgresql',
#         'NAME':     config('DB_NAME',     default='pujarighar'),
#         'USER':     config('DB_USER',     default='postgres'),
#         'PASSWORD': config('DB_PASSWORD', default='postgres'),
#         'HOST':     config('DB_HOST',     default='localhost'),
#         'PORT':     config('DB_PORT',     default='5432'),
#     }
# }

CORS_ALLOWED_ORIGINS = [
    origin.strip().rstrip('/')
    for origin in config('CORS_ALLOWED_ORIGINS', default='http://localhost:3000').split(',')
    if origin.strip()
]
CORS_ALLOW_CREDENTIALS = True
