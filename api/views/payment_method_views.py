from decimal import Decimal, InvalidOperation
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny

from api.models import PaymentMethod
from api.permissions import has_permission
from api.utils.response import ApiResponse

CHARGE_TYPES = ['NONE', 'PERCENT', 'FLAT']


def _serialize(m: PaymentMethod, request=None) -> dict:
    logo_url = None
    if m.logo:
        logo_url = request.build_absolute_uri(m.logo.url) if request else m.logo.url
    return {
        'id':            m.id,
        'code':          m.code,
        'name_bn':       m.name_bn,
        'name_en':       m.name_en,
        'logo':          logo_url,
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
    return ApiResponse(message='Payment methods retrieved', data=[_serialize(m, request) for m in methods])


@api_view(['POST'])
@permission_classes([IsAuthenticated, has_permission('payment_methods', 'edit')])
def create_payment_method(request):
    """Admin-added row — e.g. a mobile wallet with no gateway integration
    yet. Starts is_enabled=False/is_integrated=False always (see the model
    docstring): this only lets an admin pre-configure the name/logo/charge
    ahead of a future code change that actually wires up that gateway and
    flips is_integrated, exactly like BKASH/NAGAD/STRIPE were seeded."""
    code    = (request.data.get('code') or '').strip().upper().replace(' ', '_')
    name_bn = (request.data.get('name_bn') or '').strip()
    name_en = (request.data.get('name_en') or '').strip()

    if not code or not name_bn or not name_en:
        return ApiResponse(
            message='Validation failed',
            errors='code, name_bn and name_en are required',
            status_code=422,
        )
    if PaymentMethod.objects.filter(code=code).exists():
        return ApiResponse(message='Already exists', errors=f'{code} already exists', status_code=422)

    charge_type = request.data.get('charge_type', 'NONE')
    if charge_type not in CHARGE_TYPES:
        return ApiResponse(message='Invalid charge_type', status_code=422)

    try:
        charge_value = Decimal(str(request.data.get('charge_value', '0') or '0'))
        if charge_value < 0:
            raise InvalidOperation
    except (InvalidOperation, TypeError, ValueError):
        return ApiResponse(message='Invalid charge_value', status_code=422)

    next_sort = (PaymentMethod.objects.order_by('-sort_order').values_list('sort_order', flat=True).first() or 0) + 1
    m = PaymentMethod.objects.create(
        code=code, name_bn=name_bn, name_en=name_en,
        charge_type=charge_type, charge_value=charge_value,
        sort_order=next_sort,
    )
    if 'logo' in request.FILES:
        m.logo = request.FILES['logo']
        m.save(update_fields=['logo'])

    return ApiResponse(message='Payment method created', data=_serialize(m, request), status_code=status.HTTP_201_CREATED)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, has_permission('payment_methods', 'edit')])
def update_payment_method(request, pk):
    try:
        m = PaymentMethod.objects.get(pk=pk)
    except PaymentMethod.DoesNotExist:
        return ApiResponse(message='Not found', status_code=status.HTTP_404_NOT_FOUND)

    # code / is_integrated / sort_order are backend-controlled, not
    # admin-editable — the toggle, charge config, display names and logo are.
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

    if 'name_bn' in request.data and request.data['name_bn'].strip():
        m.name_bn = request.data['name_bn'].strip()
    if 'name_en' in request.data and request.data['name_en'].strip():
        m.name_en = request.data['name_en'].strip()
    if 'logo' in request.FILES:
        m.logo = request.FILES['logo']

    m.save(update_fields=['is_enabled', 'charge_type', 'charge_value', 'name_bn', 'name_en', 'logo'])
    return ApiResponse(message='Payment method updated', data=_serialize(m, request))
