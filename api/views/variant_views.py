import logging
from django.db.models import ProtectedError
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated

from api.models import Product, ProductVariant, VariantAttributeType, VariantAttributeValue
from api.serializers.product_serializers import (
    VariantAttributeTypeSerializer, VariantAttributeValueSerializer, ProductVariantSerializer,
    AttributeTypeWriteSerializer, AttributeTypeUpdateSerializer,
    AttributeValueWriteSerializer, AttributeValueUpdateSerializer,
    GenerateVariantsSerializer, VariantUpdateSerializer,
)
from api.services.product_service import VariantService
from api.utils.response import ApiResponse, api_error
from api.permissions import has_permission

logger = logging.getLogger(__name__)
_svc = VariantService()


@api_view(['GET'])
@permission_classes([AllowAny])
def list_attribute_types(request):
    # Readable by anyone — the storefront needs this to label pill-rows
    # generically (attribute_type_code -> name), same as categories/brands.
    # include_inactive is only meaningful for the admin settings screen
    # (an anonymous storefront call never passes it).
    include_inactive = request.query_params.get('include_inactive') == 'true'
    types = _svc.list_attribute_types(include_inactive=include_inactive)
    return ApiResponse(message="Attribute types retrieved", data=VariantAttributeTypeSerializer(types, many=True).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated, has_permission('products', 'edit')])
def create_attribute_type(request):
    serializer = AttributeTypeWriteSerializer(data=request.data)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    d = serializer.validated_data
    attribute_type = _svc.create_attribute_type(d['name_bn'], d['name_en'], d['code'], d['has_bilingual_values'])
    return ApiResponse(message="Attribute type created", data=VariantAttributeTypeSerializer(attribute_type).data, status_code=201)


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsAuthenticated, has_permission('products', 'edit')])
def attribute_type_detail(request, pk):
    try:
        attribute_type = VariantAttributeType.objects.get(pk=pk)
    except VariantAttributeType.DoesNotExist:
        return ApiResponse(message="Attribute type not found", errors="Not found", status_code=404)

    if request.method == 'DELETE':
        try:
            _svc.delete_attribute_type(attribute_type)
        except ProtectedError:
            return ApiResponse(
                message="Cannot delete",
                errors={'message_bn': 'এই ধরনের মান এখনও ব্যবহৃত হচ্ছে', 'message_en': 'Values under this type are still in use'},
                status_code=400,
            )
        return ApiResponse(message="Attribute type deleted")

    serializer = AttributeTypeUpdateSerializer(data=request.data, partial=True)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    updated = _svc.update_attribute_type(attribute_type, serializer.validated_data)
    return ApiResponse(message="Attribute type updated", data=VariantAttributeTypeSerializer(updated).data)


@api_view(['GET'])
@permission_classes([AllowAny])
def list_attribute_values(request):
    include_inactive = request.query_params.get('include_inactive') == 'true'
    values = _svc.list_attribute_values(request.query_params.get('attribute_type_id'), include_inactive=include_inactive)
    return ApiResponse(message="Attribute values retrieved", data=VariantAttributeValueSerializer(values, many=True).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated, has_permission('products', 'edit')])
def create_attribute_value(request):
    serializer = AttributeValueWriteSerializer(data=request.data)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    d = serializer.validated_data
    attribute_type = VariantAttributeType.objects.get(pk=d['attribute_type_id'])
    value = _svc.create_attribute_value(attribute_type, d.get('value_bn', ''), d['value_en'])
    return ApiResponse(message="Attribute value created", data=VariantAttributeValueSerializer(value).data, status_code=201)


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsAuthenticated, has_permission('products', 'edit')])
def attribute_value_detail(request, pk):
    try:
        value = VariantAttributeValue.objects.get(pk=pk)
    except VariantAttributeValue.DoesNotExist:
        return ApiResponse(message="Attribute value not found", errors="Not found", status_code=404)

    if request.method == 'DELETE':
        try:
            _svc.delete_attribute_value(value)
        except ProtectedError:
            return ApiResponse(
                message="Cannot delete",
                errors={'message_bn': 'এই মানটি কোনো ভ্যারিয়েন্টে ব্যবহৃত হচ্ছে', 'message_en': 'This value is still used by a variant'},
                status_code=400,
            )
        return ApiResponse(message="Attribute value deleted")

    serializer = AttributeValueUpdateSerializer(data=request.data, partial=True)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    updated = _svc.update_attribute_value(value, serializer.validated_data)
    return ApiResponse(message="Attribute value updated", data=VariantAttributeValueSerializer(updated).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated, has_permission('products', 'edit')])
def generate_product_variants(request, pk):
    try:
        product = Product.objects.get(pk=pk)
    except Product.DoesNotExist:
        return ApiResponse(message="Product not found", errors="Not found", status_code=404)

    serializer = GenerateVariantsSerializer(data=request.data)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    try:
        created = _svc.generate_variants(product, [str(v) for v in serializer.validated_data['value_ids']])
        return ApiResponse(
            message=f"{len(created)} variant(s) created",
            data=ProductVariantSerializer(created, many=True).data,
            status_code=201,
        )
    except Exception as e:
        logger.error(f"Generate variants error: {e}", exc_info=True)
        return api_error(e)


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsAuthenticated, has_permission('products', 'edit')])
def variant_detail(request, pk, variant_id):
    try:
        variant = ProductVariant.objects.get(pk=variant_id, product_id=pk)
    except ProductVariant.DoesNotExist:
        return ApiResponse(message="Variant not found", errors="Not found", status_code=404)

    if request.method == 'DELETE':
        _svc.delete_variant(variant)
        return ApiResponse(message="Variant deleted")

    serializer = VariantUpdateSerializer(data=request.data, partial=True)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    updated = _svc.update_variant(variant, serializer.validated_data)
    return ApiResponse(message="Variant updated", data=ProductVariantSerializer(updated).data)
