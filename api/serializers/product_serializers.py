from decimal import Decimal
from rest_framework import serializers
from api.models import Category, Product, ProductImage, ProductPackageItem, StockMovement


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model  = Category
        fields = ['id', 'name_bn', 'name_en', 'slug', 'parent', 'icon', 'is_active', 'created_at']


class ProductImageSerializer(serializers.ModelSerializer):
    class Meta:
        model  = ProductImage
        fields = ['id', 'image', 'alt_bn', 'alt_en', 'order']


class PackageItemReadSerializer(serializers.ModelSerializer):
    component_id      = serializers.UUIDField(source='component.id', read_only=True)
    component_name_bn = serializers.CharField(source='component.name_bn', read_only=True)
    component_name_en = serializers.CharField(source='component.name_en', read_only=True)
    component_sku     = serializers.CharField(source='component.sku', read_only=True)
    unit_price        = serializers.DecimalField(source='component.unit_price', max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model  = ProductPackageItem
        fields = ['id', 'component_id', 'component_name_bn', 'component_name_en', 'component_sku', 'quantity', 'unit_price']


class PackageItemWriteSerializer(serializers.Serializer):
    component_id = serializers.UUIDField()
    quantity     = serializers.DecimalField(max_digits=10, decimal_places=3, min_value=Decimal('0.001'))

    def validate_component_id(self, value):
        try:
            component = Product.objects.get(id=value, is_active=True)
        except Product.DoesNotExist:
            raise serializers.ValidationError({'message_en': 'Component product not found'})
        if component.is_package:
            raise serializers.ValidationError({'message_en': 'Nested packages are not allowed'})
        return value


class ProductSerializer(serializers.ModelSerializer):
    stock_on_hand = serializers.DecimalField(max_digits=12, decimal_places=3, read_only=True)
    images        = ProductImageSerializer(many=True, read_only=True)
    package_items = PackageItemReadSerializer(many=True, read_only=True)
    category_name_bn = serializers.CharField(source='category.name_bn', read_only=True)
    category_name_en = serializers.CharField(source='category.name_en', read_only=True)

    class Meta:
        model  = Product
        fields = [
            'id', 'name_bn', 'name_en',
            'description_bn', 'description_en',
            'sku', 'category', 'category_name_bn', 'category_name_en',
            'unit_price', 'cost_price',
            'unit_bn', 'unit_en',
            'is_package', 'discount_type', 'discount_value', 'is_active',
            'stock_on_hand', 'images', 'package_items',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class StockMovementSerializer(serializers.ModelSerializer):
    created_by_email = serializers.EmailField(source='created_by.email', read_only=True)

    class Meta:
        model  = StockMovement
        fields = [
            'id', 'product', 'movement_type', 'quantity',
            'reference_id', 'note_bn', 'note_en',
            'created_by', 'created_by_email', 'created_at',
        ]
        read_only_fields = ['id', 'created_by', 'created_at']


class StockAdjustSerializer(serializers.Serializer):
    movement_type = serializers.ChoiceField(choices=['PURCHASE', 'ADJUSTMENT'])
    quantity      = serializers.DecimalField(max_digits=12, decimal_places=3)
    note_bn       = serializers.CharField(required=False, allow_blank=True, default='')
    note_en       = serializers.CharField(required=False, allow_blank=True, default='')

    def validate_quantity(self, value):
        if value == 0:
            raise serializers.ValidationError('Quantity cannot be zero')
        return value
