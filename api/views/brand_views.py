import logging
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated

from api.models import Brand
from api.serializers.product_serializers import BrandSerializer
from api.services.product_service import BrandService
from api.utils.response import ApiResponse
from api.permissions import IsAdmin

logger = logging.getLogger(__name__)
_svc = BrandService()


@api_view(['GET'])
@permission_classes([AllowAny])
def list_brands(request):
    try:
        qs = _svc.list_brands(
            include_inactive=request.query_params.get('include_inactive') == 'true',
        )
        return ApiResponse(message="Brands retrieved", data=BrandSerializer(qs, many=True).data)
    except Exception as e:
        logger.error(f"List brands error: {e}", exc_info=True)
        return ApiResponse(message=str(e), errors=str(e), status_code=500)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAdmin])
def create_brand(request):
    serializer = BrandSerializer(data=request.data)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    try:
        brand = _svc.create_brand(serializer.validated_data)
        return ApiResponse(
            message="Brand created",
            data=BrandSerializer(brand).data,
            status_code=status.HTTP_201_CREATED,
        )
    except Exception as e:
        return ApiResponse(message=str(e), errors=str(e), status_code=400)


@api_view(['GET'])
@permission_classes([AllowAny])
def get_brand(_request, pk):
    try:
        return ApiResponse(message="Brand retrieved", data=BrandSerializer(_svc.get_brand(pk)).data)
    except Brand.DoesNotExist:
        return ApiResponse(message="Brand not found", errors="Not found", status_code=404)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, IsAdmin])
def update_brand(request, pk):
    try:
        brand = _svc.get_brand(pk)
    except Brand.DoesNotExist:
        return ApiResponse(message="Brand not found", errors="Not found", status_code=404)
    serializer = BrandSerializer(brand, data=request.data, partial=True)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    try:
        updated = _svc.update_brand(brand, serializer.validated_data)
        return ApiResponse(message="Brand updated", data=BrandSerializer(updated).data)
    except Exception as e:
        return ApiResponse(message=str(e), errors=str(e), status_code=400)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, IsAdmin])
def delete_brand(_request, pk):
    try:
        _svc.delete_brand(_svc.get_brand(pk))
        return ApiResponse(message="Brand deleted")
    except Brand.DoesNotExist:
        return ApiResponse(message="Brand not found", errors="Not found", status_code=404)
