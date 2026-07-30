import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from api.models import Permission, Role
from api.permissions import IsAdmin
from api.serializers.role_serializers import PermissionSerializer, RoleSerializer
from api.utils.response import ApiResponse

logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def list_roles(request):
    roles = Role.objects.all().prefetch_related('permissions')
    return ApiResponse(message="Roles retrieved", data=RoleSerializer(roles, many=True).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def list_permissions(request):
    permissions = Permission.objects.all()
    return ApiResponse(message="Permissions retrieved", data=PermissionSerializer(permissions, many=True).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAdmin])
def create_role(request):
    serializer = RoleSerializer(data=request.data)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    try:
        role = serializer.save()
        return ApiResponse(message="Role created", data=RoleSerializer(role).data, status_code=201)
    except Exception as e:
        logger.error(f"Create role error: {e}", exc_info=True)
        return ApiResponse(message=str(e), errors=str(e), status_code=400)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, IsAdmin])
def update_role(request, pk):
    try:
        role = Role.objects.get(pk=pk)
    except Role.DoesNotExist:
        return ApiResponse(message="Role not found", errors="Not found", status_code=404)
    serializer = RoleSerializer(role, data=request.data, partial=True)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    try:
        updated = serializer.save()
        return ApiResponse(message="Role updated", data=RoleSerializer(updated).data)
    except Exception as e:
        logger.error(f"Update role error: {e}", exc_info=True)
        return ApiResponse(message=str(e), errors=str(e), status_code=400)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, IsAdmin])
def delete_role(_request, pk):
    try:
        role = Role.objects.get(pk=pk)
    except Role.DoesNotExist:
        return ApiResponse(message="Role not found", errors="Not found", status_code=404)
    if role.is_system:
        return ApiResponse(
            message="System roles cannot be deleted",
            errors={'message_bn': 'সিস্টেম ভূমিকা মুছে ফেলা যাবে না', 'message_en': 'System roles cannot be deleted'},
            status_code=400,
        )
    if role.users.exists():
        return ApiResponse(
            message="Role is still assigned to users",
            errors={'message_bn': 'এই ভূমিকা এখনও ব্যবহারকারীদের সাথে যুক্ত আছে', 'message_en': 'This role is still assigned to one or more users'},
            status_code=400,
        )
    role.delete()
    return ApiResponse(message="Role deleted")
