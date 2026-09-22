import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny

from api.models import PaymentMethod
from api.serializers.guest_serializers import GuestCheckoutSerializer
from api.services.guest_service import GuestCheckoutService
from api.services.sslcommerz_service import SSLCommerzService
from api.services import mail_service
from django.conf import settings as django_settings
from api.utils.response import ApiResponse

logger = logging.getLogger(__name__)
_svc = GuestCheckoutService()


@api_view(['POST'])
@permission_classes([AllowAny])
def guest_checkout(request):
    serializer = GuestCheckoutSerializer(data=request.data)
    if not serializer.is_valid():
        return ApiResponse(
            message="Validation failed",
            errors=serializer.errors,
            status_code=422,
        )
    payment_method = serializer.validated_data.get('payment_method', 'COD')
    method = PaymentMethod.objects.filter(code=payment_method).first()
    if not method or not method.is_enabled:
        return ApiResponse(
            message="Payment method unavailable",
            errors="This payment method is currently disabled",
            status_code=422,
        )
    try:
        is_mobile_app = request.headers.get('X-Client-Platform') == 'mobile_app'
        order = _svc.checkout(serializer.validated_data, is_mobile_app=is_mobile_app)
        mail_service.send_order_created(order)
        data  = {
            'order_number': order.order_number,
            'order_id':     str(order.id),
            'grand_total':  str(order.grand_total),
            'gateway_charge_amount': str(order.gateway_charge_amount),
            'status':       order.status,
        }

        if serializer.validated_data.get('payment_method') == 'SSLCOMMERZ':
            gateway_url = SSLCommerzService().initiate_payment(order, django_settings.BACKEND_URL)
            data['gateway_url'] = gateway_url
            return ApiResponse(message="Proceed to payment", data=data, status_code=201)

        return ApiResponse(message="Order placed successfully", data=data, status_code=201)
    except Exception as e:
        logger.error(f"Guest checkout error: {e}", exc_info=True)
        return ApiResponse(message=str(e), errors=str(e), status_code=400)
