from decimal import Decimal, InvalidOperation

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated

from api.models import PromoCode
from api.permissions import has_permission
from api.services.promo_service import validate_promo_code
from api.utils.response import ApiResponse

DISCOUNT_TYPES = ['PERCENT', 'FLAT']
SCOPES = ['MOBILE_APP', 'WEBSITE', 'BOTH']


def _serialize(p: PromoCode) -> dict:
    return {
        'id':               p.id,
        'code':             p.code,
        'scope':            p.scope,
        'discount_type':    p.discount_type,
        'discount_value':   str(p.discount_value),
        'valid_from':       p.valid_from,
        'valid_until':      p.valid_until,
        'is_active':        p.is_active,
        'is_valid_now':     p.is_valid_now(),
        'times_used':       p.times_used,
    }


def _parse_dt(value):
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        raise ValueError
    return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed


@api_view(['GET'])
@permission_classes([IsAuthenticated, has_permission('promo_codes', 'view')])
def list_promo_codes(request):
    codes = PromoCode.objects.all()
    return ApiResponse(message='Promo codes retrieved', data=[_serialize(p) for p in codes])


@api_view(['GET'])
@permission_classes([AllowAny])
def has_active_website_promo(request):
    """Public, unauthenticated check used by the guest checkout page — lets
    it decide whether to show a "login to use a promo code" nudge at all,
    without exposing any actual code (unlike list_promo_codes, which is
    admin-only)."""
    exists = any(
        p.is_valid_now()
        for p in PromoCode.objects.filter(scope__in=['WEBSITE', 'BOTH'], is_active=True)
    )
    return ApiResponse(message='Checked', data={'has_active': exists})


@api_view(['POST'])
@permission_classes([IsAuthenticated, has_permission('promo_codes', 'edit')])
def create_promo_code(request):
    code = (request.data.get('code') or '').strip().upper()
    if not code:
        return ApiResponse(message='Validation failed', errors='code is required', status_code=422)
    if PromoCode.objects.filter(code__iexact=code).exists():
        return ApiResponse(message='Already exists', errors=f'{code} already exists', status_code=422)

    scope = request.data.get('scope', 'MOBILE_APP')
    if scope not in SCOPES:
        return ApiResponse(message='Invalid scope', status_code=422)

    discount_type = request.data.get('discount_type', 'PERCENT')
    if discount_type not in DISCOUNT_TYPES:
        return ApiResponse(message='Invalid discount_type', status_code=422)
    try:
        discount_value = Decimal(str(request.data.get('discount_value', '0') or '0'))
        if discount_value <= 0:
            raise InvalidOperation
        if discount_type == 'PERCENT' and discount_value > 100:
            raise InvalidOperation
    except (InvalidOperation, TypeError, ValueError):
        return ApiResponse(message='Invalid discount_value', status_code=422)

    try:
        valid_from  = _parse_dt(request.data.get('valid_from'))
        valid_until = _parse_dt(request.data.get('valid_until'))
    except ValueError:
        return ApiResponse(message='Invalid date', errors='valid_from/valid_until must be ISO datetimes', status_code=422)

    is_active = str(request.data.get('is_active', True)).lower() in ('true', '1', 'yes')

    p = PromoCode.objects.create(
        code=code, scope=scope, discount_type=discount_type, discount_value=discount_value,
        valid_from=valid_from, valid_until=valid_until, is_active=is_active,
    )
    return ApiResponse(message='Promo code created', data=_serialize(p), status_code=status.HTTP_201_CREATED)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, has_permission('promo_codes', 'edit')])
def update_promo_code(request, pk):
    try:
        p = PromoCode.objects.get(pk=pk)
    except PromoCode.DoesNotExist:
        return ApiResponse(message='Not found', status_code=status.HTTP_404_NOT_FOUND)

    update_fields = []
    if 'is_active' in request.data:
        p.is_active = str(request.data['is_active']).lower() in ('true', '1', 'yes')
        update_fields.append('is_active')
    if 'scope' in request.data:
        if request.data['scope'] not in SCOPES:
            return ApiResponse(message='Invalid scope', status_code=422)
        p.scope = request.data['scope']
        update_fields.append('scope')
    if 'discount_type' in request.data:
        if request.data['discount_type'] not in DISCOUNT_TYPES:
            return ApiResponse(message='Invalid discount_type', status_code=422)
        p.discount_type = request.data['discount_type']
        update_fields.append('discount_type')
    if 'discount_value' in request.data:
        try:
            value = Decimal(str(request.data['discount_value']))
            if value <= 0:
                raise InvalidOperation
            p.discount_value = value
            update_fields.append('discount_value')
        except (InvalidOperation, TypeError, ValueError):
            return ApiResponse(message='Invalid discount_value', status_code=422)
    if 'valid_from' in request.data:
        try:
            p.valid_from = _parse_dt(request.data['valid_from'])
            update_fields.append('valid_from')
        except ValueError:
            return ApiResponse(message='Invalid date', status_code=422)
    if 'valid_until' in request.data:
        try:
            p.valid_until = _parse_dt(request.data['valid_until'])
            update_fields.append('valid_until')
        except ValueError:
            return ApiResponse(message='Invalid date', status_code=422)

    if update_fields:
        p.save(update_fields=update_fields)
    return ApiResponse(message='Promo code updated', data=_serialize(p))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def preview_promo_code(request):
    """Lets the app show "10% off applied" (or a clear reason it can't be)
    before the customer actually submits checkout — without this, the
    first they'd hear about an invalid/expired/already-used code is a
    checkout failure. Never redeems (times_used only increments once an
    order is actually created — see promo_service.redeem_promo_code)."""
    code = (request.data.get('code') or '').strip()
    source = 'MOBILE_APP' if request.headers.get('X-Client-Platform') == 'mobile_app' else 'WEBSITE'
    if not code:
        return ApiResponse(message='Validation failed', errors='code is required', status_code=422)
    try:
        promo = validate_promo_code(code, source, user=request.user)
    except Exception as e:
        detail = getattr(e, 'detail', str(e))
        return ApiResponse(message='Invalid promo code', errors=detail, status_code=422)
    return ApiResponse(message='Promo code is valid', data={
        'code': promo.code, 'discount_type': promo.discount_type, 'discount_value': str(promo.discount_value),
    })
