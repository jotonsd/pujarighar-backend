import os
from decouple import config
from django.core.wsgi import get_wsgi_application

SETTINGS_MODULE = 'core.settings.prod' if config('ENVIRONMENT', default='development') == 'production' else 'core.settings.dev'

os.environ.setdefault('DJANGO_SETTINGS_MODULE', SETTINGS_MODULE)
application = get_wsgi_application()
