from decimal import Decimal
from rest_framework import serializers
from api.models import Product


class GuestCartItemSerializer(serializers.Serializer):
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


class GuestCheckoutSerializer(serializers.Serializer):
    items            = GuestCartItemSerializer(many=True, min_length=1)
    name_bn          = serializers.CharField(max_length=200)
    name_en          = serializers.CharField(required=False, allow_blank=True, default='')
    phone            = serializers.CharField(max_length=15)
    email            = serializers.EmailField(required=False, allow_blank=True, default='')
    address_bn       = serializers.CharField()
    address_en       = serializers.CharField(required=False, allow_blank=True, default='')
    district         = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    thana            = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    post_code        = serializers.CharField(max_length=10,  required=False, allow_blank=True, default='')
    notes_bn         = serializers.CharField(required=False, allow_blank=True, default='')
    payment_method   = serializers.ChoiceField(choices=['COD'], default='COD')
    apply_delivery   = serializers.BooleanField(default=True)
    delivery_zone    = serializers.ChoiceField(choices=['inside', 'outside'], required=False, allow_null=True, default=None)


class POSCheckoutSerializer(GuestCheckoutSerializer):
    """POS-only: adds a staff-applied order discount on top of the guest
    checkout fields. Never exposed on the public guest checkout endpoint —
    only `pos_create_order` (admin/warehouse, authenticated) uses this.
    """
    discount_type  = serializers.ChoiceField(choices=['NONE', 'PERCENTAGE', 'FLAT'], required=False, default='NONE')
    discount_value = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=Decimal('0'), min_value=Decimal('0'))

    def validate(self, data):
        if data.get('discount_type') == 'PERCENTAGE' and data.get('discount_value', Decimal('0')) > 100:
            raise serializers.ValidationError({'discount_value': 'Percentage discount cannot exceed 100'})
        return data
