import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated

from api.models import DeliveryCharge
from api.permissions import IsAdmin
from api.utils.response import ApiResponse

logger = logging.getLogger(__name__)

DHAKA_DISTRICTS = {'dhaka', 'ঢাকা'}


def _serialize(charge: DeliveryCharge) -> dict:
    return {
        'inside_dhaka':  str(charge.inside_dhaka),
        'outside_dhaka': str(charge.outside_dhaka),
        'updated_at':    charge.updated_at.isoformat() if charge.updated_at else None,
    }


@api_view(['GET'])
@permission_classes([AllowAny])
def get_delivery_charges(request):
    return ApiResponse(message='Delivery charges', data=_serialize(DeliveryCharge.get()))


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, IsAdmin])
def update_delivery_charges(request):
    charge = DeliveryCharge.get()
    inside  = request.data.get('inside_dhaka')
    outside = request.data.get('outside_dhaka')
    if inside  is not None: charge.inside_dhaka  = inside
    if outside is not None: charge.outside_dhaka = outside
    charge.updated_by = request.user
    charge.save()
    logger.info(f'Delivery charges updated by {request.user.email}: inside={charge.inside_dhaka} outside={charge.outside_dhaka}')
    return ApiResponse(message='Delivery charges updated', data=_serialize(charge))
