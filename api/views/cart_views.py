import logging
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from api.models import CartItem
from api.serializers.cart_serializers import CartSerializer, AddToCartSerializer, UpdateCartItemSerializer
from api.services.cart_service import CartService
from api.services.checkout_service import CheckoutService
from api.services.sslcommerz_service import SSLCommerzService
from django.conf import settings as django_settings
from api.serializers.order_serializers import SalesOrderSerializer
from api.utils.response import ApiResponse
from api.permissions import IsCustomer

logger = logging.getLogger(__name__)
_cart_svc     = CartService()
_checkout_svc = CheckoutService()


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsCustomer])
def get_cart(request):
    cart = _cart_svc.get_or_create_cart(request.user)
    return ApiResponse(message="Cart retrieved", data=CartSerializer(cart, context={'request': request}).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsCustomer])
def add_to_cart(request):
    serializer = AddToCartSerializer(data=request.data)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    try:
        cart = _cart_svc.add_item(
            request.user,
            serializer.validated_data['product'],
            serializer.validated_data['quantity'],
        )
        return ApiResponse(message="Item added to cart", data=CartSerializer(cart).data, status_code=201)
    except Exception as e:
        logger.error(f"Add to cart error: {e}", exc_info=True)
        return ApiResponse(message=str(e), errors=str(e), status_code=400)


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsAuthenticated, IsCustomer])
def update_cart_item(request, item_id):
    if request.method == 'DELETE':
        _cart_svc.remove_item(str(item_id))
        return ApiResponse(message="Item removed from cart")

    # PATCH — update quantity
    serializer = UpdateCartItemSerializer(data=request.data)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    try:
        cart    = _cart_svc.get_or_create_cart(request.user)
        updated = _cart_svc.update_item(cart, str(item_id), serializer.validated_data['quantity'])
        return ApiResponse(message="Cart item updated", data=CartSerializer(updated).data)
    except CartItem.DoesNotExist:
        return ApiResponse(message="Item not found", errors="Not found", status_code=404)
    except Exception as e:
        return ApiResponse(message=str(e), errors=str(e), status_code=400)


# Kept as a no-op alias so the __init__.py import doesn't break
remove_cart_item = update_cart_item


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, IsCustomer])
def clear_cart(request):
    cart = _cart_svc.get_or_create_cart(request.user)
    _cart_svc.clear_cart(cart)
    return ApiResponse(message="Cart cleared")


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsCustomer])
def checkout(request):
    payment_method      = request.data.get('payment_method', 'COD')
    shipping_address_id = request.data.get('shipping_address_id') or None
    delivery_zone       = request.data.get('delivery_zone') or None
    if payment_method not in ('COD', 'ONLINE'):
        return ApiResponse(message="Invalid payment method", errors="Use COD or ONLINE", status_code=422)
    try:
        order = _checkout_svc.checkout(
            request.user,
            payment_method=payment_method,
            shipping_address_id=shipping_address_id,
            delivery_zone=delivery_zone,
        )
        data  = SalesOrderSerializer(order).data

        if payment_method == 'ONLINE':
            gateway_url = SSLCommerzService().initiate_payment(order, django_settings.BACKEND_URL)
            data = {**data, 'gateway_url': gateway_url}
            return ApiResponse(message="Proceed to payment", data=data, status_code=status.HTTP_201_CREATED)

        return ApiResponse(message="Order placed successfully", data=data, status_code=status.HTTP_201_CREATED)
    except Exception as e:
        logger.error(f"Checkout error: {e}", exc_info=True)
        return ApiResponse(message=str(e), errors=str(e), status_code=400)
