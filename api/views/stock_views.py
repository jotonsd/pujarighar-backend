import logging
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from api.models import Product, ProductPackageItem
from api.serializers.product_serializers import (
    StockMovementSerializer, StockAdjustSerializer,
    PackageItemReadSerializer, PackageItemWriteSerializer,
)
from api.services.product_service import StockService
from api.utils.response import ApiResponse
from api.permissions import IsAdmin, IsAdminOrWarehouse

logger = logging.getLogger(__name__)
_svc = StockService()


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdminOrWarehouse])
def get_stock(_request, pk):
    try:
        product = Product.objects.get(pk=pk)
        detail  = _svc.get_stock_detail(product)
        return ApiResponse(message="Stock retrieved", data={
            'stock_on_hand': detail['stock_on_hand'],
            'movements': StockMovementSerializer(detail['movements'], many=True).data,
        })
    except Product.DoesNotExist:
        return ApiResponse(message="Product not found", errors="Not found", status_code=404)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAdminOrWarehouse])
def adjust_stock(request, pk):
    try:
        product = Product.objects.get(pk=pk)
    except Product.DoesNotExist:
        return ApiResponse(message="Product not found", errors="Not found", status_code=404)

    serializer = StockAdjustSerializer(data=request.data)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    try:
        d        = serializer.validated_data
        movement = _svc.adjust_stock(
            product, d['movement_type'], d['quantity'],
            d.get('note_bn', ''), d.get('note_en', ''), request.user,
            unit_cost=d.get('unit_cost', 0),
            unit_price=d.get('unit_price'),
            supplier_id=str(d['supplier_id']) if d.get('supplier_id') else None,
            supplier_name=d.get('supplier_name', ''),
            payment_method=d.get('payment_method', 'CASH'),
        )
        return ApiResponse(
            message="Stock adjusted",
            data=StockMovementSerializer(movement).data,
            status_code=status.HTTP_201_CREATED,
        )
    except Exception as e:
        logger.error(f"Stock adjust error: {e}", exc_info=True)
        return ApiResponse(message=str(e), errors=str(e), status_code=400)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def get_purchase_report(request):
    data = _svc.get_purchase_report(
        supplier_id=request.query_params.get('supplier_id', ''),
        product_id=request.query_params.get('product_id', ''),
        from_date=request.query_params.get('from', ''),
        to_date=request.query_params.get('to', ''),
    )
    return ApiResponse(message="Purchase report retrieved", data=data)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def get_supplier_return_report(request):
    data = _svc.get_supplier_return_report(
        supplier_id=request.query_params.get('supplier_id', ''),
        product_id=request.query_params.get('product_id', ''),
        from_date=request.query_params.get('from', ''),
        to_date=request.query_params.get('to', ''),
    )
    return ApiResponse(message="Supplier return report retrieved", data=data)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def list_package_items(_request, pk):
    try:
        product = Product.objects.get(pk=pk, is_package=True)
        items   = _svc.list_package_items(product)
        return ApiResponse(message="Package items retrieved", data=PackageItemReadSerializer(items, many=True).data)
    except Product.DoesNotExist:
        return ApiResponse(message="Package not found", errors="Not found", status_code=404)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAdmin])
def add_package_item(request, pk):
    try:
        package = Product.objects.get(pk=pk, is_package=True)
    except Product.DoesNotExist:
        return ApiResponse(message="Package not found", errors="Not found", status_code=404)

    serializer = PackageItemWriteSerializer(data=request.data)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    try:
        item = _svc.add_package_item(package, str(serializer.validated_data['component_id']), serializer.validated_data['quantity'])
        return ApiResponse(message="Package item added", data=PackageItemReadSerializer(item).data, status_code=201)
    except Exception as e:
        return ApiResponse(message=str(e), errors=str(e), status_code=400)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, IsAdmin])
def delete_package_item(_request, pk, item_id):
    try:
        item = ProductPackageItem.objects.get(pk=item_id, package_id=pk)
        _svc.delete_package_item(item)
        return ApiResponse(message="Package item deleted")
    except ProductPackageItem.DoesNotExist:
        return ApiResponse(message="Item not found", errors="Not found", status_code=404)
