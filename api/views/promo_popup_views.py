import logging
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated

from api.models import PromoPopup
from api.serializers.promo_popup_serializers import PromoPopupSerializer
from api.permissions import has_permission
from api.utils.response import ApiResponse

logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([AllowAny])
def get_active_promo_popup(request):
    """Resolves the ONE popup to show right now, if any — is_active, within
    [valid_from, valid_until] when set, highest-priority `order` first. Not
    cached (unlike list_banners) since this is one cheap indexed query and
    needs to react immediately to an admin flipping is_active off."""
    try:
        now = timezone.now()
        popup = None
        for p in PromoPopup.objects.filter(is_active=True).order_by('order', '-created_at'):
            if p.valid_from and now < p.valid_from:
                continue
            if p.valid_until and now > p.valid_until:
                continue
            popup = p
            break
        data = PromoPopupSerializer(popup, context={'request': request}).data if popup else None
        return ApiResponse(message="Active popup retrieved", data=data)
    except Exception as e:
        return ApiResponse(message=str(e), errors=str(e), status_code=500)


@api_view(['GET'])
@permission_classes([IsAuthenticated, has_permission('promo_popups', 'view')])
def list_all_promo_popups(request):
    try:
        qs = PromoPopup.objects.all()
        return ApiResponse(message="Promo popups retrieved", data=PromoPopupSerializer(qs, many=True, context={'request': request}).data)
    except Exception as e:
        return ApiResponse(message=str(e), errors=str(e), status_code=500)


@api_view(['POST'])
@permission_classes([IsAuthenticated, has_permission('promo_popups', 'create')])
def create_promo_popup(request):
    serializer = PromoPopupSerializer(data=request.data, context={'request': request})
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    try:
        popup = serializer.save()
        return ApiResponse(message="Promo popup created", data=PromoPopupSerializer(popup, context={'request': request}).data, status_code=201)
    except Exception as e:
        return ApiResponse(message=str(e), errors=str(e), status_code=400)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, has_permission('promo_popups', 'edit')])
def update_promo_popup(request, pk):
    try:
        popup = PromoPopup.objects.get(id=pk)
    except PromoPopup.DoesNotExist:
        return ApiResponse(message="Not found", errors="Not found", status_code=404)

    if request.data.get('clear_image') == '1' and not request.FILES.get('image'):
        if popup.image:
            popup.image.delete(save=False)
        popup.image = None
        popup.save(update_fields=['image'])

    serializer = PromoPopupSerializer(popup, data=request.data, partial=True, context={'request': request})
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    try:
        updated = serializer.save()
        return ApiResponse(message="Promo popup updated", data=PromoPopupSerializer(updated, context={'request': request}).data)
    except Exception as e:
        return ApiResponse(message=str(e), errors=str(e), status_code=400)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, has_permission('promo_popups', 'delete')])
def delete_promo_popup(_request, pk):
    try:
        PromoPopup.objects.get(id=pk).delete()
        return ApiResponse(message="Promo popup deleted")
    except PromoPopup.DoesNotExist:
        return ApiResponse(message="Not found", errors="Not found", status_code=404)
