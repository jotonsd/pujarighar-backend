import logging
from decimal import Decimal
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from django.db.models import Sum, Subquery, OuterRef, DecimalField, Value
from django.db.models.functions import Coalesce

from api.models import Product, ProductImage, Category, SalesOrderItem
from api.serializers.product_serializers import ProductSerializer, ProductImageSerializer, CategorySerializer
from api.services.product_service import ProductService
from api.utils.response import ApiResponse
from api.utils.pagination import paginate_queryset
from api.permissions import IsAdmin

logger = logging.getLogger(__name__)
_svc = ProductService()


def _ctx(request):
    return {'request': request}


@api_view(['GET'])
@permission_classes([AllowAny])
def list_products(request):
    try:
        include_inactive = request.query_params.get('include_inactive', '').lower() == 'true'
        qs = _svc.list_products(
            category=request.query_params.get('category'),
            brand=request.query_params.get('brand'),
            search=request.query_params.get('search', ''),
            is_package=request.query_params.get('is_package'),
            min_price=request.query_params.get('min_price'),
            max_price=request.query_params.get('max_price'),
            include_inactive=include_inactive,
            ordering=request.query_params.get('ordering'),
            has_discount=request.query_params.get('has_discount', '').lower() == 'true',
            is_active=request.query_params.get('is_active'),
        )
        page_data, pagination = paginate_queryset(qs, request)
        return ApiResponse(
            message="Products retrieved successfully",
            data=ProductSerializer(page_data, many=True, context=_ctx(request)).data,
            pagination=pagination,
        )
    except Exception as e:
        logger.error(f"List products error: {e}", exc_info=True)
        return ApiResponse(message=str(e), errors=str(e), status_code=500)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAdmin])
def create_product(request):
    serializer = ProductSerializer(data=request.data, context=_ctx(request))
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    try:
        product = _svc.create_product(serializer.validated_data)
        return ApiResponse(
            message="Product created",
            data=ProductSerializer(product, context=_ctx(request)).data,
            status_code=status.HTTP_201_CREATED,
        )
    except Exception as e:
        logger.error(f"Create product error: {e}", exc_info=True)
        return ApiResponse(message=str(e), errors=str(e), status_code=400)


@api_view(['GET'])
@permission_classes([AllowAny])
def get_product(request, pk):
    try:
        product = _svc.get_product(pk)
        return ApiResponse(
            message="Product retrieved",
            data=ProductSerializer(product, context=_ctx(request)).data,
        )
    except Product.DoesNotExist:
        return ApiResponse(message="Product not found", errors="Not found", status_code=404)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, IsAdmin])
def update_product(request, pk):
    try:
        product = _svc.get_product(pk)
    except Product.DoesNotExist:
        return ApiResponse(message="Product not found", errors="Not found", status_code=404)
    serializer = ProductSerializer(product, data=request.data, partial=True, context=_ctx(request))
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    try:
        updated = _svc.update_product(product, serializer.validated_data)
        return ApiResponse(
            message="Product updated",
            data=ProductSerializer(updated, context=_ctx(request)).data,
        )
    except Exception as e:
        return ApiResponse(message=str(e), errors=str(e), status_code=400)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, IsAdmin])
def delete_product(_request, pk):
    try:
        product = _svc.get_product(pk)
        _svc.delete_product(product)
        return ApiResponse(message="Product deleted")
    except Product.DoesNotExist:
        return ApiResponse(message="Product not found", errors="Not found", status_code=404)


MAX_IMAGES = 5

@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAdmin])
def add_product_image(request, pk):
    try:
        product = _svc.get_product(pk)
    except Product.DoesNotExist:
        return ApiResponse(message="Product not found", errors="Not found", status_code=404)

    files = request.FILES.getlist('images') or (
        [request.FILES['image']] if 'image' in request.FILES else []
    )
    if not files:
        return ApiResponse(message="No image provided", errors="images is required", status_code=422)

    current_count = product.images.count()
    slots = MAX_IMAGES - current_count
    if slots <= 0:
        return ApiResponse(
            message=f"Maximum {MAX_IMAGES} images allowed",
            errors={'message_bn': f'সর্বোচ্চ {MAX_IMAGES}টি ছবি দেওয়া যাবে', 'message_en': f'Maximum {MAX_IMAGES} images allowed'},
            status_code=400,
        )

    files = files[:slots]
    created = []
    for i, file in enumerate(files):
        img = ProductImage.objects.create(
            product=product,
            image=file,
            alt_bn=request.data.get('alt_bn', ''),
            alt_en=request.data.get('alt_en', ''),
            order=current_count + i,
        )
        created.append(img)

    return ApiResponse(
        message=f"{len(created)} image(s) uploaded",
        data=ProductImageSerializer(created, many=True, context=_ctx(request)).data,
        status_code=201,
    )


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, IsAdmin])
def delete_product_image(request, pk, image_id):
    try:
        img = ProductImage.objects.get(pk=image_id, product_id=pk)
        img.image.delete(save=False)
        img.delete()
        return ApiResponse(message="Image deleted")
    except ProductImage.DoesNotExist:
        return ApiResponse(message="Image not found", errors="Not found", status_code=404)


@api_view(['GET'])
@permission_classes([AllowAny])
def popular_by_category(request):
    try:
        sold_sq = Subquery(
            SalesOrderItem.objects.filter(
                product_id=OuterRef('pk'),
                order__status__in=['CONFIRMED', 'PACKED', 'ASSIGNED', 'ON_THE_WAY', 'DELIVERED'],
            ).values('product_id').annotate(total=Sum('quantity')).values('total')[:1],
            output_field=DecimalField(max_digits=12, decimal_places=2),
        )

        categories = Category.objects.filter(is_active=True).order_by('name_bn')
        result = []
        for cat in categories:
            products = list(
                Product.objects
                .filter(category=cat, is_active=True, is_package=False)
                .select_related('category', 'brand')
                .prefetch_related('images')
                .annotate(total_sold=Coalesce(sold_sq, Value(Decimal('0')), output_field=DecimalField()))
                .order_by('-total_sold')[:12]
            )
            if products:
                result.append({
                    'category': CategorySerializer(cat).data,
                    'products': ProductSerializer(products, many=True, context={'request': request}).data,
                })
        return ApiResponse(message="Popular by category", data=result)
    except Exception as e:
        logger.error(f"popular_by_category error: {e}", exc_info=True)
        return ApiResponse(message=str(e), errors=str(e), status_code=500)
