import os

from decouple import config
from django.core.asgi import get_asgi_application

SETTINGS_MODULE = 'core.settings.prod' if config('ENVIRONMENT', default='development') == 'production' else 'core.settings.dev'

os.environ.setdefault('DJANGO_SETTINGS_MODULE', SETTINGS_MODULE)

# django.setup() (triggered by get_asgi_application) must run before anything
# below imports models/consumers, otherwise Django raises AppRegistryNotReady.
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402

from api.routing import websocket_urlpatterns  # noqa: E402
from api.ws_auth import JWTAuthMiddleware  # noqa: E402

application = ProtocolTypeRouter({
    'http': django_asgi_app,
    'websocket': JWTAuthMiddleware(URLRouter(websocket_urlpatterns)),
})
