from decimal import Decimal
from rest_framework import serializers
from django.db.models import Q, Sum
from django.utils import timezone
from api.models import (
    PRODUCT_BADGES, Brand, Category, Product, ProductImage, ProductPackageItem, StockMovement,
    Supplier, SupplierPayment, VariantAttributeType, VariantAttributeValue, ProductVariant,
)


class VariantAttributeTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model  = VariantAttributeType
        fields = ['id', 'name_bn', 'name_en', 'code', 'has_bilingual_values', 'is_active']


class VariantAttributeValueSerializer(serializers.ModelSerializer):
    attribute_type_code = serializers.CharField(source='attribute_type.code', read_only=True)

    class Meta:
        model  = VariantAttributeValue
        fields = ['id', 'attribute_type', 'attribute_type_code', 'value_bn', 'value_en', 'is_active']


class ProductVariantSerializer(serializers.ModelSerializer):
    effective_price = serializers.SerializerMethodField()
    stock_on_hand   = serializers.SerializerMethodField()
    label_bn        = serializers.SerializerMethodField()
    label_en        = serializers.SerializerMethodField()
    attribute_values = serializers.SerializerMethodField()

    class Meta:
        model  = ProductVariant
        fields = ['id', 'product', 'sku_suffix', 'price_override', 'effective_price',
                  'stock_on_hand', 'is_active', 'label_bn', 'label_en', 'attribute_values']

    def get_effective_price(self, obj):
        return str(obj.effective_price)

    def get_stock_on_hand(self, obj):
        return str(obj.stock_on_hand)

    def get_label_bn(self, obj):
        return obj.label(True)

    def get_label_en(self, obj):
        return obj.label(False)

    def get_attribute_values(self, obj):
        return [
            {
                'attribute_type_code': av.attribute_value.attribute_type.code,
                'attribute_type_name_bn': av.attribute_value.attribute_type.name_bn,
                'attribute_type_name_en': av.attribute_value.attribute_type.name_en,
                'value_id': str(av.attribute_value_id),
                'value_bn': av.attribute_value.value_bn,
                'value_en': av.attribute_value.value_en,
            }
            for av in obj.attribute_values.select_related('attribute_value', 'attribute_value__attribute_type')
                .order_by('attribute_value__attribute_type__code')
        ]


class AttributeTypeWriteSerializer(serializers.Serializer):
    name_bn = serializers.CharField(max_length=40)
    name_en = serializers.CharField(max_length=40)
    code    = serializers.SlugField(max_length=50)
    has_bilingual_values = serializers.BooleanField(default=False)

    def validate_code(self, value):
        if VariantAttributeType.objects.filter(code=value).exists():
            raise serializers.ValidationError({'message_bn': 'এই কোড ইতিমধ্যে ব্যবহৃত', 'message_en': 'This code is already in use'})
        return value


class AttributeValueWriteSerializer(serializers.Serializer):
    attribute_type_id = serializers.UUIDField()
    value_bn = serializers.CharField(max_length=40, required=False, allow_blank=True, default='')
    value_en = serializers.CharField(max_length=40)

    def validate_attribute_type_id(self, value):
        if not VariantAttributeType.objects.filter(id=value, is_active=True).exists():
            raise serializers.ValidationError({'message_bn': 'ধরন পাওয়া যায়নি', 'message_en': 'Attribute type not found'})
        return value


class AttributeTypeUpdateSerializer(serializers.Serializer):
    name_bn = serializers.CharField(max_length=40, required=False)
    name_en = serializers.CharField(max_length=40, required=False)
    has_bilingual_values = serializers.BooleanField(required=False)
    is_active = serializers.BooleanField(required=False)


class AttributeValueUpdateSerializer(serializers.Serializer):
    value_bn = serializers.CharField(max_length=40, required=False, allow_blank=True)
    value_en = serializers.CharField(max_length=40, required=False)
    is_active = serializers.BooleanField(required=False)


class GenerateVariantsSerializer(serializers.Serializer):
    value_ids = serializers.ListField(child=serializers.UUIDField(), min_length=1)


class VariantUpdateSerializer(serializers.Serializer):
    sku_suffix     = serializers.CharField(max_length=20, required=False, allow_blank=True)
    price_override = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    is_active      = serializers.BooleanField(required=False)


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model  = Category
        fields = [
            'id', 'name_bn', 'name_en', 'slug', 'parent', 'icon', 'order', 'is_active', 'created_at',
            'seo_title_bn', 'seo_title_en', 'meta_description_bn', 'meta_description_en',
            'description_bn', 'description_en',
        ]


class BrandSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Brand
        fields = ['id', 'name_bn', 'name_en', 'slug', 'logo', 'is_active', 'created_at']


class ProductImageSerializer(serializers.ModelSerializer):
    visual_value_bn = serializers.CharField(source='visual_value.value_bn', read_only=True, default='')
    visual_value_en = serializers.CharField(source='visual_value.value_en', read_only=True, default='')

    class Meta:
        model  = ProductImage
        fields = ['id', 'image', 'alt_bn', 'alt_en', 'order', 'visual_value', 'visual_value_bn', 'visual_value_en']


class PackageItemReadSerializer(serializers.ModelSerializer):
    component_id      = serializers.UUIDField(source='component.id', read_only=True)
    component_name_bn = serializers.CharField(source='component.name_bn', read_only=True)
    component_name_en = serializers.CharField(source='component.name_en', read_only=True)
    component_sku     = serializers.CharField(source='component.sku', read_only=True)
    component_image   = serializers.SerializerMethodField()
    unit_price        = serializers.DecimalField(source='component.unit_price', max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model  = ProductPackageItem
        fields = ['id', 'component_id', 'component_name_bn', 'component_name_en', 'component_sku', 'component_image', 'quantity', 'unit_price']

    def get_component_image(self, obj):
        img = obj.component.images.first()
        if not img:
            return None
        request = self.context.get('request')
        url = img.image.url
        return request.build_absolute_uri(url) if request else url


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
    variants             = ProductVariantSerializer(many=True, read_only=True)
    variant_attribute_types = serializers.SerializerMethodField()
    visual_attribute_type_code = serializers.CharField(source='visual_attribute_type.code', read_only=True, default=None)
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
    can_delete            = serializers.SerializerMethodField()

    def get_can_delete(self, obj):
        # Only annotated for the admin Product List (ProductService.
        # list_products' include_inactive branch) — None everywhere else,
        # since the storefront/detail fetch never needs this.
        return getattr(obj, '_can_delete', None)

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

    def get_variant_attribute_types(self, obj):
        # Distinct attribute types this product's variants actually use
        # (e.g. ['color', 'size']) — drives which generic pill-rows the
        # storefront renders, without re-deriving it from the nested
        # variants list every time.
        codes = []
        seen = set()
        for variant in obj.variants.all():
            for av in variant.attribute_values.all():
                code = av.attribute_value.attribute_type.code
                if code not in seen:
                    seen.add(code)
                    codes.append(code)
        return codes

    def validate_badges(self, value):
        invalid = set(value) - set(PRODUCT_BADGES)
        if invalid:
            raise serializers.ValidationError(f'Unknown badge(s): {", ".join(invalid)}')
        return value

    def to_representation(self, instance):
        # 'new' is dropped from the OUTPUT once its 2-week window has
        # elapsed (see Product.effective_badges/new_badge_active) — the
        # stored field itself is untouched here, so a later save that
        # doesn't re-include 'new' in the submitted badges (because the
        # admin form now shows it unchecked) is what actually clears it,
        # not this read path.
        data = super().to_representation(instance)
        data['badges'] = instance.effective_badges()
        return data

    class Meta:
        model  = Product
        fields = [
            'id', 'slug', 'name_bn', 'name_en',
            'description_bn', 'description_en',
            'sku', 'category', 'category_name_bn', 'category_name_en',
            'brand', 'brand_name_bn', 'brand_name_en',
            'unit_price', 'cost_price', 'effective_price', 'original_price',
            'active_discount_type', 'active_discount_value',
            'unit_bn', 'unit_en', 'weight_kg',
            'is_package', 'discount_type', 'discount_value', 'is_active', 'badges',
            'stock_on_hand', 'images', 'package_items', 'variants', 'variant_attribute_types', 'visual_attribute_type_code',
            'average_rating', 'review_count', 'can_delete',
            'seo_title_bn', 'seo_title_en', 'meta_description_bn', 'meta_description_en',
            'focus_keyword', 'canonical_url',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'slug', 'cost_price', 'created_at', 'updated_at']


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
    variant_label_bn = serializers.SerializerMethodField()
    variant_label_en = serializers.SerializerMethodField()

    class Meta:
        model  = StockMovement
        fields = [
            'id', 'product', 'variant', 'variant_label_bn', 'variant_label_en',
            'movement_type', 'quantity', 'unit_cost',
            'supplier', 'supplier_name', 'supplier_display', 'payment_method',
            'reference_id', 'note_bn', 'note_en',
            'created_by', 'created_by_email', 'created_at',
        ]
        read_only_fields = ['id', 'created_by', 'created_at']

    def get_supplier_display(self, obj):
        if obj.supplier:
            return obj.supplier.name_bn or obj.supplier.name_en
        return obj.supplier_name or ''

    def get_variant_label_bn(self, obj):
        return obj.variant.label(True) if obj.variant_id else ''

    def get_variant_label_en(self, obj):
        return obj.variant.label(False) if obj.variant_id else ''


class StockAdjustSerializer(serializers.Serializer):
    movement_type  = serializers.ChoiceField(choices=['PURCHASE', 'ADJUSTMENT', 'SUPPLIER_RETURN'])
    quantity       = serializers.DecimalField(max_digits=12, decimal_places=3)
    variant_id     = serializers.UUIDField(required=False, allow_null=True, default=None)
    unit_cost      = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=Decimal('0'))
    unit_price     = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True, default=None)
    supplier_id    = serializers.UUIDField(required=False, allow_null=True, default=None)
    supplier_name  = serializers.CharField(required=False, allow_blank=True, default='')
    payment_method = serializers.ChoiceField(choices=['CASH', 'CREDIT'], required=False, default='CASH')
    # When the entry is being logged after the fact (e.g. purchased
    # yesterday, entered today) — defaults to today if left out.
    date           = serializers.DateField(required=False, allow_null=True, default=None)
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


class StockMovementUpdateSerializer(serializers.Serializer):
    """Corrects a mistaken PURCHASE/SUPPLIER_RETURN entry — deliberately does
    NOT accept movement_type/product (those never change on an edit, only
    the numbers that were entered wrong). variant_id DOES change on an edit
    (unlike product) — it's for the real case of picking the wrong color/
    size when the entry was first made; empty string reassigns to no
    variant (the product-level ledger)."""
    quantity       = serializers.DecimalField(max_digits=12, decimal_places=3, required=False)
    variant_id     = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    unit_cost      = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    unit_price     = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    supplier_id    = serializers.UUIDField(required=False, allow_null=True)
    supplier_name  = serializers.CharField(required=False, allow_blank=True)
    payment_method = serializers.ChoiceField(choices=['CASH', 'CREDIT'], required=False)
    date           = serializers.DateField(required=False, allow_null=True)
    note_bn        = serializers.CharField(required=False, allow_blank=True)
    note_en        = serializers.CharField(required=False, allow_blank=True)

    def validate_quantity(self, value):
        if value == 0:
            raise serializers.ValidationError('Quantity cannot be zero')
        return value

    def validate_unit_cost(self, value):
        if value <= 0:
            raise serializers.ValidationError('Buying price must be greater than zero')
        return value
