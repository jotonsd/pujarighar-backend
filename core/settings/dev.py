from .base import *

DEBUG = config('DEBUG', default=True, cast=bool)

ALLOWED_HOSTS = ['*']

DATABASES = {
    'default': build_database_config(defaults={
        'NAME':     'pujarighar',
        'USER':     'root',
        'PASSWORD': '',
        'HOST':     'localhost',
    })
}

CORS_ALLOWED_ORIGINS = [
    origin.strip().rstrip('/')
    for origin in config('CORS_ALLOWED_ORIGINS', default='http://localhost:3000').split(',')
    if origin.strip()
]
CORS_ALLOW_CREDENTIALS = True
