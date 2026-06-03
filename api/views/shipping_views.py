import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from api.models import ShippingAddress
from api.serializers.shipping_serializers import ShippingAddressSerializer
from api.utils.response import ApiResponse
from api.permissions import IsCustomer

logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsCustomer])
def list_shipping_addresses(request):
    addresses = ShippingAddress.objects.filter(user=request.user)
    return ApiResponse(
        message="Addresses retrieved",
        data=ShippingAddressSerializer(addresses, many=True).data,
    )


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsCustomer])
def create_shipping_address(request):
    serializer = ShippingAddressSerializer(data=request.data)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)

    is_first = not ShippingAddress.objects.filter(user=request.user).exists()
    address  = serializer.save(user=request.user, is_default=is_first)

    # If caller explicitly requests default, honour it
    if request.data.get('is_default') and not is_first:
        address.set_as_default()

    return ApiResponse(
        message="Address created",
        data=ShippingAddressSerializer(address).data,
        status_code=201,
    )


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, IsCustomer])
def update_shipping_address(request, pk):
    try:
        address = ShippingAddress.objects.get(pk=pk, user=request.user)
    except ShippingAddress.DoesNotExist:
        return ApiResponse(message="Not found", errors="Not found", status_code=404)

    serializer = ShippingAddressSerializer(address, data=request.data, partial=True)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)

    address = serializer.save()
    if request.data.get('is_default'):
        address.set_as_default()

    return ApiResponse(message="Address updated", data=ShippingAddressSerializer(address).data)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, IsCustomer])
def delete_shipping_address(request, pk):
    try:
        address = ShippingAddress.objects.get(pk=pk, user=request.user)
    except ShippingAddress.DoesNotExist:
        return ApiResponse(message="Not found", errors="Not found", status_code=404)

    was_default = address.is_default
    address.delete()

    if was_default:
        # Promote the most recent remaining address to default
        next_addr = ShippingAddress.objects.filter(user=request.user).first()
        if next_addr:
            next_addr.is_default = True
            next_addr.save(update_fields=['is_default', 'updated_at'])

    return ApiResponse(message="Address deleted")


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsCustomer])
def set_default_shipping_address(request, pk):
    try:
        address = ShippingAddress.objects.get(pk=pk, user=request.user)
    except ShippingAddress.DoesNotExist:
        return ApiResponse(message="Not found", errors="Not found", status_code=404)

    address.set_as_default()
    return ApiResponse(message="Default address updated", data=ShippingAddressSerializer(address).data)
