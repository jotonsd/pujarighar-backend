import logging

from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.throttling import AnonRateThrottle

from api.services import support_chat_service
from api.utils.response import ApiResponse

logger = logging.getLogger(__name__)


class SupportChatThrottle(AnonRateThrottle):
    # Public, unauthenticated, cost-per-call endpoint — capped per IP so it
    # can't be hammered into a large Gemini bill.
    scope = 'support_chat'
    rate = '20/min'


class SupportChatAddItemsThrottle(AnonRateThrottle):
    # No Gemini call on this path (see add_order_items_by_id) — just DB
    # reads, so a more generous cap than the LLM-backed endpoint is fine.
    scope = 'support_chat_add_items'
    rate = '60/min'


@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([SupportChatThrottle])
def support_chat(request):
    message = (request.data.get('message') or '').strip()
    if not message:
        return ApiResponse(message='Message is required', errors='message is required', status_code=400)
    history = request.data.get('history') or []
    if not isinstance(history, list):
        history = []
    pending_order = request.data.get('pending_order')
    if not isinstance(pending_order, dict):
        pending_order = None

    if not support_chat_service.is_configured():
        return ApiResponse(
            message='Support chat is not configured',
            errors='AI support chat is not set up yet',
            status_code=503,
        )

    try:
        result = support_chat_service.answer(message, history, pending_order)
        return ApiResponse(message='Reply generated', data=result)
    except Exception as e:
        logger.error(f'Support chat error: {e}', exc_info=True)
        return ApiResponse(message='Failed to generate a reply', errors=str(e), status_code=502)


@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([SupportChatAddItemsThrottle])
def support_chat_add_items(request):
    """Checkbox multi-select 'Order selected' — adds known product_ids
    straight to the order preview, no Gemini call (see
    support_chat_service.add_order_items_by_id for why this is safe to
    resolve deterministically instead of through the AI chat loop)."""
    product_ids = request.data.get('product_ids')
    if not isinstance(product_ids, list) or not product_ids:
        return ApiResponse(message='product_ids is required', errors='product_ids is required', status_code=400)
    pending_order = request.data.get('pending_order')
    if not isinstance(pending_order, dict):
        pending_order = None

    try:
        result = support_chat_service.add_order_items_by_id(product_ids, pending_order)
        if 'error' in result:
            return ApiResponse(message=result['error'], errors=result['error'], status_code=422)
        return ApiResponse(message='Items added', data=result)
    except Exception as e:
        logger.error(f'Support chat add-items error: {e}', exc_info=True)
        return ApiResponse(message='Failed to add items', errors=str(e), status_code=502)
