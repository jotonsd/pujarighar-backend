import logging

from django.http import HttpResponse
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny

from api.models import SiteSetting
from api.services import whatsapp_service
from api.utils.response import ApiResponse

logger = logging.getLogger(__name__)


@api_view(['GET', 'POST'])
@authentication_classes([])  # bypass DRF's global JWTAuthentication — Meta calls this
                             # endpoint directly (verify handshake + real events), never
                             # carrying one of our JWTs
@permission_classes([AllowAny])
def whatsapp_webhook(request):
    s = SiteSetting.get()

    if request.method == 'GET':
        # One-time (and periodic re-check) verification handshake — Meta calls
        # this when the webhook URL is registered in the dashboard, echoing
        # back hub.challenge only if hub.verify_token matches what the admin
        # configured on both sides.
        mode = request.query_params.get('hub.mode')
        token = request.query_params.get('hub.verify_token')
        challenge = request.query_params.get('hub.challenge', '')
        if mode == 'subscribe' and s.whatsapp_verify_token and token == s.whatsapp_verify_token:
            return HttpResponse(challenge, content_type='text/plain')
        logger.warning('WhatsApp webhook verification failed: token mismatch')
        return ApiResponse(message='Verification failed', errors='Invalid verify token', status_code=403)

    # POST — a real event. Verified via X-Hub-Signature-256 (HMAC of the raw
    # body with the app secret), not a bearer token — same role Pathao's
    # X-PATHAO-Signature plays on the courier webhook.
    logger.info(f'WhatsApp webhook received: {request.data}')
    signature = request.META.get('HTTP_X_HUB_SIGNATURE_256', '')
    if not whatsapp_service.verify_signature(request.body, signature):
        logger.warning('WhatsApp webhook rejected: signature mismatch')
        return ApiResponse(message='Invalid signature', errors='Unauthorized', status_code=401)

    try:
        whatsapp_service.handle_incoming_message(request.data)
    except Exception as e:
        logger.error(f'WhatsApp webhook error: {e}', exc_info=True)
        # Still 200 — Meta retries aggressively on non-200 and we've already
        # logged the failure; a stuck retry loop is worse than a dropped one-off.
    return ApiResponse(message='ok')
