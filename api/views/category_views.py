import logging
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated

from api.models import Category
from api.serializers.product_serializers import CategorySerializer
from api.services.product_service import CategoryService
from api.utils.response import ApiResponse
from api.permissions import IsAdmin

logger = logging.getLogger(__name__)
_svc = CategoryService()


@api_view(['GET'])
@permission_classes([AllowAny])
def list_categories(request):
    try:
        qs = _svc.list_categories(
            parent=request.query_params.get('parent'),
            include_inactive=request.query_params.get('include_inactive') == 'true',
        )
        return ApiResponse(message="Categories retrieved", data=CategorySerializer(qs, many=True).data)
    except Exception as e:
        logger.error(f"List categories error: {e}", exc_info=True)
        return ApiResponse(message=str(e), errors=str(e), status_code=500)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAdmin])
def create_category(request):
    serializer = CategorySerializer(data=request.data)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    try:
        category = _svc.create_category(serializer.validated_data)
        return ApiResponse(
            message="Category created",
            data=CategorySerializer(category).data,
            status_code=status.HTTP_201_CREATED,
        )
    except Exception as e:
        return ApiResponse(message=str(e), errors=str(e), status_code=400)


@api_view(['GET'])
@permission_classes([AllowAny])
def get_category(_request, pk):
    try:
        return ApiResponse(message="Category retrieved", data=CategorySerializer(_svc.get_category(pk)).data)
    except Category.DoesNotExist:
        return ApiResponse(message="Category not found", errors="Not found", status_code=404)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, IsAdmin])
def update_category(request, pk):
    try:
        category = _svc.get_category(pk)
    except Category.DoesNotExist:
        return ApiResponse(message="Category not found", errors="Not found", status_code=404)
    serializer = CategorySerializer(category, data=request.data, partial=True)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    try:
        updated = _svc.update_category(category, serializer.validated_data)
        return ApiResponse(message="Category updated", data=CategorySerializer(updated).data)
    except Exception as e:
        return ApiResponse(message=str(e), errors=str(e), status_code=400)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, IsAdmin])
def delete_category(_request, pk):
    try:
        _svc.delete_category(_svc.get_category(pk))
        return ApiResponse(message="Category deleted")
    except Category.DoesNotExist:
        return ApiResponse(message="Category not found", errors="Not found", status_code=404)
