import logging

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from api.models import PromoEmail
from api.serializers.promo_email_serializers import PromoEmailSerializer, PromoEmailCreateSerializer
from api.services.mail_service import send_promo_email, promo_recipients
from api.permissions import IsAdmin
from api.utils.response import ApiResponse
from api.utils.pagination import paginate_queryset

logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def list_promo_emails(request):
    try:
        qs = PromoEmail.objects.select_related('sent_by', 'sent_by__profile').all()
        page_data, pagination = paginate_queryset(qs, request)
        return ApiResponse(
            message="Promo emails retrieved",
            data=PromoEmailSerializer(page_data, many=True, context={'request': request}).data,
            pagination=pagination,
        )
    except Exception as e:
        logger.error(f"List promo emails error: {e}", exc_info=True)
        return ApiResponse(message=str(e), errors=str(e), status_code=500)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def promo_email_audience(request):
    """Returns the recipient count for a given email_type before sending, for confirmation."""
    email_type = request.query_params.get('email_type', 'GENERAL')
    try:
        count = len(promo_recipients(email_type))
        return ApiResponse(message="Audience size retrieved", data={'recipient_count': count})
    except Exception as e:
        return ApiResponse(message=str(e), errors=str(e), status_code=400)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAdmin])
def create_promo_email(request):
    serializer = PromoEmailCreateSerializer(data=request.data)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    try:
        promo = serializer.save(sent_by=request.user)
        send_promo_email(promo)
        return ApiResponse(
            message="Promo email queued for sending",
            data=PromoEmailSerializer(promo, context={'request': request}).data,
            status_code=201,
        )
    except Exception as e:
        logger.error(f"Create promo email error: {e}", exc_info=True)
        return ApiResponse(message=str(e), errors=str(e), status_code=400)
