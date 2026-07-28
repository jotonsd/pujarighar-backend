from rest_framework import serializers
from api.models import BlogPost


class BlogPostSerializer(serializers.ModelSerializer):
    created_by_email = serializers.EmailField(source='created_by.email', read_only=True, default=None)

    class Meta:
        model  = BlogPost
        fields = [
            'id', 'slug', 'title_bn', 'title_en',
            'seo_title_bn', 'seo_title_en', 'meta_description_bn', 'meta_description_en',
            'focus_keyword', 'canonical_url',
            'body_bn', 'body_en', 'cover_image',
            'is_active', 'published_at', 'created_by', 'created_by_email',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'slug', 'created_by', 'created_at', 'updated_at']
