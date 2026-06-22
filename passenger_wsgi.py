import sys
import os

# Add project root to the path
sys.path.insert(0, os.path.dirname(__file__))

from decouple import config

SETTINGS_MODULE = 'core.settings.prod' if config('ENVIRONMENT', default='development') == 'production' else 'core.settings.dev'

os.environ.setdefault('DJANGO_SETTINGS_MODULE', SETTINGS_MODULE)

from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
