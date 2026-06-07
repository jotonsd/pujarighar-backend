from rest_framework import serializers
from api.models import Review


class ReviewSerializer(serializers.ModelSerializer):
    user_name        = serializers.SerializerMethodField()
    product_id       = serializers.UUIDField(source='product.id', read_only=True)
    product_name_bn  = serializers.CharField(source='product.name_bn', read_only=True)
    product_name_en  = serializers.CharField(source='product.name_en', read_only=True)

    def get_user_name(self, obj):
        p = getattr(obj.user, 'profile', None)
        return (p.full_name_bn or p.full_name_en) if p else obj.user.email

    class Meta:
        model  = Review
        fields = [
            'id', 'product_id', 'product_name_bn', 'product_name_en',
            'order', 'user', 'user_name',
            'rating', 'comment', 'is_approved', 'created_at',
        ]
        read_only_fields = ['id', 'user', 'is_approved', 'created_at']


class ReviewCreateSerializer(serializers.Serializer):
    product_id = serializers.UUIDField()
    order_id   = serializers.UUIDField()
    rating     = serializers.IntegerField(min_value=1, max_value=5)
    comment    = serializers.CharField(allow_blank=True, default='')
