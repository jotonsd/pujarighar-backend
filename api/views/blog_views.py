import logging
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated

from api.models import BlogPost
from api.serializers.blog_serializers import BlogPostSerializer
from api.permissions import has_permission
from api.utils.pagination import paginate_queryset
from api.utils.response import ApiResponse

logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([AllowAny])
def list_blog_posts(request):
    try:
        qs = BlogPost.objects.filter(is_active=True).order_by('-published_at', '-created_at')
        page_data, pagination = paginate_queryset(qs, request, default_page_size=12)
        return ApiResponse(
            message="Blog posts retrieved",
            data=BlogPostSerializer(page_data, many=True, context={'request': request}).data,
            pagination=pagination,
        )
    except Exception as e:
        logger.error(f"List blog posts error: {e}", exc_info=True)
        return ApiResponse(message=str(e), errors=str(e), status_code=500)


@api_view(['GET'])
@permission_classes([IsAuthenticated, has_permission('blog', 'view')])
def list_all_blog_posts(request):
    try:
        qs = BlogPost.objects.all().order_by('-created_at')
        page_data, pagination = paginate_queryset(qs, request)
        return ApiResponse(
            message="Blog posts retrieved",
            data=BlogPostSerializer(page_data, many=True, context={'request': request}).data,
            pagination=pagination,
        )
    except Exception as e:
        logger.error(f"List all blog posts error: {e}", exc_info=True)
        return ApiResponse(message=str(e), errors=str(e), status_code=500)


@api_view(['GET'])
@permission_classes([AllowAny])
def get_blog_post_by_slug(request, slug):
    try:
        post = BlogPost.objects.get(slug=slug, is_active=True)
        return ApiResponse(message="Blog post retrieved", data=BlogPostSerializer(post, context={'request': request}).data)
    except BlogPost.DoesNotExist:
        return ApiResponse(message="Blog post not found", errors="Not found", status_code=404)


@api_view(['GET'])
@permission_classes([IsAuthenticated, has_permission('blog', 'view')])
def get_blog_post(request, pk):
    try:
        post = BlogPost.objects.get(pk=pk)
        return ApiResponse(message="Blog post retrieved", data=BlogPostSerializer(post, context={'request': request}).data)
    except BlogPost.DoesNotExist:
        return ApiResponse(message="Blog post not found", errors="Not found", status_code=404)


@api_view(['POST'])
@permission_classes([IsAuthenticated, has_permission('blog', 'create')])
def create_blog_post(request):
    serializer = BlogPostSerializer(data=request.data, context={'request': request})
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    try:
        extra = {'created_by': request.user}
        if serializer.validated_data.get('is_active', True) and not request.data.get('published_at'):
            extra['published_at'] = timezone.now()
        post = serializer.save(**extra)
        return ApiResponse(message="Blog post created", data=BlogPostSerializer(post, context={'request': request}).data, status_code=201)
    except Exception as e:
        logger.error(f"Create blog post error: {e}", exc_info=True)
        return ApiResponse(message=str(e), errors=str(e), status_code=400)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, has_permission('blog', 'edit')])
def update_blog_post(request, pk):
    try:
        post = BlogPost.objects.get(pk=pk)
    except BlogPost.DoesNotExist:
        return ApiResponse(message="Blog post not found", errors="Not found", status_code=404)

    if request.data.get('clear_cover_image') == '1' and not request.FILES.get('cover_image'):
        if post.cover_image:
            post.cover_image.delete(save=False)
        post.cover_image = None
        post.save(update_fields=['cover_image'])

    serializer = BlogPostSerializer(post, data=request.data, partial=True, context={'request': request})
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    try:
        # First time a post is switched to active with no publish date yet, stamp it now.
        extra = {}
        if serializer.validated_data.get('is_active') and not post.published_at:
            extra['published_at'] = timezone.now()
        updated = serializer.save(**extra)
        return ApiResponse(message="Blog post updated", data=BlogPostSerializer(updated, context={'request': request}).data)
    except Exception as e:
        logger.error(f"Update blog post error: {e}", exc_info=True)
        return ApiResponse(message=str(e), errors=str(e), status_code=400)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, has_permission('blog', 'delete')])
def delete_blog_post(_request, pk):
    try:
        BlogPost.objects.get(pk=pk).delete()
        return ApiResponse(message="Blog post deleted")
    except BlogPost.DoesNotExist:
        return ApiResponse(message="Blog post not found", errors="Not found", status_code=404)
