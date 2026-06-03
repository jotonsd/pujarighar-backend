from rest_framework import serializers
from api.models import ShippingAddress


class ShippingAddressSerializer(serializers.ModelSerializer):
    class Meta:
        model  = ShippingAddress
        fields = [
            'id', 'label',
            'full_name_bn', 'full_name_en',
            'phone',
            'address_bn', 'address_en',
            'district', 'thana', 'post_code',
            'is_default',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'is_default', 'created_at', 'updated_at']
