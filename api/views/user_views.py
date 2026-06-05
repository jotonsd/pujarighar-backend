import logging
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from api.models import User
from api.serializers.auth_serializers import ChangePasswordSerializer
from api.serializers.user_serializers import (
    UserSerializer, ProfileSerializer,
    AdminCreateUserSerializer, AdminUpdateUserSerializer, ChangeRoleSerializer,
)
from api.services.user_service import UserService
from api.utils.response import ApiResponse
from api.utils.pagination import paginate_queryset
from api.permissions import IsAdmin

logger = logging.getLogger(__name__)
_svc = UserService()


# ─── Admin: user list & create ───────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def list_users(request):
    try:
        qs = _svc.list_users(
            role=request.query_params.get('role', ''),
            search=request.query_params.get('search', ''),
            is_active=request.query_params.get('is_active', ''),
        )
        page_data, pagination = paginate_queryset(qs, request)
        return ApiResponse(
            message="Users retrieved successfully",
            data=UserSerializer(page_data, many=True).data,
            pagination=pagination,
            status_code=status.HTTP_200_OK,
        )
    except Exception as e:
        logger.error(f"List users error: {e}", exc_info=True)
        return ApiResponse(message=str(e), errors=str(e), status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAdmin])
def create_user(request):
    serializer = AdminCreateUserSerializer(data=request.data)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    try:
        user = _svc.create_user(serializer.validated_data)
        return ApiResponse(message="User created", data=UserSerializer(user).data, status_code=201)
    except Exception as e:
        logger.error(f"Create user error: {e}", exc_info=True)
        return ApiResponse(message=str(e), errors=str(e), status_code=400)


# ─── Admin: user detail ───────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def get_user(request, pk):
    try:
        user = _svc.get_user(pk)
        return ApiResponse(message="User retrieved", data=UserSerializer(user).data)
    except User.DoesNotExist:
        return ApiResponse(message="User not found", errors="Not found", status_code=404)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, IsAdmin])
def update_user(request, pk):
    try:
        user = _svc.get_user(pk)
    except User.DoesNotExist:
        return ApiResponse(message="User not found", errors="Not found", status_code=404)
    serializer = AdminUpdateUserSerializer(user, data=request.data, partial=True, context={'request': request})
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    try:
        updated = _svc.update_user(user, serializer.validated_data)
        return ApiResponse(message="User updated", data=UserSerializer(updated).data)
    except Exception as e:
        logger.error(f"Update user error: {e}", exc_info=True)
        return ApiResponse(message=str(e), errors=str(e), status_code=400)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, IsAdmin])
def delete_user(request, pk):
    try:
        user = _svc.get_user(pk)
        _svc.deactivate(user)
        return ApiResponse(message="User deactivated")
    except User.DoesNotExist:
        return ApiResponse(message="User not found", errors="Not found", status_code=404)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAdmin])
def activate_user(request, pk):
    try:
        user = _svc.get_user(pk)
        _svc.activate(user)
        return ApiResponse(message="User activated", data=UserSerializer(user).data)
    except User.DoesNotExist:
        return ApiResponse(message="User not found", errors="Not found", status_code=404)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAdmin])
def deactivate_user(request, pk):
    try:
        user = _svc.get_user(pk)
        _svc.deactivate(user)
        return ApiResponse(message="User deactivated", data=UserSerializer(user).data)
    except User.DoesNotExist:
        return ApiResponse(message="User not found", errors="Not found", status_code=404)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAdmin])
def change_role(request, pk):
    try:
        user = _svc.get_user(pk)
    except User.DoesNotExist:
        return ApiResponse(message="User not found", errors="Not found", status_code=404)
    serializer = ChangeRoleSerializer(data=request.data, context={'request': request, 'target_user': user})
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    try:
        updated = _svc.change_role(user, serializer.validated_data['role'])
        return ApiResponse(message="Role updated", data=UserSerializer(updated).data)
    except Exception as e:
        return ApiResponse(message=str(e), errors=str(e), status_code=400)


# ─── Current user ─────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_me(request):
    return ApiResponse(message="Profile retrieved", data=UserSerializer(request.user).data)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def update_me(request):
    serializer = ProfileSerializer(request.user.profile, data=request.data, partial=True)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    try:
        _svc.update_profile(request.user, {**serializer.validated_data,
                                            'preferred_language': request.data.get('preferred_language')})
        return ApiResponse(message="Profile updated", data=UserSerializer(request.user).data)
    except Exception as e:
        return ApiResponse(message=str(e), errors=str(e), status_code=400)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def change_password(request):
    serializer = ChangePasswordSerializer(data=request.data, context={'request': request})
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    try:
        _svc.change_password(request.user, serializer.validated_data['new_password'])
        return ApiResponse(message="Password changed successfully")
    except Exception as e:
        return ApiResponse(message=str(e), errors=str(e), status_code=400)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def list_delivery_persons(request):
    persons = _svc.list_delivery_persons()
    return ApiResponse(message="Delivery persons retrieved", data=UserSerializer(persons, many=True).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def lookup_user_by_phone(request):
    phone = request.query_params.get('phone', '').strip()
    if not phone:
        return ApiResponse(message="Phone required", errors="phone param required", status_code=400)
    try:
        user = User.objects.get(phone=phone)
        return ApiResponse(message="User found", data=UserSerializer(user).data)
    except User.DoesNotExist:
        return ApiResponse(message="Not found", errors="No user with this phone", status_code=404)
