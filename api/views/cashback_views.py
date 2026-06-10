import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from api.models import CashbackTier
from api.permissions import IsAdmin
from api.utils.response import ApiResponse

logger = logging.getLogger(__name__)


def _serialize(tier: CashbackTier) -> dict:
    return {
        'id':               tier.id,
        'min_order_amount': str(tier.min_order_amount),
        'cashback_type':    tier.cashback_type,
        'cashback_value':   str(tier.cashback_value),
        'max_cashback':     str(tier.max_cashback),
        'is_active':        tier.is_active,
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def list_cashback_tiers(request):
    tiers = CashbackTier.objects.all()
    return ApiResponse(message='Cashback tiers', data=[_serialize(t) for t in tiers])


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAdmin])
def create_cashback_tier(request):
    try:
        tier = CashbackTier.objects.create(
            min_order_amount=request.data['min_order_amount'],
            cashback_type=request.data.get('cashback_type', 'FIXED'),
            cashback_value=request.data['cashback_value'],
            max_cashback=request.data.get('max_cashback', 0),
            is_active=request.data.get('is_active', True),
        )
        logger.info(f'Cashback tier created by {request.user.email}: ৳{tier.min_order_amount}+')
        return ApiResponse(message='Tier created', data=_serialize(tier), status_code=201)
    except Exception as e:
        return ApiResponse(message=str(e), errors=str(e), status_code=400)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, IsAdmin])
def update_cashback_tier(request, pk):
    try:
        tier = CashbackTier.objects.get(pk=pk)
    except CashbackTier.DoesNotExist:
        return ApiResponse(message='Not found', errors='Not found', status_code=404)

    for field in ['min_order_amount', 'cashback_type', 'cashback_value', 'max_cashback', 'is_active']:
        if field in request.data:
            setattr(tier, field, request.data[field])
    tier.save()
    return ApiResponse(message='Tier updated', data=_serialize(tier))


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, IsAdmin])
def delete_cashback_tier(request, pk):
    try:
        CashbackTier.objects.get(pk=pk).delete()
        return ApiResponse(message='Tier deleted')
    except CashbackTier.DoesNotExist:
        return ApiResponse(message='Not found', errors='Not found', status_code=404)
