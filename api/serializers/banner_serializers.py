from rest_framework import serializers
from api.models import Banner


class BannerSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Banner
        fields = ['id', 'title_bn', 'title_en', 'subtitle_bn', 'subtitle_en',
                  'badge_text', 'image', 'bg_color', 'link', 'order', 'is_active',
                  'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']
