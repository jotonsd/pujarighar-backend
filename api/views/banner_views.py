import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated

from api.models import Banner
from api.serializers.banner_serializers import BannerSerializer
from api.permissions import IsAdmin
from api.utils.response import ApiResponse

logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([AllowAny])
def list_banners(request):
    try:
        qs = Banner.objects.filter(is_active=True)
        return ApiResponse(message="Banners retrieved", data=BannerSerializer(qs, many=True, context={'request': request}).data)
    except Exception as e:
        return ApiResponse(message=str(e), errors=str(e), status_code=500)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def list_all_banners(request):
    try:
        qs = Banner.objects.all()
        return ApiResponse(message="Banners retrieved", data=BannerSerializer(qs, many=True, context={'request': request}).data)
    except Exception as e:
        return ApiResponse(message=str(e), errors=str(e), status_code=500)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAdmin])
def create_banner(request):
    serializer = BannerSerializer(data=request.data, context={'request': request})
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    try:
        banner = serializer.save()
        return ApiResponse(message="Banner created", data=BannerSerializer(banner, context={'request': request}).data, status_code=201)
    except Exception as e:
        return ApiResponse(message=str(e), errors=str(e), status_code=400)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, IsAdmin])
def update_banner(request, pk):
    try:
        banner = Banner.objects.get(id=pk)
    except Banner.DoesNotExist:
        return ApiResponse(message="Not found", errors="Not found", status_code=404)
    serializer = BannerSerializer(banner, data=request.data, partial=True, context={'request': request})
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    try:
        updated = serializer.save()
        return ApiResponse(message="Banner updated", data=BannerSerializer(updated, context={'request': request}).data)
    except Exception as e:
        return ApiResponse(message=str(e), errors=str(e), status_code=400)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, IsAdmin])
def delete_banner(_request, pk):
    try:
        Banner.objects.get(id=pk).delete()
        return ApiResponse(message="Banner deleted")
    except Banner.DoesNotExist:
        return ApiResponse(message="Not found", errors="Not found", status_code=404)
