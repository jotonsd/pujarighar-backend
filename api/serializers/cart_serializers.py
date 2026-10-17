from decimal import Decimal
from rest_framework import serializers
from api.models import Cart, CartItem, Product


def _channel_from(request) -> str:
    # Same X-Client-Platform convention used for PromoCode/first-order-
    # discount gating — reused here so a Discount.scope='WEBSITE'/
    # 'MOBILE_APP' row only applies on the matching channel's cart pricing.
    header = request.headers.get('X-Client-Platform') if request else None
    return 'MOBILE_APP' if header == 'mobile_app' else 'WEBSITE'


class CartItemSerializer(serializers.ModelSerializer):
    product_name_bn     = serializers.CharField(source='product.name_bn', read_only=True)
    product_name_en     = serializers.CharField(source='product.name_en', read_only=True)
    unit_price          = serializers.SerializerMethodField()
    original_unit_price = serializers.SerializerMethodField()
    stock_on_hand       = serializers.SerializerMethodField()
    line_total          = serializers.SerializerMethodField()
    is_package          = serializers.BooleanField(source='product.is_package', read_only=True)
    package_items       = serializers.SerializerMethodField()
    product_image       = serializers.SerializerMethodField()
    weight_kg           = serializers.SerializerMethodField()
    has_free_delivery   = serializers.SerializerMethodField()

    class Meta:
        model  = CartItem
        fields = ['id', 'product', 'product_name_bn', 'product_name_en', 'variant', 'variant_label_bn', 'variant_label_en',
                  'unit_price', 'original_unit_price', 'quantity', 'line_total', 'stock_on_hand',
                  'is_package', 'package_items', 'product_image', 'weight_kg', 'has_free_delivery']
        read_only_fields = ['id']

    def get_weight_kg(self, obj):
        # Per-unit weight — the app sums weight_kg * quantity across lines to
        # get the cart's total weight for a live delivery-charge quote (see
        # CheckoutService._cart_weight for the server-side equivalent).
        return str(obj.product.weight_kg or Decimal('0'))

    def get_has_free_delivery(self, obj):
        # Lets the app exclude this line's weight from its own client-side
        # delivery-charge quote (cart_provider.dart's totalWeightKg), same
        # reasoning as CartSerializer.get_weight_kg's own exclusion for the
        # web cart preview — see CheckoutService._chargeable_weight for the
        # server-side source of truth this mirrors.
        channel = _channel_from(self.context.get('request'))
        obj.product._channel = channel
        return obj.product.has_free_delivery()

    def get_product_image(self, obj):
        img = None
        if obj.variant_id and obj.product.visual_attribute_type_id:
            # Find this cart line's variant's value for whichever attribute
            # type the product's photos are organized by (almost always
            # Color) — same resolution SalesOrderItemSerializer uses, so a
            # cart line shows the same photo its eventual order line will.
            value_id = next(
                (
                    av.attribute_value_id for av in obj.variant.attribute_values.all()
                    if av.attribute_value.attribute_type_id == obj.product.visual_attribute_type_id
                ),
                None,
            )
            if value_id:
                img = next((i for i in obj.product.images.all() if i.visual_value_id == value_id), None)
        if not img:
            img = obj.product.images.order_by('order').first()
        if not img:
            return None
        request = self.context.get('request')
        return request.build_absolute_uri(img.image.url) if request else img.image.url

    def get_stock_on_hand(self, obj):
        return str(obj.variant.stock_on_hand if obj.variant_id else obj.product.stock_on_hand)

    def get_unit_price(self, obj):
        obj.product._channel = _channel_from(self.context.get('request'))
        return str(obj.variant.effective_price if obj.variant_id else obj.product.effective_price)

    def get_original_unit_price(self, obj):
        return str(obj.product.original_price)

    def get_line_total(self, obj):
        obj.product._channel = _channel_from(self.context.get('request'))
        unit_price = obj.variant.effective_price if obj.variant_id else obj.product.effective_price
        return str(unit_price * obj.quantity)

    def get_package_items(self, obj):
        if not obj.product.is_package:
            return []
        return [
            {
                'component_name_bn': pi.component.name_bn,
                'component_name_en': pi.component.name_en,
                'quantity':          str(pi.quantity),
            }
            for pi in obj.product.package_items.select_related('component').all()
        ]


class CartSerializer(serializers.ModelSerializer):
    items           = CartItemSerializer(many=True, read_only=True)
    subtotal        = serializers.SerializerMethodField()
    discount_amount = serializers.SerializerMethodField()
    item_count      = serializers.SerializerMethodField()
    weight_kg       = serializers.SerializerMethodField()
    all_free_delivery = serializers.SerializerMethodField()

    class Meta:
        model  = Cart
        fields = ['id', 'items', 'subtotal', 'discount_amount', 'item_count', 'weight_kg', 'all_free_delivery']

    def get_weight_kg(self, obj):
        # Same calculation checkout uses to price delivery (see
        # CheckoutService._chargeable_weight) — an item whose product
        # currently has an active free_delivery Discount has its weight
        # excluded, same reasoning as there, so this cart total feeds the
        # delivery-charge preview (CartClient's useGetDeliveryChargesQuery)
        # with the number that actually matches what checkout will charge,
        # not the item's real physical weight.
        channel = _channel_from(self.context.get('request'))
        total = Decimal('0')
        for item in obj.items.all():
            item.product._channel = channel
            if not item.product.has_free_delivery():
                total += (item.product.weight_kg or Decimal('0')) * item.quantity
        return str(total)

    def get_all_free_delivery(self, obj):
        # A 0kg weight_kg above doesn't guarantee DeliveryCharge.charge_for()
        # returns ৳0 — its configured tiers can have a nonzero base bracket
        # (e.g. ৳70 for 0–0.2kg) — so CheckoutService._all_free_delivery's
        # explicit ৳0 override needs a matching signal here, or the cart
        # preview would show a charge checkout won't actually apply.
        channel = _channel_from(self.context.get('request'))
        items = list(obj.items.all())
        if not items:
            return False
        for item in items:
            item.product._channel = channel
        return all(item.product.has_free_delivery() for item in items)

    def get_subtotal(self, obj):
        channel = _channel_from(self.context.get('request'))
        subtotal = Decimal('0')
        for item in obj.items.all():
            item.product._channel = channel
            subtotal += item.product.effective_price * item.quantity
        return str(subtotal)

    def get_discount_amount(self, obj):
        # Product-level discount only — a promo code's discount isn't known
        # until the customer actually enters one at checkout (see
        # api.services.promo_service), so it can't be previewed here the
        # way the old always-on mobile_app_order_discount_percent was.
        channel = _channel_from(self.context.get('request'))
        total = Decimal('0')
        for item in obj.items.all():
            item.product._channel = channel
            total += (item.product.original_price - item.product.effective_price) * item.quantity
        return str(max(total, Decimal('0')))

    def get_item_count(self, obj):
        # Total units across all lines, not the number of distinct products —
        # adding 4 of one product should read as 4, not 1. Cast off Decimal so
        # this serializes as a JSON number (the frontend types it as `number`),
        # not a string like the other Decimal-backed fields on this serializer.
        return float(sum(item.quantity for item in obj.items.all()))


class AddToCartSerializer(serializers.Serializer):
    product_id = serializers.UUIDField()
    quantity   = serializers.DecimalField(max_digits=10, decimal_places=3, min_value=Decimal('0.001'))
    variant_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    # Transitional bridge for callers still sending a plain color string
    # instead of variant_id (e.g. the AI chatbot) — see
    # Product.resolve_color_variant. The storefront now sends variant_id
    # directly once a product has any variants.
    color_bn   = serializers.CharField(required=False, allow_blank=True, default='', max_length=40)
    color_en   = serializers.CharField(required=False, allow_blank=True, default='', max_length=40)

    def validate(self, data):
        try:
            product = Product.objects.get(id=data['product_id'], is_active=True)
        except Product.DoesNotExist:
            raise serializers.ValidationError({
                'message_bn': 'পণ্য পাওয়া যায়নি',
                'message_en': 'Product not found',
            })
        data['product'] = product
        if data.get('variant_id'):
            variant = product.variants.filter(id=data['variant_id']).first()
            if not variant:
                raise serializers.ValidationError({
                    'message_bn': 'ভ্যারিয়েন্ট পাওয়া যায়নি',
                    'message_en': 'Variant not found',
                })
            data['variant'] = variant
        else:
            data['variant'] = product.resolve_color_variant(data.get('color_bn', ''), data.get('color_en', ''))
        return data


class UpdateCartItemSerializer(serializers.Serializer):
    quantity = serializers.DecimalField(max_digits=10, decimal_places=3, min_value=Decimal('0.001'))
