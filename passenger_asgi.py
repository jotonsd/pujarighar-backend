import sys
import os

# Add project root to the path
sys.path.insert(0, os.path.dirname(__file__))

from decouple import config

SETTINGS_MODULE = 'core.settings.prod' if config('ENVIRONMENT', default='development') == 'production' else 'core.settings.dev'

os.environ.setdefault('DJANGO_SETTINGS_MODULE', SETTINGS_MODULE)

# Passenger's ASGI entry point — unlike passenger_wsgi.py, this serves the
# full ProtocolTypeRouter from core/asgi.py (HTTP + WebSocket), which is
# what the Log Viewer's live tail and log-list sockets (/ws/logs-index/,
# /ws/logs-tail/) actually need. Plain WSGI has no WebSocket support at
# all — that's why both silently fail in production while passenger_wsgi.py
# is the active entry point.
from core.asgi import application  # noqa: E402,F401
