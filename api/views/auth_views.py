import logging
import requests
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework_simplejwt.views import TokenRefreshView

from api.models import SalesOrder, User
from api.serializers.auth_serializers import RegisterSerializer, LoginSerializer
from api.serializers.user_serializers import UserSerializer
from api.services.auth_service import AuthService
from api.utils.response import ApiResponse

logger = logging.getLogger(__name__)
_auth = AuthService()


@api_view(['POST'])
@permission_classes([AllowAny])
def register(request):
    serializer = RegisterSerializer(data=request.data)
    if not serializer.is_valid():
        return ApiResponse(
            message="Validation failed",
            errors=serializer.errors,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    try:
        user   = serializer.save()
        tokens = _auth.register(user)
        # Link any guest orders placed with this phone number
        _link_guest_orders(user)
        return ApiResponse(
            message="Registration successful",
            data={'user': UserSerializer(user).data, **tokens},
            status_code=status.HTTP_201_CREATED,
        )
    except Exception as e:
        logger.error(f"Register error: {e}", exc_info=True)
        return ApiResponse(message=str(e), errors=str(e), status_code=status.HTTP_400_BAD_REQUEST)


def _link_guest_orders(user) -> None:
    linked = SalesOrder.objects.filter(is_guest=True, shipping_phone=user.phone, customer__isnull=True)
    count  = linked.update(customer=user, is_guest=False)
    if count:
        logger.info(f"Linked {count} guest orders to user {user.email}")


@api_view(['POST'])
@permission_classes([AllowAny])
def login(request):
    serializer = LoginSerializer(data=request.data)
    if not serializer.is_valid():
        return ApiResponse(
            message="Invalid credentials",
            errors=serializer.errors,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    try:
        user   = serializer.validated_data['user']
        tokens = _auth.login(user)
        return ApiResponse(
            message="Login successful",
            data={'user': UserSerializer(user).data, **tokens},
            status_code=status.HTTP_200_OK,
        )
    except Exception as e:
        logger.error(f"Login error: {e}", exc_info=True)
        return ApiResponse(message=str(e), errors=str(e), status_code=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def logout(request):
    try:
        _auth.logout(request.data.get('refresh', ''))
        return ApiResponse(message="Logged out successfully", status_code=status.HTTP_200_OK)
    except Exception as e:
        logger.error(f"Logout error: {e}", exc_info=True)
        return ApiResponse(message=str(e), errors=str(e), status_code=status.HTTP_400_BAD_REQUEST)


# Reuse simplejwt's built-in token refresh
token_refresh = TokenRefreshView.as_view()


@api_view(['POST'])
@permission_classes([AllowAny])
def google_login(request):
    access_token = request.data.get('access_token', '')
    if not access_token:
        return ApiResponse(
            message="Missing access_token",
            errors={"access_token": "This field is required"},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        resp = requests.get(
            'https://www.googleapis.com/oauth2/v3/userinfo',
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=10,
        )
        if resp.status_code != 200:
            raise ValueError('Invalid Google access token')
        data = resp.json()
    except Exception as e:
        logger.warning(f"Google userinfo request failed: {e}")
        return ApiResponse(
            message="Invalid Google token",
            errors=str(e),
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    email   = data.get('email', '').lower()
    name    = data.get('name', '')
    picture = data.get('picture', '')

    if not email:
        return ApiResponse(
            message="Google account has no email",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    user, created = User.objects.get_or_create(
        email=email,
        defaults={'is_active': True},
    )

    if created:
        user.set_unusable_password()
        user.save()
        profile = user.profile
        profile.full_name_en = name
        if picture:
            profile.avatar = picture
        profile.save()
        _link_guest_orders(user)
        logger.info(f"New user created via Google OAuth: {email}")
    else:
        profile = user.profile
        if picture and not profile.avatar:
            profile.avatar = picture
            profile.save(update_fields=['avatar'])
        logger.info(f"Existing user signed in via Google OAuth: {email}")

    tokens = _auth.login(user)
    return ApiResponse(
        message="Login successful",
        data={'user': UserSerializer(user).data, **tokens},
        status_code=status.HTTP_200_OK,
    )
