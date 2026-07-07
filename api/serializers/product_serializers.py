from decimal import Decimal
from rest_framework import serializers
from django.db.models import Q, Sum
from django.utils import timezone
from api.models import Brand, Category, Product, ProductImage, ProductPackageItem, StockMovement, Supplier, SupplierPayment


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model  = Category
        fields = ['id', 'name_bn', 'name_en', 'slug', 'parent', 'icon', 'is_active', 'created_at']


class BrandSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Brand
        fields = ['id', 'name_bn', 'name_en', 'slug', 'logo', 'is_active', 'created_at']


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
    stock_on_hand        = serializers.DecimalField(max_digits=12, decimal_places=3, read_only=True)
    images               = ProductImageSerializer(many=True, read_only=True)
    package_items        = PackageItemReadSerializer(many=True, read_only=True)
    category_name_bn     = serializers.CharField(source='category.name_bn', read_only=True)
    category_name_en     = serializers.CharField(source='category.name_en', read_only=True)
    brand_name_bn        = serializers.CharField(source='brand.name_bn', read_only=True, default=None)
    brand_name_en        = serializers.CharField(source='brand.name_en', read_only=True, default=None)
    effective_price      = serializers.SerializerMethodField()
    original_price       = serializers.SerializerMethodField()
    active_discount_type  = serializers.SerializerMethodField()
    active_discount_value = serializers.SerializerMethodField()
    average_rating        = serializers.FloatField(read_only=True, default=None)
    review_count          = serializers.IntegerField(read_only=True, default=0)

    def _active_discount(self, obj):
        today = timezone.now().date()
        return (
            obj.discounts
            .filter(is_active=True)
            .filter(Q(start_date__isnull=True) | Q(start_date__lte=today))
            .filter(Q(end_date__isnull=True)   | Q(end_date__gte=today))
            .order_by('-created_at')
            .first()
        )

    def get_effective_price(self, obj):
        return str(obj.effective_price)

    def get_original_price(self, obj):
        return str(obj.original_price)

    def get_active_discount_type(self, obj):
        d = self._active_discount(obj)
        return d.discount_type if d else None

    def get_active_discount_value(self, obj):
        d = self._active_discount(obj)
        return str(d.discount_value) if d else None

    class Meta:
        model  = Product
        fields = [
            'id', 'name_bn', 'name_en',
            'description_bn', 'description_en',
            'sku', 'category', 'category_name_bn', 'category_name_en',
            'brand', 'brand_name_bn', 'brand_name_en',
            'unit_price', 'cost_price', 'effective_price', 'original_price',
            'active_discount_type', 'active_discount_value',
            'unit_bn', 'unit_en',
            'is_package', 'discount_type', 'discount_value', 'is_active',
            'stock_on_hand', 'images', 'package_items',
            'average_rating', 'review_count',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'cost_price', 'created_at', 'updated_at']


class SupplierSerializer(serializers.ModelSerializer):
    total_credit  = serializers.SerializerMethodField()
    total_paid    = serializers.SerializerMethodField()
    total_balance = serializers.SerializerMethodField()

    class Meta:
        model  = Supplier
        fields = ['id', 'name_bn', 'name_en', 'phone', 'address', 'is_active', 'created_at',
                  'total_credit', 'total_paid', 'total_balance']
        read_only_fields = ['id', 'created_at', 'total_credit', 'total_paid', 'total_balance']

    def _totals(self, obj):
        if not hasattr(obj, '_sup_cache'):
            # SUPPLIER_RETURN movements carry a negative quantity, so summing them
            # alongside PURCHASE naturally nets a credit return against what's owed.
            movements    = obj.stockmovement_set.filter(
                movement_type__in=['PURCHASE', 'SUPPLIER_RETURN'], payment_method='CREDIT',
            )
            total_credit = sum(m.unit_cost * m.quantity for m in movements)
            total_paid   = obj.payments.aggregate(t=Sum('amount'))['t'] or Decimal('0')
            obj._sup_cache = (Decimal(str(total_credit)), Decimal(str(total_paid)))
        return obj._sup_cache

    def get_total_credit(self, obj):
        return str(self._totals(obj)[0])

    def get_total_paid(self, obj):
        return str(self._totals(obj)[1])

    def get_total_balance(self, obj):
        c, p = self._totals(obj)
        return str(c - p)


class SupplierPaymentSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(source='supplier.name_bn', read_only=True)
    created_by_email = serializers.EmailField(source='created_by.email', read_only=True)

    class Meta:
        model  = SupplierPayment
        fields = ['id', 'supplier', 'supplier_name', 'amount', 'paid_date', 'note',
                  'created_by', 'created_by_email', 'created_at']
        read_only_fields = ['id', 'created_by', 'created_at', 'supplier_name', 'created_by_email']


class StockMovementSerializer(serializers.ModelSerializer):
    created_by_email = serializers.EmailField(source='created_by.email', read_only=True)
    supplier_display = serializers.SerializerMethodField()

    class Meta:
        model  = StockMovement
        fields = [
            'id', 'product', 'movement_type', 'quantity', 'unit_cost',
            'supplier', 'supplier_name', 'supplier_display', 'payment_method',
            'reference_id', 'note_bn', 'note_en',
            'created_by', 'created_by_email', 'created_at',
        ]
        read_only_fields = ['id', 'created_by', 'created_at']

    def get_supplier_display(self, obj):
        if obj.supplier:
            return obj.supplier.name_bn or obj.supplier.name_en
        return obj.supplier_name or ''


class StockAdjustSerializer(serializers.Serializer):
    movement_type  = serializers.ChoiceField(choices=['PURCHASE', 'ADJUSTMENT', 'SUPPLIER_RETURN'])
    quantity       = serializers.DecimalField(max_digits=12, decimal_places=3)
    unit_cost      = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=Decimal('0'))
    unit_price     = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True, default=None)
    supplier_id    = serializers.UUIDField(required=False, allow_null=True, default=None)
    supplier_name  = serializers.CharField(required=False, allow_blank=True, default='')
    payment_method = serializers.ChoiceField(choices=['CASH', 'CREDIT'], required=False, default='CASH')
    note_bn        = serializers.CharField(required=False, allow_blank=True, default='')
    note_en        = serializers.CharField(required=False, allow_blank=True, default='')

    def validate_quantity(self, value):
        if value == 0:
            raise serializers.ValidationError('Quantity cannot be zero')
        return value

    def validate(self, data):
        if data['movement_type'] in ('PURCHASE', 'SUPPLIER_RETURN') and data.get('unit_cost', Decimal('0')) <= 0:
            raise serializers.ValidationError({'unit_cost': 'Buying price must be greater than zero for purchases and supplier returns'})
        return data
