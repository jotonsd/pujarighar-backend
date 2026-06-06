import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated

from api.models import SalesOrder, OrderStatusLog
from api.serializers.guest_serializers import GuestCheckoutSerializer
from api.services.guest_service import GuestCheckoutService
from api.serializers.order_serializers import (
    SalesOrderSerializer, OrderStatusLogSerializer,
    OrderTrackingSerializer, AssignDeliverySerializer, OrderCancelSerializer,
)
from api.services.order_service import OrderService
from api.utils.response import ApiResponse, api_error
from api.utils.pagination import paginate_queryset
from api.permissions import IsAdmin, IsAdminOrWarehouse, IsAdminOrDelivery, IsDelivery

logger = logging.getLogger(__name__)
_svc = OrderService()


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAdmin])
def pos_create_order(request):
    serializer = GuestCheckoutSerializer(data=request.data)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    try:
        order = GuestCheckoutService().checkout(serializer.validated_data)
        return ApiResponse(
            message="POS order created",
            data=SalesOrderSerializer(order).data,
            status_code=201,
        )
    except Exception as e:
        logger.error(f"POS create error: {e}", exc_info=True)
        return api_error(e)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_orders(request):
    try:
        qs = _svc.list_orders(request.user, request.query_params)
        page_data, pagination = paginate_queryset(qs, request)
        return ApiResponse(
            message="Orders retrieved successfully",
            data=SalesOrderSerializer(page_data, many=True).data,
            pagination=pagination,
        )
    except Exception as e:
        logger.error(f"List orders error: {e}", exc_info=True)
        return api_error(e)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_order(request, pk):
    try:
        order = _svc.get_order(pk)
        role  = request.user.role
        if role == 'CUSTOMER' and order.customer != request.user:
            return ApiResponse(message="Permission denied", errors="Forbidden", status_code=403)
        if role == 'WAREHOUSE' and order.status not in ('CONFIRMED', 'PACKED'):
            return ApiResponse(message="Permission denied", errors="Forbidden", status_code=403)
        if role == 'DELIVERY' and (not hasattr(order, 'delivery') or order.delivery.delivery_person != request.user):
            return ApiResponse(message="Permission denied", errors="Forbidden", status_code=403)
        return ApiResponse(message="Order retrieved", data=SalesOrderSerializer(order).data)
    except SalesOrder.DoesNotExist:
        return ApiResponse(message="Order not found", errors="Not found", status_code=404)


@api_view(['GET'])
@permission_classes([AllowAny])
def get_order_tracking(_request, pk):
    try:
        order = SalesOrder.objects.prefetch_related(
            'status_logs', 'delivery__delivery_person__profile'
        ).get(pk=pk)
        return ApiResponse(message="Tracking retrieved", data=OrderTrackingSerializer(order).data)
    except SalesOrder.DoesNotExist:
        return ApiResponse(message="Order not found", errors="Not found", status_code=404)


@api_view(['GET'])
@permission_classes([AllowAny])
def track_by_order_number(request):
    order_number = request.query_params.get('order_number', '').strip().upper()
    phone        = request.query_params.get('phone', '').strip()

    if not order_number or not phone:
        return ApiResponse(
            message="order_number and phone are required",
            errors="Missing params",
            status_code=400,
        )

    try:
        order = SalesOrder.objects.prefetch_related(
            'status_logs', 'delivery__delivery_person__profile'
        ).get(
            order_number__iexact=order_number,
            shipping_phone=phone,
        )
        return ApiResponse(message="Order found", data=OrderTrackingSerializer(order).data)
    except SalesOrder.DoesNotExist:
        return ApiResponse(message="Order not found", errors="Not found", status_code=404)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_order_status_log(request, pk):
    try:
        order = SalesOrder.objects.prefetch_related('delivery').get(pk=pk)
        role  = request.user.role
        if role == 'CUSTOMER' and order.customer != request.user:
            return ApiResponse(message="Permission denied", errors="Forbidden", status_code=403)
        if role == 'DELIVERY':
            if not hasattr(order, 'delivery') or order.delivery.delivery_person != request.user:
                return ApiResponse(message="Permission denied", errors="Forbidden", status_code=403)
        if role == 'WAREHOUSE':
            return ApiResponse(message="Permission denied", errors="Forbidden", status_code=403)
        logs = OrderStatusLog.objects.filter(order=order).order_by('changed_at')
        return ApiResponse(message="Status log retrieved", data=OrderStatusLogSerializer(logs, many=True).data)
    except SalesOrder.DoesNotExist:
        return ApiResponse(message="Order not found", errors="Not found", status_code=404)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAdmin])
def confirm_order(request, pk):
    try:
        order = _svc.get_order(pk)
        return ApiResponse(message="Order confirmed", data=SalesOrderSerializer(_svc.confirm(order, request.user)).data)
    except SalesOrder.DoesNotExist:
        return ApiResponse(message="Order not found", errors="Not found", status_code=404)
    except Exception as e:
        return api_error(e)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAdminOrWarehouse])
def pack_order(request, pk):
    try:
        order = _svc.get_order(pk)
        return ApiResponse(message="Order packed", data=SalesOrderSerializer(_svc.pack(order, request.user)).data)
    except SalesOrder.DoesNotExist:
        return ApiResponse(message="Order not found", errors="Not found", status_code=404)
    except Exception as e:
        return api_error(e)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAdmin])
def assign_delivery(request, pk):
    try:
        order = _svc.get_order(pk)
    except SalesOrder.DoesNotExist:
        return ApiResponse(message="Order not found", errors="Not found", status_code=404)
    serializer = AssignDeliverySerializer(data=request.data)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    try:
        updated = _svc.assign_delivery(order, str(serializer.validated_data['delivery_person_id']), request.user)
        return ApiResponse(message="Delivery assigned", data=SalesOrderSerializer(updated).data)
    except Exception as e:
        return api_error(e)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsDelivery])
def dispatch_order(request, pk):
    try:
        order = _svc.get_order(pk)
        if not hasattr(order, 'delivery') or order.delivery.delivery_person != request.user:
            return ApiResponse(message="Permission denied", errors="Forbidden", status_code=403)
        return ApiResponse(message="Order dispatched", data=SalesOrderSerializer(_svc.dispatch(order, request.user)).data)
    except SalesOrder.DoesNotExist:
        return ApiResponse(message="Order not found", errors="Not found", status_code=404)
    except Exception as e:
        return api_error(e)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsDelivery])
def deliver_order(request, pk):
    try:
        order = _svc.get_order(pk)
        if not hasattr(order, 'delivery') or order.delivery.delivery_person != request.user:
            return ApiResponse(message="Permission denied", errors="Forbidden", status_code=403)
        return ApiResponse(message="Order delivered", data=SalesOrderSerializer(_svc.deliver(order, request.user)).data)
    except SalesOrder.DoesNotExist:
        return ApiResponse(message="Order not found", errors="Not found", status_code=404)
    except Exception as e:
        return api_error(e)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsDelivery])
def return_order(request, pk):
    try:
        order = _svc.get_order(pk)
        if not hasattr(order, 'delivery') or order.delivery.delivery_person != request.user:
            return ApiResponse(message="Permission denied", errors="Forbidden", status_code=403)
        note_bn = request.data.get('note_bn', '')
        note_en = request.data.get('note_en', '')
        return ApiResponse(
            message="Order returned",
            data=SalesOrderSerializer(_svc.return_order(order, request.user, note_bn, note_en)).data,
        )
    except SalesOrder.DoesNotExist:
        return ApiResponse(message="Order not found", errors="Not found", status_code=404)
    except Exception as e:
        return api_error(e)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAdminOrDelivery])
def mark_cod_paid(request, pk):
    try:
        order = _svc.get_order(pk)
    except SalesOrder.DoesNotExist:
        return ApiResponse(message='Order not found', errors='Not found', status_code=404)
    try:
        updated = _svc.mark_cod_paid(order, request.user)
        return ApiResponse(message='Payment recorded', data=SalesOrderSerializer(updated).data)
    except Exception as e:
        logger.error(f'Mark COD paid error: {e}', exc_info=True)
        return api_error(e)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def cancel_order(request, pk):
    try:
        order = _svc.get_order(pk)
    except SalesOrder.DoesNotExist:
        return ApiResponse(message="Order not found", errors="Not found", status_code=404)

    role = request.user.role
    if role == 'CUSTOMER':
        if order.customer != request.user:
            return ApiResponse(message="Permission denied", errors="Forbidden", status_code=403)
        if order.status != 'PENDING':
            return ApiResponse(message="Only pending orders can be cancelled", errors="Invalid status", status_code=400)
    elif role != 'ADMIN':
        return ApiResponse(message="Permission denied", errors="Forbidden", status_code=403)

    serializer = OrderCancelSerializer(data=request.data)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    try:
        updated = _svc.cancel(order, request.user,
                              serializer.validated_data['note_bn'],
                              serializer.validated_data['note_en'])
        return ApiResponse(message="Order cancelled", data=SalesOrderSerializer(updated).data)
    except Exception as e:
        logger.error(f"Cancel order error: {e}", exc_info=True)
        return api_error(e, locale_hint=request.LANGUAGE_CODE)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, IsAdmin])
def update_shipping(request, pk):
    try:
        order = SalesOrder.objects.get(pk=pk)
    except SalesOrder.DoesNotExist:
        return ApiResponse(message='Order not found', errors='Not found', status_code=404)

    allowed = [
        'shipping_name_bn', 'shipping_name_en', 'shipping_phone',
        'shipping_address_bn', 'shipping_address_en',
        'shipping_district', 'shipping_thana', 'shipping_post_code',
    ]
    fields = {k: v for k, v in request.data.items() if k in allowed}
    if not fields:
        return ApiResponse(message='No valid fields', errors='Provide at least one field', status_code=422)

    for k, v in fields.items():
        setattr(order, k, v)
    order.save(update_fields=list(fields.keys()))
    return ApiResponse(message='Shipping updated', data=SalesOrderSerializer(order).data)
