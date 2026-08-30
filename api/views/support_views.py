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

    if not support_chat_service.is_configured():
        return ApiResponse(
            message='Support chat is not configured',
            errors='AI support chat is not set up yet',
            status_code=503,
        )

    try:
        reply = support_chat_service.answer(message, history)
        return ApiResponse(message='Reply generated', data={'reply': reply})
    except Exception as e:
        logger.error(f'Support chat error: {e}', exc_info=True)
        return ApiResponse(message='Failed to generate a reply', errors=str(e), status_code=502)
