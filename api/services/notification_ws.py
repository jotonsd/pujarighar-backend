from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


def _serialize(notification) -> dict:
    return {
        'id': str(notification.id),
        'title_bn': notification.title_bn,
        'title_en': notification.title_en,
        'body_bn': notification.body_bn,
        'body_en': notification.body_en,
        'is_read': notification.is_read,
        'reference_type': notification.reference_type,
        'reference_id': str(notification.reference_id) if notification.reference_id else None,
        'created_at': notification.created_at.isoformat() if notification.created_at else None,
    }


def broadcast_notification(notification) -> None:
    layer = get_channel_layer()
    if not layer:
        return
    async_to_sync(layer.group_send)(
        f'notifications_{notification.user_id}',
        {'type': 'notify', 'notification': _serialize(notification)},
    )


def broadcast_notifications(notifications) -> None:
    for notification in notifications:
        broadcast_notification(notification)
