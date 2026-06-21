import sys
import os

# Add project root to the path
sys.path.insert(0, os.path.dirname(__file__))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings.dev')

from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
