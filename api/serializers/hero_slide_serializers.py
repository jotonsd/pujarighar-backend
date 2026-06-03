from rest_framework import serializers
from api.models import HeroSlide


class HeroSlideSerializer(serializers.ModelSerializer):
    class Meta:
        model  = HeroSlide
        fields = ['id', 'title_bn', 'title_en', 'subtitle_bn', 'subtitle_en',
                  'cta_label_bn', 'cta_label_en', 'cta_link', 'image',
                  'bg_color', 'order', 'is_active', 'created_at']
        read_only_fields = ['id', 'created_at']
