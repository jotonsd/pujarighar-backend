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
    def send_order(self, order: SalesOrder, provider_id, user: User, weight=None, note=None) -> CourierConsignment:
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
            result = service.create_order(order, weight, note)
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
        order_svc = OrderService()
        if order.status != 'ASSIGNED':
            order = order_svc.assign_delivery(order, None, user, weight)
        else:
            # assign_delivery() (which also recalculates delivery charge from
            # weight) only runs on the transition above — if the order was
            # already ASSIGNED, that branch never fires, so recalculate here.
            order_svc.recalculate_delivery_charge(order, weight)

        data = result.get('consignment', result)
        consignment = CourierConsignment.objects.create(
            order=order,
            provider=provider,
            consignment_id=str(data.get('consignment_id', '')),
            tracking_code=data.get('tracking_code', ''),
            status=data.get('status', ''),
            cod_amount=Decimal(str(data.get('cod_amount', 0) or 0)),
            weight=Decimal(str(weight)) if weight else None,
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

    # Steadfast's documented delivery_status values -> what that means for our
    # own SalesOrder state machine. 'DISPATCH' = ASSIGNED -> ON_THE_WAY,
    # 'DELIVER' = -> DELIVERED (crediting cashback, posting the sale journal,
    # marking COD paid), 'RETURN' = DELIVERED -> RETURNED (reversing journal).
    # Steadfast has no separate "in transit" webhook status (coarser than
    # Pathao) — pending/hold/in_review/cancelled and the *_approval_pending
    # variants deliberately map to nothing here: cancellation isn't a
    # reachable transition once ASSIGNED (see ALLOWED_TRANSITIONS), and
    # "approval pending" isn't final yet, so those stay visible only in the
    # tracking timeline until an admin acts.
    _STEADFAST_STATUS_ACTIONS = {
        'delivered': 'DELIVER',
        'partial_delivered': 'DELIVER',
    }

    # Pathao's webhook "event" values -> the same action vocabulary as above,
    # now with 'PICK' for the dedicated ASSIGNED -> PICKED waypoint —
    # order.pickup is the rider physically picking the package up from us,
    # distinct from order.in-transit/assigned-for-delivery (later, -> ON_THE_WAY).
    _PATHAO_EVENT_ACTIONS = {
        'order.pickup': 'PICK',
        'order.in-transit': 'DISPATCH',
        'order.assigned-for-delivery': 'DISPATCH',
        'order.delivered': 'DELIVER',
        'order.returned': 'RETURN',
    }

    # Pathao's raw event slugs read poorly in an admin notification
    # ("order.in-transit") — human-friendly labels for every event Pathao's
    # webhook can send, matching their own dashboard's event names.
    _PATHAO_EVENT_LABELS = {
        'order.created': ('অর্ডার তৈরি হয়েছে', 'Order Created'),
        'order.updated': ('অর্ডার আপডেট হয়েছে', 'Order Updated'),
        'order.pickup-requested': ('পিকআপ অনুরোধ করা হয়েছে', 'Pickup Requested'),
        'order.assigned-for-pickup': ('পিকআপের জন্য নির্ধারিত', 'Assigned For Pickup'),
        'order.pickup': ('পিকআপ হয়েছে', 'Picked Up'),
        'order.pickup-failed': ('পিকআপ ব্যর্থ হয়েছে', 'Pickup Failed'),
        'order.pickup-cancelled': ('পিকআপ বাতিল হয়েছে', 'Pickup Cancelled'),
        'order.at-the-sorting-hub': ('সর্টিং হাবে পৌঁছেছে', 'At the Sorting Hub'),
        'order.in-transit': ('ট্রানজিটে আছে', 'In Transit'),
        'order.received-at-last-mile-hub': ('লাস্ট মাইল হাবে পৌঁছেছে', 'Received at Last Mile Hub'),
        'order.assigned-for-delivery': ('ডেলিভারির জন্য নির্ধারিত', 'Assigned for Delivery'),
        'order.delivered': ('ডেলিভারি সম্পন্ন হয়েছে', 'Delivered'),
        'order.partial-delivery': ('আংশিক ডেলিভারি হয়েছে', 'Partial Delivery'),
        'order.returned': ('ফেরত এসেছে', 'Returned'),
        'order.delivery-failed': ('ডেলিভারি ব্যর্থ হয়েছে', 'Delivery Failed'),
        'order.on-hold': ('হোল্ডে আছে', 'On Hold'),
        'order.payment-invoice': ('পেমেন্ট ইনভয়েস', 'Payment Invoice'),
        'order.paid-return': ('পেইড রিটার্ন', 'Paid Return'),
        'order.exchange': ('এক্সচেঞ্জ', 'Exchange'),
    }

    def _get_system_user(self) -> User:
        return User.objects.filter(role__code='ADMIN').first()

    def _apply_courier_status_to_order(self, consignment: CourierConsignment, action: str | None) -> None:
        """Shared by both the Steadfast and Pathao webhook handlers, so a
        courier reporting "delivered"/"returned"/"in transit" auto-advances
        SalesOrder.status identically regardless of which one it was —
        reuses the exact same OrderService methods (and their cashback/
        accounting/referral side effects) the manual admin buttons call.
        Silently no-ops if the order isn't currently in a state that
        transition is valid from (e.g. a stray "delivered" event arriving
        for an order that's already CANCELLED) rather than raising, since a
        webhook that doesn't cleanly apply shouldn't break processing the
        rest of the payload."""
        if not action:
            return
        order = consignment.order
        user = self._get_system_user()
        order_svc = OrderService()
        try:
            if action == 'PICK' and order.status == 'ASSIGNED':
                order_svc.pick_up(order, user)
            elif action == 'DISPATCH' and order.status in ('ASSIGNED', 'PICKED'):
                order_svc.dispatch(order, user)
            elif action == 'DELIVER' and order.status in ('ASSIGNED', 'PICKED', 'ON_THE_WAY'):
                if order.status in ('ASSIGNED', 'PICKED'):
                    order = order_svc.dispatch(order, user)
                order_svc.deliver(order, user)
            elif action == 'RETURN' and order.status == 'DELIVERED':
                order_svc.return_order(order, user)
        except Exception as e:
            logger.warning(f'Courier webhook: could not auto-apply {action} to order {order.order_number}: {e}')

    @transaction.atomic
    def handle_webhook(self, payload: dict) -> None:
        """Steadfast pushes delivery_status / tracking_update notifications
        here. Updates the matching consignment's tracking info, and — for a
        final delivery_status (delivered/partial_delivered) — auto-advances
        the order itself via _apply_courier_status_to_order, same as Pathao's
        handle_pathao_webhook below."""
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
        raw_status = payload.get('status', consignment.status)

        if notification_type == 'delivery_status':
            consignment.status = raw_status
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

        if notification_type == 'delivery_status':
            self._apply_courier_status_to_order(consignment, self._STEADFAST_STATUS_ACTIONS.get(raw_status))
            # Only fan out a notification for real status changes, not every
            # low-signal tracking_update ping (e.g. "arrived at sorting center").
            self._notify_admins(consignment)

    @transaction.atomic
    def handle_pathao_webhook(self, payload: dict) -> None:
        """Pathao pushes one event per status change (order.created,
        order.in-transit, order.assigned-for-delivery, order.delivered,
        order.returned, ...). Same shape as handle_webhook above: update the
        consignment/tracking timeline, then auto-advance SalesOrder.status
        via the shared mapping where applicable."""
        consignment_id = str(payload.get('consignment_id', ''))
        merchant_order_id = payload.get('merchant_order_id', '')
        event = payload.get('event', '')

        consignment = CourierConsignment.objects.filter(consignment_id=consignment_id).select_related('order').first()
        if not consignment and merchant_order_id:
            consignment = CourierConsignment.objects.filter(order__order_number=merchant_order_id).select_related('order').first()
        if not consignment:
            logger.warning(f'Pathao webhook: no consignment found for consignment_id={consignment_id} merchant_order_id={merchant_order_id}')
            return

        consignment.status = event or consignment.status
        if 'collected_amount' in payload:
            consignment.cod_amount = Decimal(str(payload.get('collected_amount') or 0))
        if 'delivery_fee' in payload:
            consignment.delivery_charge = Decimal(str(payload.get('delivery_fee') or 0))
        consignment.raw_response = {**consignment.raw_response, 'last_webhook': payload}
        consignment.save()

        CourierTrackingEvent.objects.create(
            consignment=consignment,
            status=event,
            message=payload.get('reason', ''),
            source='WEBHOOK',
        )
        logger.info(f'Pathao webhook applied to consignment {consignment.id} ({event})')

        self._apply_courier_status_to_order(consignment, self._PATHAO_EVENT_ACTIONS.get(event))
        if event != 'order.created':
            self._notify_admins(consignment)

    def notify_webhook_verified(self, provider: CourierProvider) -> None:
        """Fired for the one-time webhook_integration handshake — no order/
        consignment involved (Pathao's dashboard just pinging to confirm the
        URL is reachable and correctly configured), so this is purely an
        informational ping for admins, not tied to any order."""
        admins = User.objects.filter(role__code='ADMIN', is_active=True)
        provider_short = provider.code.title()
        notifications = [
            Notification(
                user=admin,
                title_bn=f'ওয়েবহুক ভেরিফাই হয়েছে — {provider_short}',
                title_en=f'Webhook Verified — {provider_short}',
                body_bn=f'{provider_short}: আপনার ওয়েবহুক ইউআরএল সফলভাবে ভেরিফাই করেছে।',
                body_en=f'{provider_short}: successfully verified your webhook URL.',
                reference_type='COURIER_WEBHOOK_VERIFIED',
            )
            for admin in admins
        ]
        Notification.objects.bulk_create(notifications)
        broadcast_notifications(notifications)

    def _notify_admins(self, consignment: CourierConsignment) -> None:
        admins = User.objects.filter(role__code='ADMIN', is_active=True)
        order = consignment.order
        label_bn, label_en = self._PATHAO_EVENT_LABELS.get(consignment.status, (consignment.status, consignment.status))
        provider_short = consignment.provider.code.title()
        notifications = [
            Notification(
                user=admin,
                title_bn=f'কুরিয়ার স্ট্যাটাস — {order.order_number}',
                title_en=f'Courier Status — {order.order_number}',
                body_bn=f'{provider_short}: অর্ডার #{order.order_number} এখন **{label_bn}**।',
                body_en=f'{provider_short}: Order #{order.order_number} is now **{label_en}**.',
                reference_type='COURIER_STATUS',
                reference_id=order.id,
            )
            for admin in admins
        ]
        Notification.objects.bulk_create(notifications)
        broadcast_notifications(notifications)
