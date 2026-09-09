import logging
from decimal import Decimal, InvalidOperation
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated

from api.models import DeliveryCharge
from api.permissions import has_permission
from api.utils.response import ApiResponse

logger = logging.getLogger(__name__)

DHAKA_DISTRICTS = {'dhaka', 'ঢাকা'}


def _serialize(charge: DeliveryCharge) -> dict:
    return {
        'inside_dhaka':               str(charge.inside_dhaka),
        'outside_dhaka':              str(charge.outside_dhaka),
        'inside_dhaka_weight_tiers':  charge.inside_dhaka_weight_tiers,
        'outside_dhaka_weight_tiers': charge.outside_dhaka_weight_tiers,
        'inside_dhaka_extra_per_kg':  str(charge.inside_dhaka_extra_per_kg),
        'outside_dhaka_extra_per_kg': str(charge.outside_dhaka_extra_per_kg),
        'updated_at':                 charge.updated_at.isoformat() if charge.updated_at else None,
    }


def _clean_tiers(raw) -> list:
    """Drop/skip malformed rows rather than 500ing — a bad row here would
    otherwise break DeliveryCharge.charge_for() for every order at
    assignment time, not just the settings save."""
    if not isinstance(raw, list):
        return []
    cleaned = []
    for row in raw:
        try:
            max_weight_kg = str(Decimal(str(row.get('max_weight_kg'))))
            charge_amount = str(Decimal(str(row.get('charge_amount'))))
        except (InvalidOperation, AttributeError, TypeError, ValueError):
            continue
        cleaned.append({'max_weight_kg': max_weight_kg, 'charge_amount': charge_amount})
    return cleaned


@api_view(['GET'])
@permission_classes([AllowAny])
def get_delivery_charges(request):
    charge = DeliveryCharge.get()
    data = _serialize(charge)
    # Optional live quote — the cart/checkout page passes its current total
    # weight so the price shown to the customer for each zone matches
    # exactly what checkout will actually charge (same charge_for() call),
    # instead of the flat rate above which ignores weight tiers entirely.
    weight_param = request.query_params.get('weight')
    if weight_param:
        try:
            weight = Decimal(weight_param)
            data['inside_dhaka_for_weight']  = str(charge.charge_for('inside', weight))
            data['outside_dhaka_for_weight'] = str(charge.charge_for('outside', weight))
        except InvalidOperation:
            pass
    return ApiResponse(message='Delivery charges', data=data)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, has_permission('delivery_charges', 'edit')])
def update_delivery_charges(request):
    charge = DeliveryCharge.get()
    inside  = request.data.get('inside_dhaka')
    outside = request.data.get('outside_dhaka')
    if inside  is not None: charge.inside_dhaka  = inside
    if outside is not None: charge.outside_dhaka = outside
    if 'inside_dhaka_weight_tiers' in request.data:
        charge.inside_dhaka_weight_tiers = _clean_tiers(request.data.get('inside_dhaka_weight_tiers'))
    if 'outside_dhaka_weight_tiers' in request.data:
        charge.outside_dhaka_weight_tiers = _clean_tiers(request.data.get('outside_dhaka_weight_tiers'))
    inside_extra  = request.data.get('inside_dhaka_extra_per_kg')
    outside_extra = request.data.get('outside_dhaka_extra_per_kg')
    if inside_extra  is not None: charge.inside_dhaka_extra_per_kg  = inside_extra
    if outside_extra is not None: charge.outside_dhaka_extra_per_kg = outside_extra
    charge.updated_by = request.user
    charge.save()
    logger.info(f'Delivery charges updated by {request.user.email}: inside={charge.inside_dhaka} outside={charge.outside_dhaka}')
    return ApiResponse(message='Delivery charges updated', data=_serialize(charge))
