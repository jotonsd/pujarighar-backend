import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from api.models import Notification
from api.utils.response import ApiResponse
from api.utils.pagination import paginate_queryset

logger = logging.getLogger(__name__)


def _serialize(n: Notification) -> dict:
    return {
        'id':             str(n.id),
        'title_bn':       n.title_bn,
        'title_en':       n.title_en,
        'body_bn':        n.body_bn,
        'body_en':        n.body_en,
        'is_read':        n.is_read,
        'reference_type': n.reference_type,
        'reference_id':   str(n.reference_id) if n.reference_id else None,
        'created_at':     n.created_at.isoformat(),
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_notifications(request):
    qs = Notification.objects.filter(user=request.user)[:30]
    unread_count = Notification.objects.filter(user=request.user, is_read=False).count()
    data = [_serialize(n) for n in qs]
    return ApiResponse(message="Notifications retrieved", data={'notifications': data, 'unread_count': unread_count})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_all_notifications(request):
    qs = Notification.objects.filter(user=request.user)
    is_read = request.query_params.get('is_read')
    if is_read is not None:
        qs = qs.filter(is_read=is_read.lower() == 'true')
    page_data, pagination = paginate_queryset(qs, request, default_page_size=20)
    return ApiResponse(
        message="Notifications retrieved",
        data=[_serialize(n) for n in page_data],
        pagination=pagination,
    )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def mark_all_read(request):
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    return ApiResponse(message="All notifications marked as read")


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def mark_one_read(request, pk):
    try:
        n = Notification.objects.get(pk=pk, user=request.user)
        n.is_read = True
        n.save(update_fields=['is_read'])
        return ApiResponse(message="Notification marked as read")
    except Notification.DoesNotExist:
        return ApiResponse(message="Not found", errors="Not found", status_code=404)
