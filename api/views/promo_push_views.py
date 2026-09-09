import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from api.models import PromoPush, DeviceToken, Notification
from api.permissions import has_permission
from api.services.push_service import send_promo_push
from api.services.notification_ws import broadcast_notifications
from api.utils.response import ApiResponse
from api.utils.pagination import paginate_queryset

logger = logging.getLogger(__name__)


def _serialize(p: PromoPush) -> dict:
    return {
        'id':               str(p.id),
        'title_bn':         p.title_bn,
        'title_en':         p.title_en,
        'body_bn':          p.body_bn,
        'body_en':          p.body_en,
        'sent_by':          p.sent_by.email if p.sent_by else None,
        'recipient_count':  p.recipient_count,
        'created_at':       p.created_at.isoformat(),
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated, has_permission('promo_notifications', 'view')])
def list_promo_pushes(request):
    qs = PromoPush.objects.select_related('sent_by').all()
    page_data, pagination = paginate_queryset(qs, request, default_page_size=20)
    return ApiResponse(
        message="Promotional pushes retrieved",
        data=[_serialize(p) for p in page_data],
        pagination=pagination,
    )


@api_view(['POST'])
@permission_classes([IsAuthenticated, has_permission('promo_notifications', 'create')])
def send_promo_push_view(request):
    title_bn = (request.data.get('title_bn') or '').strip()
    title_en = (request.data.get('title_en') or '').strip()
    body_bn  = (request.data.get('body_bn') or '').strip()
    body_en  = (request.data.get('body_en') or '').strip()
    if not title_bn or not title_en:
        return ApiResponse(message="Title is required", errors="title_bn and title_en are required", status_code=422)

    # Every device that's ever opened the app, logged in or not — a
    # promotional push has to reach guests too, not just accounts. The
    # in-app Notification row is different: it lives in a specific
    # account's notification list, so only devices actually attached to an
    # account get one (guests have no account/notification center for it
    # to live in — the OS-level push is all they get, which is the point).
    device_count = DeviceToken.objects.count()
    if not device_count:
        return ApiResponse(message="No app installs to notify yet", errors="No registered devices", status_code=422)

    user_ids = list(
        DeviceToken.objects.exclude(user_id=None).values_list('user_id', flat=True).distinct()
    )
    notifications = [
        Notification(
            user_id=uid, title_bn=title_bn, title_en=title_en, body_bn=body_bn, body_en=body_en,
            reference_type='PROMOTIONAL',
        )
        for uid in user_ids
    ]
    Notification.objects.bulk_create(notifications)
    broadcast_notifications(notifications)
    send_promo_push(title_bn, body_bn, data={'reference_type': 'PROMOTIONAL'})

    campaign = PromoPush.objects.create(
        title_bn=title_bn, title_en=title_en, body_bn=body_bn, body_en=body_en,
        sent_by=request.user, recipient_count=device_count,
    )
    logger.info(f"Promo push '{title_en}' sent by {request.user.email} to {device_count} devices")
    return ApiResponse(message="Promotional notification sent", data=_serialize(campaign), status_code=201)
