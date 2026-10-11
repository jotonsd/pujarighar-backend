from rest_framework import serializers
from api.models import PromoPopup


class PromoPopupSerializer(serializers.ModelSerializer):
    # Intrinsic pixel dimensions of the uploaded image — same reasoning as
    # BannerSerializer's own image_width/image_height: lets the frontend
    # reserve the right box before the image loads, avoiding layout shift.
    image_width  = serializers.SerializerMethodField()
    image_height = serializers.SerializerMethodField()

    class Meta:
        model  = PromoPopup
        fields = ['id', 'image', 'image_width', 'image_height',
                  'link', 'valid_from', 'valid_until', 'order', 'is_active',
                  'created_at', 'updated_at']
        read_only_fields = ['id', 'image_width', 'image_height', 'created_at', 'updated_at']

    def get_image_width(self, obj):
        try:
            return obj.image.width if obj.image else None
        except (FileNotFoundError, OSError):
            return None

    def get_image_height(self, obj):
        try:
            return obj.image.height if obj.image else None
        except (FileNotFoundError, OSError):
            return None
