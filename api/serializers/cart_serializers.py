from decimal import Decimal
from rest_framework import serializers
from api.models import Cart, CartItem, Product


class CartItemSerializer(serializers.ModelSerializer):
    product_name_bn = serializers.CharField(source='product.name_bn', read_only=True)
    product_name_en = serializers.CharField(source='product.name_en', read_only=True)
    unit_price      = serializers.DecimalField(source='product.unit_price', max_digits=12, decimal_places=2, read_only=True)
    stock_on_hand   = serializers.SerializerMethodField()
    line_total      = serializers.SerializerMethodField()
    is_package      = serializers.BooleanField(source='product.is_package', read_only=True)
    package_items   = serializers.SerializerMethodField()
    product_image   = serializers.SerializerMethodField()

    class Meta:
        model  = CartItem
        fields = ['id', 'product', 'product_name_bn', 'product_name_en',
                  'unit_price', 'quantity', 'line_total', 'stock_on_hand',
                  'is_package', 'package_items', 'product_image']
        read_only_fields = ['id']

    def get_product_image(self, obj):
        img = obj.product.images.order_by('order').first()
        if not img:
            return None
        request = self.context.get('request')
        return request.build_absolute_uri(img.image.url) if request else img.image.url

    def get_stock_on_hand(self, obj):
        return str(obj.product.stock_on_hand)

    def get_line_total(self, obj):
        return str(obj.product.unit_price * obj.quantity)

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
    items      = CartItemSerializer(many=True, read_only=True)
    subtotal   = serializers.SerializerMethodField()
    item_count = serializers.SerializerMethodField()

    class Meta:
        model  = Cart
        fields = ['id', 'items', 'subtotal', 'item_count']

    def get_subtotal(self, obj):
        return str(sum(item.product.unit_price * item.quantity for item in obj.items.all()))

    def get_item_count(self, obj):
        return obj.items.count()


class AddToCartSerializer(serializers.Serializer):
    product_id = serializers.UUIDField()
    quantity   = serializers.DecimalField(max_digits=10, decimal_places=3, min_value=Decimal('0.001'))

    def validate(self, data):
        try:
            product = Product.objects.get(id=data['product_id'], is_active=True)
        except Product.DoesNotExist:
            raise serializers.ValidationError({
                'message_bn': 'পণ্য পাওয়া যায়নি',
                'message_en': 'Product not found',
            })
        data['product'] = product
        return data


class UpdateCartItemSerializer(serializers.Serializer):
    quantity = serializers.DecimalField(max_digits=10, decimal_places=3, min_value=Decimal('0.001'))
