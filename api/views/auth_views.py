import logging
import requests
from django.conf import settings as django_settings
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework_simplejwt.views import TokenRefreshView

from api.models import SalesOrder, User
from api.serializers.auth_serializers import (
    RegisterSerializer, LoginSerializer, ForgotPasswordSerializer, ResetPasswordSerializer,
)
from api.serializers.user_serializers import UserSerializer
from api.services.auth_service import AuthService
from api.services import mail_service
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
@permission_classes([AllowAny])
def forgot_password(request):
    serializer = ForgotPasswordSerializer(data=request.data)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)

    email = serializer.validated_data['email'].strip().lower()
    locale = request.data.get('locale') if request.data.get('locale') in ('bn', 'en') else 'bn'
    generic_message = "If that email is registered, a reset link has been sent."

    try:
        user = User.objects.get(email=email, is_active=True)
    except User.DoesNotExist:
        # Don't reveal whether the email exists
        return ApiResponse(message=generic_message)

    uid   = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    reset_link = f"{django_settings.FRONTEND_URL}/{locale}/auth/reset-password?uid={uid}&token={token}"
    mail_service.send_password_reset(user, reset_link)
    return ApiResponse(message=generic_message)


@api_view(['POST'])
@permission_classes([AllowAny])
def reset_password(request):
    serializer = ResetPasswordSerializer(data=request.data)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)

    try:
        uid  = force_str(urlsafe_base64_decode(serializer.validated_data['uid']))
        user = User.objects.get(pk=uid)
    except (User.DoesNotExist, ValueError, TypeError, OverflowError):
        return ApiResponse(
            message="Invalid reset link",
            errors={'message_bn': 'লিংকটি অবৈধ', 'message_en': 'Invalid reset link'},
            status_code=400,
        )

    if not default_token_generator.check_token(user, serializer.validated_data['token']):
        return ApiResponse(
            message="Invalid or expired reset link",
            errors={'message_bn': 'লিংকটির মেয়াদ শেষ হয়ে গেছে', 'message_en': 'This reset link has expired'},
            status_code=400,
        )

    user.set_password(serializer.validated_data['new_password'])
    user.save(update_fields=['password'])
    logger.info(f"Password reset for user: {user.email}")
    return ApiResponse(message="Password reset successful")


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


def _oauth_login_or_create(email: str, name: str, picture: str, provider_label: str):
    """Shared get-or-create + sign-in logic for social login providers. Returns
    the User, or None if the provider didn't give us an email to key on."""
    email = (email or '').lower()
    if not email:
        return None

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
        logger.info(f"New user created via {provider_label} OAuth: {email}")
    else:
        profile = user.profile
        if picture and not profile.avatar:
            profile.avatar = picture
            profile.save(update_fields=['avatar'])
        logger.info(f"Existing user signed in via {provider_label} OAuth: {email}")

    return user


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

    user = _oauth_login_or_create(data.get('email', ''), data.get('name', ''), data.get('picture', ''), 'Google')
    if not user:
        return ApiResponse(
            message="Google account has no email",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    tokens = _auth.login(user)
    return ApiResponse(
        message="Login successful",
        data={'user': UserSerializer(user).data, **tokens},
        status_code=status.HTTP_200_OK,
    )


@api_view(['POST'])
@permission_classes([AllowAny])
def facebook_login(request):
    access_token = request.data.get('access_token', '')
    if not access_token:
        return ApiResponse(
            message="Missing access_token",
            errors={"access_token": "This field is required"},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        resp = requests.get(
            'https://graph.facebook.com/me',
            params={'fields': 'id,name,email,picture.type(large)', 'access_token': access_token},
            timeout=10,
        )
        if resp.status_code != 200:
            raise ValueError('Invalid Facebook access token')
        data = resp.json()
    except Exception as e:
        logger.warning(f"Facebook userinfo request failed: {e}")
        return ApiResponse(
            message="Invalid Facebook token",
            errors=str(e),
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    picture = (data.get('picture') or {}).get('data', {}).get('url', '')
    user = _oauth_login_or_create(data.get('email', ''), data.get('name', ''), picture, 'Facebook')
    if not user:
        # Facebook only returns an email if the account has one verified and the
        # user granted the `email` permission — neither is guaranteed.
        return ApiResponse(
            message="Facebook account has no email",
            errors={
                'message_bn': 'আপনার ফেসবুক অ্যাকাউন্টে কোনো যাচাইকৃত ইমেইল নেই। অনুগ্রহ করে অন্য পদ্ধতিতে লগইন করুন।',
                'message_en': "Your Facebook account doesn't have a verified email. Please use another login method.",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    tokens = _auth.login(user)
    return ApiResponse(
        message="Login successful",
        data={'user': UserSerializer(user).data, **tokens},
        status_code=status.HTTP_200_OK,
    )
