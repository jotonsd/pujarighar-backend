import logging
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework_simplejwt.views import TokenRefreshView

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
        return ApiResponse(
            message="Registration successful",
            data={'user': UserSerializer(user).data, **tokens},
            status_code=status.HTTP_201_CREATED,
        )
    except Exception as e:
        logger.error(f"Register error: {e}", exc_info=True)
        return ApiResponse(message=str(e), errors=str(e), status_code=status.HTTP_400_BAD_REQUEST)


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
