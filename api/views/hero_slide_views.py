import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated

from api.models import HeroSlide
from api.serializers.hero_slide_serializers import HeroSlideSerializer
from api.permissions import IsAdmin
from api.utils.response import ApiResponse

logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([AllowAny])
def list_hero_slides(request):
    try:
        qs = HeroSlide.objects.filter(is_active=True)
        return ApiResponse(message="Slides retrieved", data=HeroSlideSerializer(qs, many=True, context={'request': request}).data)
    except Exception as e:
        return ApiResponse(message=str(e), errors=str(e), status_code=500)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def list_all_hero_slides(request):
    try:
        qs = HeroSlide.objects.all()
        return ApiResponse(message="Slides retrieved", data=HeroSlideSerializer(qs, many=True, context={'request': request}).data)
    except Exception as e:
        return ApiResponse(message=str(e), errors=str(e), status_code=500)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAdmin])
def create_hero_slide(request):
    serializer = HeroSlideSerializer(data=request.data, context={'request': request})
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    try:
        slide = serializer.save()
        return ApiResponse(message="Slide created", data=HeroSlideSerializer(slide, context={'request': request}).data, status_code=201)
    except Exception as e:
        return ApiResponse(message=str(e), errors=str(e), status_code=400)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, IsAdmin])
def update_hero_slide(request, pk):
    try:
        slide = HeroSlide.objects.get(id=pk)
    except HeroSlide.DoesNotExist:
        return ApiResponse(message="Not found", errors="Not found", status_code=404)
    serializer = HeroSlideSerializer(slide, data=request.data, partial=True, context={'request': request})
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    try:
        updated = serializer.save()
        return ApiResponse(message="Slide updated", data=HeroSlideSerializer(updated, context={'request': request}).data)
    except Exception as e:
        return ApiResponse(message=str(e), errors=str(e), status_code=400)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, IsAdmin])
def delete_hero_slide(_request, pk):
    try:
        HeroSlide.objects.get(id=pk).delete()
        return ApiResponse(message="Slide deleted")
    except HeroSlide.DoesNotExist:
        return ApiResponse(message="Not found", errors="Not found", status_code=404)
