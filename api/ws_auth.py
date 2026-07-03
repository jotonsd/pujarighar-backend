from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken


@database_sync_to_async
def _get_user(token: str):
    from api.models import User
    try:
        validated = AccessToken(token)
        return User.objects.get(id=validated['user_id'])
    except (TokenError, User.DoesNotExist, KeyError):
        return AnonymousUser()


class JWTAuthMiddleware(BaseMiddleware):
    """Authenticates WebSocket connections via `?token=<JWT access token>`,
    since browsers can't set an Authorization header on the WS handshake.
    """

    async def __call__(self, scope, receive, send):
        query = parse_qs(scope['query_string'].decode())
        token = query.get('token', [None])[0]
        scope['user'] = await _get_user(token) if token else AnonymousUser()
        return await super().__call__(scope, receive, send)
