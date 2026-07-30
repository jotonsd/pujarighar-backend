import logging
from decimal import Decimal

from django.db import transaction
from rest_framework.exceptions import ValidationError

from api.models import (
    CourierConsignment, CourierProvider, CourierReturnRequest, CourierTrackingEvent,
    Notification, SalesOrder, User,
)
from api.services.courier.registry import get_courier_service
from api.services.notification_ws import broadcast_notifications
from api.services.order_service import OrderService

logger = logging.getLogger(__name__)


class CourierService:

    def list_providers(self):
        return CourierProvider.objects.all().order_by('name')

    def get_provider(self, pk) -> CourierProvider:
        return CourierProvider.objects.get(pk=pk)

    @transaction.atomic
    def send_order(self, order: SalesOrder, provider_id, user: User, weight=None) -> CourierConsignment:
        # Sending to courier is offered as an alternative to internal delivery
        # assignment (same "who delivers this" decision point), so it's only
        # valid from the same states assign_delivery() accepts from.
        if order.status not in ('PACKED', 'ASSIGNED'):
            raise ValidationError({
                'message_bn': 'শুধুমাত্র প্যাক করা বা এসাইন্ড অর্ডার কুরিয়ারে পাঠানো যায়',
                'message_en': 'Only packed or assigned orders can be sent to a courier',
            })
        if hasattr(order, 'courier_consignment'):
            raise ValidationError({
                'message_bn': 'এই অর্ডার ইতিমধ্যে কুরিয়ারে পাঠানো হয়েছে',
                'message_en': 'This order has already been sent to a courier',
            })

        try:
            provider = CourierProvider.objects.get(pk=provider_id, is_active=True)
        except CourierProvider.DoesNotExist:
            raise ValidationError({
                'message_bn': 'সক্রিয় কুরিয়ার প্রোভাইডার পাওয়া যায়নি',
                'message_en': 'Active courier provider not found',
            })

        service = get_courier_service(provider)
        try:
            result = service.create_order(order, weight)
        except Exception as e:
            logger.error(f'Courier send_order failed for {order.order_number}: {e}', exc_info=True)
            raise ValidationError({
                'message_bn': 'কুরিয়ারে পাঠাতে ব্যর্থ হয়েছে',
                'message_en': f'Failed to send to courier: {e}',
            })

        # Courier fulfills the same role as an internal delivery person here —
        # reuse the existing "assign without a person" transition so the order
        # moves through the same PACKED → ASSIGNED → ON_THE_WAY → DELIVERED
        # pipeline regardless of who's actually delivering it.
        if order.status != 'ASSIGNED':
            order = OrderService().assign_delivery(order, None, user)

        data = result.get('consignment', result)
        consignment = CourierConsignment.objects.create(
            order=order,
            provider=provider,
            consignment_id=str(data.get('consignment_id', '')),
            tracking_code=data.get('tracking_code', ''),
            status=data.get('status', ''),
            cod_amount=Decimal(str(data.get('cod_amount', 0) or 0)),
            raw_response=result,
            created_by=user,
        )
        CourierTrackingEvent.objects.create(
            consignment=consignment,
            status=consignment.status,
            message='Consignment created',
            source='POLL',
        )
        logger.info(f'Order {order.order_number} sent to {provider.code}: {consignment.tracking_code}')
        return consignment

    @transaction.atomic
    def refresh_status(self, consignment: CourierConsignment) -> CourierConsignment:
        service = get_courier_service(consignment.provider)
        try:
            result = service.check_status(consignment)
        except Exception as e:
            logger.error(f'Courier refresh_status failed for consignment {consignment.id}: {e}', exc_info=True)
            raise ValidationError({
                'message_bn': 'স্ট্যাটাস যাচাই করতে ব্যর্থ হয়েছে',
                'message_en': f'Failed to check status: {e}',
            })

        new_status = result.get('delivery_status', result.get('status', consignment.status))
        if new_status != consignment.status:
            consignment.status = new_status
            CourierTrackingEvent.objects.create(
                consignment=consignment, status=new_status,
                message='Status refreshed', source='POLL',
            )
        consignment.raw_response = {**consignment.raw_response, 'last_status_check': result}
        consignment.save(update_fields=['status', 'raw_response', 'updated_at'])
        return consignment

    def get_balance(self, provider: CourierProvider) -> dict:
        return get_courier_service(provider).get_balance()

    @transaction.atomic
    def create_return_request(self, consignment: CourierConsignment, reason: str, user: User) -> CourierReturnRequest:
        service = get_courier_service(consignment.provider)
        try:
            result = service.create_return_request(consignment, reason)
        except Exception as e:
            logger.error(f'Courier create_return_request failed: {e}', exc_info=True)
            raise ValidationError({
                'message_bn': 'ফেরত অনুরোধ ব্যর্থ হয়েছে',
                'message_en': f'Return request failed: {e}',
            })
        return CourierReturnRequest.objects.create(
            consignment=consignment,
            provider_request_id=str(result.get('id', '')),
            reason=reason,
            status=result.get('status', 'pending'),
            created_by=user,
        )

    def refresh_return_request(self, return_request: CourierReturnRequest) -> CourierReturnRequest:
        service = get_courier_service(return_request.consignment.provider)
        result = service.get_return_request(return_request.provider_request_id)
        return_request.status = result.get('status', return_request.status)
        return_request.save(update_fields=['status', 'updated_at'])
        return return_request

    def list_payments(self, provider: CourierProvider) -> dict:
        return get_courier_service(provider).list_payments()

    def get_payment(self, provider: CourierProvider, payment_id: str) -> dict:
        return get_courier_service(provider).get_payment(payment_id)

    def list_police_stations(self, provider: CourierProvider) -> dict:
        return get_courier_service(provider).list_police_stations()

    # ── Webhook ─────────────────────────────────────────────────────────────────

    @transaction.atomic
    def handle_webhook(self, payload: dict) -> None:
        """Steadfast pushes delivery_status / tracking_update notifications here.
        Updates the matching consignment's tracking info only — it deliberately
        never touches SalesOrder.status, since that transition (crediting
        cashback, posting accounting journals) stays an explicit admin action
        via the existing OrderService.deliver()/cancel_order().
        """
        consignment_id = str(payload.get('consignment_id', ''))
        invoice = payload.get('invoice', '')

        consignment = CourierConsignment.objects.filter(consignment_id=consignment_id).select_related('order').first()
        if not consignment and invoice:
            consignment = CourierConsignment.objects.filter(order__order_number=invoice).select_related('order').first()
        if not consignment:
            logger.warning(f'Courier webhook: no consignment found for consignment_id={consignment_id} invoice={invoice}')
            return

        notification_type = payload.get('notification_type', '')
        message = payload.get('tracking_message', '')

        if notification_type == 'delivery_status':
            consignment.status = payload.get('status', consignment.status)
            if 'cod_amount' in payload:
                consignment.cod_amount = Decimal(str(payload.get('cod_amount') or 0))
            if 'delivery_charge' in payload:
                consignment.delivery_charge = Decimal(str(payload.get('delivery_charge') or 0))
        consignment.tracking_message = message or consignment.tracking_message
        consignment.raw_response = {**consignment.raw_response, 'last_webhook': payload}
        consignment.save()

        CourierTrackingEvent.objects.create(
            consignment=consignment,
            status=consignment.status,
            message=message,
            source='WEBHOOK',
        )
        logger.info(f'Courier webhook applied to consignment {consignment.id} ({notification_type})')

        # Only fan out a notification for real status changes, not every
        # low-signal tracking_update ping (e.g. "arrived at sorting center").
        if notification_type == 'delivery_status':
            self._notify_admins(consignment)

    def _notify_admins(self, consignment: CourierConsignment) -> None:
        admins = User.objects.filter(role__code='ADMIN', is_active=True)
        order = consignment.order
        notifications = [
            Notification(
                user=admin,
                title_bn=f'কুরিয়ার স্ট্যাটাস — {order.order_number}',
                title_en=f'Courier Status — {order.order_number}',
                body_bn=f'{consignment.provider.name} জানিয়েছে: অর্ডার #{order.order_number} এখন **{consignment.status}**।',
                body_en=f'{consignment.provider.name} reports order #{order.order_number} is now **{consignment.status}**.',
                reference_type='COURIER_STATUS',
                reference_id=order.id,
            )
            for admin in admins
        ]
        Notification.objects.bulk_create(notifications)
        broadcast_notifications(notifications)
