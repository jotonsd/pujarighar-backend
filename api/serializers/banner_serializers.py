from rest_framework import serializers
from api.models import Banner


class BannerSerializer(serializers.ModelSerializer):
    # Intrinsic pixel dimensions of the uploaded image — Django's ImageField
    # already reads these off the file lazily (Pillow), no extra column
    # needed. Exposed so the frontend can set real width/height attributes
    # on <img>, letting the browser reserve the correct box before the image
    # loads instead of shifting layout in after (CLS).
    image_width  = serializers.SerializerMethodField()
    image_height = serializers.SerializerMethodField()

    class Meta:
        model  = Banner
        fields = ['id', 'title_bn', 'title_en', 'subtitle_bn', 'subtitle_en',
                  'badge_text', 'image', 'image_width', 'image_height',
                  'bg_color', 'link', 'order', 'is_active',
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
