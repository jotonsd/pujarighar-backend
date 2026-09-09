import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny

from api.models import DeviceToken
from api.utils.response import ApiResponse

logger = logging.getLogger(__name__)


@api_view(['POST'])
@permission_classes([AllowAny])
def register_device_token(request):
    """Called at every app launch, logged in or not — a promotional
    broadcast needs to reach every install, not just accounts that happen
    to be signed in, so registration can't be gated on auth. When the
    request IS authenticated, the token gets attached to that user (so
    order-status pushes reach it too); a guest's token is stored with no
    user attached and only ever receives promotional pushes."""
    token = request.data.get('token')
    if not token:
        return ApiResponse(message="token is required", errors="token is required", status_code=422)
    platform = request.data.get('platform', 'android')
    user = request.user if request.user and request.user.is_authenticated else None
    # Keyed on the token itself (globally unique), not (user, token) — a
    # device re-registering under a different account (or logging out)
    # should move/detach, not duplicate.
    DeviceToken.objects.update_or_create(
        token=token, defaults={'user': user, 'platform': platform},
    )
    return ApiResponse(message="Device registered")


@api_view(['POST'])
@permission_classes([AllowAny])
def unregister_device_token(request):
    """Called on logout — detaches the device from the account (so
    per-user pushes like order-status updates stop reaching it) without
    deleting the row, since the device itself should keep receiving
    promotional broadcasts regardless of who's signed in on it."""
    token = request.data.get('token')
    if token:
        DeviceToken.objects.filter(token=token).update(user=None)
    return ApiResponse(message="Device detached")
