import logging
from rest_framework import serializers as drf_serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from api.models import Discount, Product
from api.permissions import IsAdmin
from api.utils.response import ApiResponse

logger = logging.getLogger(__name__)


class DiscountSerializer(drf_serializers.ModelSerializer):
    product_name_bn = drf_serializers.CharField(source='product.name_bn', read_only=True)
    product_name_en = drf_serializers.CharField(source='product.name_en', read_only=True)
    product_sku     = drf_serializers.CharField(source='product.sku', read_only=True)
    product_image   = drf_serializers.SerializerMethodField()

    class Meta:
        model  = Discount
        fields = ['id', 'product', 'product_name_bn', 'product_name_en', 'product_sku', 'product_image',
                  'discount_type', 'discount_value', 'note', 'is_active',
                  'start_date', 'end_date', 'created_at']
        read_only_fields = ['id', 'created_at']

    def get_product_image(self, obj):
        image = obj.product.images.first()
        if not image:
            return None
        request = self.context.get('request')
        return request.build_absolute_uri(image.image.url) if request else image.image.url


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def list_discounts(request):
    product_id = request.query_params.get('product')
    qs = Discount.objects.select_related('product').prefetch_related('product__images')
    if product_id:
        qs = qs.filter(product_id=product_id)
    return ApiResponse(message="Discounts retrieved", data=DiscountSerializer(qs, many=True, context={'request': request}).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAdmin])
def create_discount(request):
    s = DiscountSerializer(data=request.data, context={'request': request})
    if not s.is_valid():
        return ApiResponse(message="Validation failed", errors=s.errors, status_code=422)
    discount = s.save()
    return ApiResponse(message="Discount created", data=DiscountSerializer(discount, context={'request': request}).data, status_code=201)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, IsAdmin])
def toggle_discount(request, pk):
    try:
        discount = Discount.objects.get(pk=pk)
        discount.is_active = not discount.is_active
        discount.save(update_fields=['is_active'])
        return ApiResponse(message="Updated", data=DiscountSerializer(discount, context={'request': request}).data)
    except Discount.DoesNotExist:
        return ApiResponse(message="Not found", errors="Not found", status_code=404)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, IsAdmin])
def update_discount(request, pk):
    try:
        discount = Discount.objects.get(pk=pk)
    except Discount.DoesNotExist:
        return ApiResponse(message="Not found", errors="Not found", status_code=404)
    s = DiscountSerializer(discount, data=request.data, partial=True, context={'request': request})
    if not s.is_valid():
        return ApiResponse(message="Validation failed", errors=s.errors, status_code=422)
    s.save()
    return ApiResponse(message="Discount updated", data=DiscountSerializer(discount, context={'request': request}).data)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, IsAdmin])
def delete_discount(request, pk):
    try:
        Discount.objects.get(pk=pk).delete()
        return ApiResponse(message="Deleted")
    except Discount.DoesNotExist:
        return ApiResponse(message="Not found", errors="Not found", status_code=404)
