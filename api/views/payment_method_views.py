from decimal import Decimal, InvalidOperation
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny

from api.models import PaymentMethod
from api.permissions import has_permission
from api.utils.response import ApiResponse

CHARGE_TYPES = ['NONE', 'PERCENT', 'FLAT']


def _serialize(m: PaymentMethod) -> dict:
    return {
        'id':            m.id,
        'code':          m.code,
        'name_bn':       m.name_bn,
        'name_en':       m.name_en,
        'is_enabled':    m.is_enabled,
        'is_integrated': m.is_integrated,
        'charge_type':   m.charge_type,
        'charge_value':  str(m.charge_value),
        'sort_order':    m.sort_order,
    }


@api_view(['GET'])
@permission_classes([AllowAny])
def list_payment_methods(request):
    """Public — checkout (including anonymous guest checkout) needs this to
    know which methods to offer and what charge to preview."""
    methods = PaymentMethod.objects.all()
    return ApiResponse(message='Payment methods retrieved', data=[_serialize(m) for m in methods])


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, has_permission('payment_methods', 'edit')])
def update_payment_method(request, pk):
    try:
        m = PaymentMethod.objects.get(pk=pk)
    except PaymentMethod.DoesNotExist:
        return ApiResponse(message='Not found', status_code=status.HTTP_404_NOT_FOUND)

    # code / name / is_integrated / sort_order are backend-controlled, not
    # admin-editable — only the on/off toggle and charge config are.
    if 'is_enabled' in request.data:
        if not m.is_integrated and str(request.data['is_enabled']).lower() in ('true', '1', 'yes'):
            return ApiResponse(
                message='Not available yet',
                errors='This payment method has no gateway integration yet — it can be pre-configured but not enabled.',
                status_code=422,
            )
        m.is_enabled = str(request.data['is_enabled']).lower() in ('true', '1', 'yes')

    if 'charge_type' in request.data:
        if request.data['charge_type'] not in CHARGE_TYPES:
            return ApiResponse(message='Invalid charge_type', status_code=422)
        m.charge_type = request.data['charge_type']

    if 'charge_value' in request.data:
        try:
            value = Decimal(str(request.data['charge_value']))
            if value < 0:
                raise InvalidOperation
            m.charge_value = value
        except (InvalidOperation, TypeError, ValueError):
            return ApiResponse(message='Invalid charge_value', status_code=422)

    m.save(update_fields=['is_enabled', 'charge_type', 'charge_value'])
    return ApiResponse(message='Payment method updated', data=_serialize(m))
