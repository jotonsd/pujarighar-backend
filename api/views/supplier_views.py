import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from api.models import Supplier
from api.serializers.product_serializers import SupplierSerializer
from api.utils.response import ApiResponse
from api.permissions import IsAdmin

logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def list_suppliers(request):
    include_inactive = request.query_params.get('include_inactive') == 'true'
    qs = Supplier.objects.all() if include_inactive else Supplier.objects.filter(is_active=True)
    return ApiResponse(message="Suppliers retrieved", data=SupplierSerializer(qs, many=True).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAdmin])
def create_supplier(request):
    serializer = SupplierSerializer(data=request.data)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    supplier = serializer.save()
    return ApiResponse(message="Supplier created", data=SupplierSerializer(supplier).data, status_code=201)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def get_supplier(_request, pk):
    try:
        supplier = Supplier.objects.get(pk=pk)
        return ApiResponse(message="Supplier retrieved", data=SupplierSerializer(supplier).data)
    except Supplier.DoesNotExist:
        return ApiResponse(message="Not found", errors="Not found", status_code=404)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, IsAdmin])
def update_supplier(request, pk):
    try:
        supplier = Supplier.objects.get(pk=pk)
    except Supplier.DoesNotExist:
        return ApiResponse(message="Not found", errors="Not found", status_code=404)
    serializer = SupplierSerializer(supplier, data=request.data, partial=True)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    serializer.save()
    return ApiResponse(message="Supplier updated", data=serializer.data)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, IsAdmin])
def delete_supplier(_request, pk):
    try:
        supplier = Supplier.objects.get(pk=pk)
        supplier.is_active = False
        supplier.save(update_fields=['is_active'])
        return ApiResponse(message="Supplier deleted")
    except Supplier.DoesNotExist:
        return ApiResponse(message="Not found", errors="Not found", status_code=404)
