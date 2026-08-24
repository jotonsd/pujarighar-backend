import logging
from decimal import Decimal
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated

from api.models import SalesOrder, OrderStatusLog, Product
from api.serializers.guest_serializers import POSCheckoutSerializer
from api.services.guest_service import GuestCheckoutService
from api.serializers.order_serializers import (
    SalesOrderSerializer, OrderStatusLogSerializer,
    OrderTrackingSerializer, AssignDeliverySerializer, OrderCancelSerializer,
    AddOrderItemSerializer,
)
from api.services.order_service import OrderService
from api.services import mail_service
from api.utils.response import ApiResponse, api_error
from api.utils.pagination import paginate_queryset
from api.permissions import IsAdminOrDelivery, has_permission

logger = logging.getLogger(__name__)
_svc = OrderService()


@api_view(['POST'])
@permission_classes([IsAuthenticated, has_permission('pos', 'create')])
def pos_create_order(request):
    serializer = POSCheckoutSerializer(data=request.data)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    try:
        customer_id = request.data.get('customer_id')
        customer = None
        if customer_id:
            try:
                from api.models import User as UserModel
                customer = UserModel.objects.get(pk=customer_id)
            except UserModel.DoesNotExist:
                pass
        d = serializer.validated_data
        order = GuestCheckoutService().checkout(
            d, customer=customer,
            discount_type=d.get('discount_type', 'NONE'),
            discount_value=d.get('discount_value', 0),
            is_pos=True,
        )
        order = _svc.confirm(order, request.user)
        mail_service.send_order_created(order)
        return ApiResponse(
            message="POS order created",
            data=SalesOrderSerializer(order, context={'request': request}).data,
            status_code=201,
        )
    except Exception as e:
        logger.error(f"POS create error: {e}", exc_info=True)
        return api_error(e)


@api_view(['GET'])
@permission_classes([IsAuthenticated, has_permission('pos', 'create')])
def lookup_recent_order_by_phone(request):
    """POS auto-fill fallback for repeat guest customers — see
    OrderService.find_recent_shipping_by_phone for why this is separate from
    lookup_user_by_phone (that one only finds registered accounts)."""
    phone = request.query_params.get('phone', '').strip()
    if not phone:
        return ApiResponse(message="Phone required", errors="phone param required", status_code=400)
    order = _svc.find_recent_shipping_by_phone(phone)
    if not order:
        return ApiResponse(message="Not found", errors="No past order with this phone", status_code=404)
    return ApiResponse(message="Order found", data={
        'name_bn':     order.shipping_name_bn,
        'name_en':     order.shipping_name_en,
        'phone':       order.shipping_phone,
        'address_bn':  order.shipping_address_bn,
        'address_en':  order.shipping_address_en,
        'district':    order.shipping_district,
        'thana':       order.shipping_thana,
        'post_code':   order.shipping_post_code,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_orders(request):
    try:
        qs = _svc.list_orders(request.user, request.query_params)
        page_data, pagination = paginate_queryset(qs, request)
        return ApiResponse(
            message="Orders retrieved successfully",
            data=SalesOrderSerializer(page_data, many=True, context={'request': request}).data,
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
        role  = request.user.role.code
        if role == 'CUSTOMER' and order.customer != request.user:
            return ApiResponse(message="Permission denied", errors="Forbidden", status_code=403)
        if role == 'WAREHOUSE':
            pass
        if role == 'DELIVERY' and (not hasattr(order, 'delivery') or order.delivery.delivery_person != request.user):
            return ApiResponse(message="Permission denied", errors="Forbidden", status_code=403)
        return ApiResponse(message="Order retrieved", data=SalesOrderSerializer(order, context={'request': request}).data)
    except SalesOrder.DoesNotExist:
        return ApiResponse(message="Order not found", errors="Not found", status_code=404)


@api_view(['GET'])
@permission_classes([AllowAny])
def get_order_tracking(_request, pk):
    try:
        order = SalesOrder.objects.select_related('courier_consignment__provider').prefetch_related(
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
        order = SalesOrder.objects.select_related('courier_consignment__provider').prefetch_related(
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
        role  = request.user.role.code
        if role == 'CUSTOMER' and order.customer != request.user:
            return ApiResponse(message="Permission denied", errors="Forbidden", status_code=403)
        if role == 'DELIVERY':
            if not hasattr(order, 'delivery') or order.delivery.delivery_person != request.user:
                return ApiResponse(message="Permission denied", errors="Forbidden", status_code=403)
        if role == 'WAREHOUSE':
            pass
        logs = OrderStatusLog.objects.filter(order=order).order_by('changed_at')
        return ApiResponse(message="Status log retrieved", data=OrderStatusLogSerializer(logs, many=True).data)
    except SalesOrder.DoesNotExist:
        return ApiResponse(message="Order not found", errors="Not found", status_code=404)


@api_view(['POST'])
@permission_classes([IsAuthenticated, has_permission('orders', 'edit')])
def confirm_order(request, pk):
    try:
        order = _svc.get_order(pk)
        return ApiResponse(message="Order confirmed", data=SalesOrderSerializer(_svc.confirm(order, request.user), context={'request': request}).data)
    except SalesOrder.DoesNotExist:
        return ApiResponse(message="Order not found", errors="Not found", status_code=404)
    except Exception as e:
        return api_error(e)


@api_view(['POST'])
@permission_classes([IsAuthenticated, has_permission('orders', 'edit')])
def pack_order(request, pk):
    try:
        order = _svc.get_order(pk)
        return ApiResponse(message="Order packed", data=SalesOrderSerializer(_svc.pack(order, request.user), context={'request': request}).data)
    except SalesOrder.DoesNotExist:
        return ApiResponse(message="Order not found", errors="Not found", status_code=404)
    except Exception as e:
        return api_error(e)


@api_view(['POST'])
@permission_classes([IsAuthenticated, has_permission('orders', 'edit')])
def assign_delivery(request, pk):
    try:
        order = _svc.get_order(pk)
    except SalesOrder.DoesNotExist:
        return ApiResponse(message="Order not found", errors="Not found", status_code=404)
    serializer = AssignDeliverySerializer(data=request.data)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    try:
        delivery_person_id = serializer.validated_data.get('delivery_person_id')
        weight = serializer.validated_data.get('weight')
        updated = _svc.assign_delivery(order, str(delivery_person_id) if delivery_person_id else None, request.user, weight)
        return ApiResponse(message="Delivery assigned", data=SalesOrderSerializer(updated, context={'request': request}).data)
    except Exception as e:
        return api_error(e)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAdminOrDelivery])
def dispatch_order(request, pk):
    try:
        order = _svc.get_order(pk)
        # Only the assigned delivery person is ownership-checked — admins can act on any order.
        if request.user.role.code == 'DELIVERY' and (not hasattr(order, 'delivery') or order.delivery.delivery_person != request.user):
            return ApiResponse(message="Permission denied", errors="Forbidden", status_code=403)
        return ApiResponse(message="Order dispatched", data=SalesOrderSerializer(_svc.dispatch(order, request.user), context={'request': request}).data)
    except SalesOrder.DoesNotExist:
        return ApiResponse(message="Order not found", errors="Not found", status_code=404)
    except Exception as e:
        return api_error(e)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAdminOrDelivery])
def deliver_order(request, pk):
    try:
        order = _svc.get_order(pk)
        if request.user.role.code == 'DELIVERY' and (not hasattr(order, 'delivery') or order.delivery.delivery_person != request.user):
            return ApiResponse(message="Permission denied", errors="Forbidden", status_code=403)
        delivered = _svc.deliver(order, request.user)
        mail_service.send_order_delivered(delivered)
        return ApiResponse(message="Order delivered", data=SalesOrderSerializer(delivered, context={'request': request}).data)
    except SalesOrder.DoesNotExist:
        return ApiResponse(message="Order not found", errors="Not found", status_code=404)
    except Exception as e:
        return api_error(e)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAdminOrDelivery])
def return_order(request, pk):
    try:
        order = _svc.get_order(pk)
        if request.user.role.code == 'DELIVERY' and (not hasattr(order, 'delivery') or order.delivery.delivery_person != request.user):
            return ApiResponse(message="Permission denied", errors="Forbidden", status_code=403)
        note_bn = request.data.get('note_bn', '')
        note_en = request.data.get('note_en', '')
        return ApiResponse(
            message="Order returned",
            data=SalesOrderSerializer(_svc.return_order(order, request.user, note_bn, note_en), context={'request': request}).data,
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
        return ApiResponse(message='Payment recorded', data=SalesOrderSerializer(updated, context={'request': request}).data)
    except Exception as e:
        logger.error(f'Mark COD paid error: {e}', exc_info=True)
        return api_error(e)


@api_view(['POST'])
@permission_classes([IsAuthenticated, has_permission('orders', 'edit')])
def apply_discount(request, pk):
    try:
        order = _svc.get_order(pk)
    except SalesOrder.DoesNotExist:
        return ApiResponse(message='Order not found', errors='Not found', status_code=404)
    try:
        updated = _svc.apply_discount(
            order,
            request.data.get('discount_type', ''),
            Decimal(str(request.data.get('discount_value', 0))),
            request.user,
        )
        return ApiResponse(message='Discount applied', data=SalesOrderSerializer(updated, context={'request': request}).data)
    except Exception as e:
        logger.error(f'Apply discount error: {e}', exc_info=True)
        return api_error(e)


@api_view(['POST'])
@permission_classes([IsAuthenticated, has_permission('orders', 'edit')])
def waive_delivery_charge(request, pk):
    try:
        order = _svc.get_order(pk)
    except SalesOrder.DoesNotExist:
        return ApiResponse(message='Order not found', errors='Not found', status_code=404)
    try:
        updated = _svc.waive_delivery_charge(order, request.user)
        return ApiResponse(message='Delivery charge waived', data=SalesOrderSerializer(updated, context={'request': request}).data)
    except Exception as e:
        logger.error(f'Waive delivery charge error: {e}', exc_info=True)
        return api_error(e)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def cancel_order(request, pk):
    try:
        order = _svc.get_order(pk)
    except SalesOrder.DoesNotExist:
        return ApiResponse(message="Order not found", errors="Not found", status_code=404)

    role = request.user.role.code
    if role == 'CUSTOMER':
        if order.customer != request.user:
            return ApiResponse(message="Permission denied", errors="Forbidden", status_code=403)
        if order.status != 'PENDING':
            return ApiResponse(message="Only pending orders can be cancelled", errors="Invalid status", status_code=400)
    elif role not in ('ADMIN', 'WAREHOUSE'):
        return ApiResponse(message="Permission denied", errors="Forbidden", status_code=403)

    serializer = OrderCancelSerializer(data=request.data)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    try:
        updated = _svc.cancel(order, request.user,
                              serializer.validated_data['note_bn'],
                              serializer.validated_data['note_en'])
        mail_service.send_order_cancelled(updated)
        return ApiResponse(message="Order cancelled", data=SalesOrderSerializer(updated, context={'request': request}).data)
    except Exception as e:
        logger.error(f"Cancel order error: {e}", exc_info=True)
        return api_error(e, locale_hint=request.LANGUAGE_CODE)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, has_permission('orders', 'edit')])
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
    return ApiResponse(message='Shipping updated', data=SalesOrderSerializer(order, context={'request': request}).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated, has_permission('orders', 'edit')])
def add_order_item(request, pk):
    try:
        order = _svc.get_order(pk)
    except SalesOrder.DoesNotExist:
        return ApiResponse(message='Order not found', errors='Not found', status_code=404)

    serializer = AddOrderItemSerializer(data=request.data)
    if not serializer.is_valid():
        return ApiResponse(message='Validation failed', errors=serializer.errors, status_code=422)
    d = serializer.validated_data

    try:
        product = Product.objects.get(pk=d['product_id'])
        updated = _svc.add_item(order, product, d['quantity'], request.user)
        return ApiResponse(message='Item added', data=SalesOrderSerializer(updated, context={'request': request}).data)
    except Exception as e:
        logger.error(f'Add order item error: {e}', exc_info=True)
        return api_error(e)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, has_permission('orders', 'edit')])
def update_order_item(request, pk, item_id):
    try:
        order = _svc.get_order(pk)
    except SalesOrder.DoesNotExist:
        return ApiResponse(message='Order not found', errors='Not found', status_code=404)
    try:
        item = order.items.select_related('product').get(pk=item_id)
    except order.items.model.DoesNotExist:
        return ApiResponse(message='Order item not found', errors='Not found', status_code=404)

    try:
        new_quantity = Decimal(str(request.data.get('quantity', '')))
    except Exception:
        return ApiResponse(message='Validation failed', errors='Invalid quantity', status_code=422)

    try:
        updated = _svc.update_item_quantity(order, item, new_quantity, request.user)
        return ApiResponse(message='Quantity updated', data=SalesOrderSerializer(updated, context={'request': request}).data)
    except Exception as e:
        logger.error(f'Update order item error: {e}', exc_info=True)
        return api_error(e)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, has_permission('orders', 'edit')])
def delete_order_item(request, pk, item_id):
    try:
        order = _svc.get_order(pk)
    except SalesOrder.DoesNotExist:
        return ApiResponse(message='Order not found', errors='Not found', status_code=404)
    try:
        item = order.items.select_related('product').get(pk=item_id)
    except order.items.model.DoesNotExist:
        return ApiResponse(message='Order item not found', errors='Not found', status_code=404)

    try:
        updated = _svc.delete_item(order, item, request.user)
        return ApiResponse(message='Item removed', data=SalesOrderSerializer(updated, context={'request': request}).data)
    except Exception as e:
        logger.error(f'Delete order item error: {e}', exc_info=True)
        return api_error(e)


@api_view(['GET'])
@permission_classes([IsAuthenticated, has_permission('reports_sales', 'view')])
def get_sales_report(request):
    data = _svc.get_sales_report({
        'from':            request.query_params.get('from', ''),
        'to':              request.query_params.get('to', ''),
        'status':          request.query_params.get('status', ''),
        'payment_status':  request.query_params.get('payment_status', ''),
        'payment_method':  request.query_params.get('payment_method', ''),
    })
    return ApiResponse(message='Sales report retrieved', data=data)
