import logging
from decimal import Decimal

from django.db import transaction
from rest_framework.exceptions import ValidationError

from api.models import (
    Account, CourierConsignment, CourierProvider, CourierReturnRequest, CourierTrackingEvent,
    JournalEntry, JournalLine, Notification, OrderStatusLog, SalesOrder, User,
)
from api.services import mail_service
from api.services.courier.registry import get_courier_service
from api.services.notification_recipients import get_notified_users
from api.services.notification_ws import broadcast_notifications
from api.services.order_service import OrderService
from api.services.telegram_service import send_courier_telegram_message
from api.utils.journal_number import next_entry_number

logger = logging.getLogger(__name__)


class CourierService:

    def list_providers(self):
        return CourierProvider.objects.all().order_by('name')

    def get_provider(self, pk) -> CourierProvider:
        return CourierProvider.objects.get(pk=pk)

    @transaction.atomic
    def send_order(self, order: SalesOrder, provider_id, user: User, weight=None, note=None) -> CourierConsignment:
        # No manual weight entry needed any more — Product.weight_kg lets
        # checkout estimate this already (order.estimated_weight_kg), so
        # that's what the courier API and our own delivery-charge
        # recalculation both use unless a manual weight is explicitly given.
        if weight is None:
            weight = order.estimated_weight_kg
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
    # own SalesOrder state machine. 'PICK' = ASSIGNED -> PICKED, 'DELIVER' =
    # -> DELIVERED (crediting cashback, posting the sale journal, marking COD
    # paid), 'RETURN' = DELIVERED -> RETURNED (reversing journal).
    # Steadfast has no separate "in transit" webhook status the way Pathao
    # does (order.picked vs order.in-transit as two distinct events) — its
    # 'pending' status covers everything from "handed to the courier" through
    # "out for delivery", so it's the only signal available for the
    # ASSIGNED -> PICKED waypoint; there's no finer-grained ON_THE_WAY
    # transition to map separately for Steadfast (see _apply_courier_status_
    # to_order's DELIVER branch, which backfills PICKED -> ON_THE_WAY itself
    # once a later 'delivered' webhook arrives).
    # hold/in_review/cancelled and the *_approval_pending variants still
    # deliberately map to nothing: cancellation isn't a reachable transition
    # once ASSIGNED (see ALLOWED_TRANSITIONS), and "approval pending" isn't
    # final yet, so those stay visible only in the tracking timeline until an
    # admin acts.
    #
    # partial_delivered deliberately maps to nothing too (not DELIVER) —
    # Steadfast only reports a lump collected amount (e.g. "Amount has been
    # changed from 690 to 130"), never which item failed, so auto-applying
    # DELIVER here would wrongly credit full COD/cashback for an order that
    # was only partially fulfilled. This just notifies the admin (via the
    # unconditional _notify_admins call below) to reconcile it manually via
    # OrderService.partial_deliver() instead — mirrors how Pathao's own
    # order.partial-delivery event is left unmapped for the same reason.
    _STEADFAST_STATUS_ACTIONS = {
        'pending': 'PICK',
        'delivered': 'DELIVER',
    }

    # Pathao's webhook "event" values -> the same action vocabulary as above,
    # now with 'PICK' for the dedicated ASSIGNED -> PICKED waypoint —
    # order.picked is the rider physically picking the package up from us.
    # order.at-the-sorting-hub also maps to PICK: Pathao doesn't reliably
    # send a discrete order.picked event for every parcel (confirmed from
    # real traffic), but reaching the sorting hub is itself proof the rider
    # already picked it up, so it's just as valid a signal — this way
    # PICKED shows up as soon as that happens rather than waiting for the
    # next event (in-transit/assigned-for-delivery, -> ON_THE_WAY) to
    # backfill it retroactively.
    # order.returned-to-merchant is the terminal event of Pathao's more
    # granular return flow (return-id-created -> return-in-transit ->
    # returned-to-merchant) and maps to the same RETURN action as the plain
    # order.returned event — whichever one a given store actually fires.
    _PATHAO_EVENT_ACTIONS = {
        'order.picked': 'PICK',
        'order.at-the-sorting-hub': 'PICK',
        'order.in-transit': 'DISPATCH',
        'order.assigned-for-delivery': 'DISPATCH',
        'order.delivered': 'DELIVER',
        'order.returned': 'RETURN',
        'order.returned-to-merchant': 'RETURN',
    }

    # Pathao's raw event slugs read poorly in an admin notification
    # ("order.in-transit") — human-friendly labels for every event Pathao's
    # webhook can send, matching their own dashboard's event names.
    _PATHAO_EVENT_LABELS = {
        'order.created': ('অর্ডার তৈরি হয়েছে', 'Order Created'),
        'order.updated': ('অর্ডার আপডেট হয়েছে', 'Order Updated'),
        'order.pickup-requested': ('পিকআপ অনুরোধ করা হয়েছে', 'Pickup Requested'),
        'order.assigned-for-pickup': ('পিকআপের জন্য নির্ধারিত', 'Assigned For Pickup'),
        'order.picked': ('পিকআপ হয়েছে', 'Picked Up'),
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
        'order.paid': ('পেমেন্ট হয়েছে', 'Paid'),
        'order.paid-return': ('পেইড রিটার্ন', 'Paid Return'),
        'order.exchanged': ('এক্সচেঞ্জ হয়েছে', 'Exchanged'),
        'order.return-id-created': ('রিটার্ন আইডি তৈরি হয়েছে', 'Return ID Created'),
        'order.return-in-transit': ('রিটার্ন ট্রানজিটে আছে', 'Return In Transit'),
        'order.returned-to-merchant': ('মার্চেন্টের কাছে ফেরত এসেছে', 'Returned to Merchant'),
    }

    # Structural/noise fields present on every Pathao webhook payload (either
    # already parsed explicitly, or carrying no useful per-event info, like
    # updated_at/timestamp/store_id) — anything else Pathao includes
    # (invoice_id, return_consignment_id, return_type, etc.) is unknown ahead
    # of time and varies by event, so rather than guessing field names we
    # surface whatever's left over verbatim (see _pathao_extra_note below)
    # instead of silently dropping it.
    _PATHAO_KNOWN_KEYS = {
        'consignment_id', 'merchant_order_id', 'event', 'collected_amount',
        'delivery_fee', 'reason', 'updated_at', 'timestamp', 'store_id',
    }

    def _pathao_extra_note(self, payload: dict) -> str:
        extra = {
            k: v for k, v in payload.items()
            if k not in self._PATHAO_KNOWN_KEYS and v not in (None, '', [], {})
        }
        return ', '.join(f'{k.replace("_", " ").title()}: {v}' for k, v in extra.items())

    def _get_system_user(self) -> User:
        return User.objects.filter(role__code='ADMIN').first()

    def _acct(self, code: str):
        try:
            return Account.objects.get(code=code)
        except Account.DoesNotExist:
            return None

    def _post_delivery_expense_if_needed(self, consignment: CourierConsignment, user: User | None) -> None:
        """The delivery_charge a courier reports (Pathao's delivery_fee /
        Steadfast's delivery_charge) is what they deduct from the COD they
        collect before remitting the rest — it's a real cost to the
        business, not part of your income, but order_service's PAYMENT
        journal (posted at deliver()) books the FULL grand_total as Cash
        received, with no visibility into what the courier will end up
        keeping. This posts that cost as a separate expense once the
        courier tells us the number, rather than trying to net it into the
        payment journal (which may well have already been posted by the
        time this webhook data arrives). Only ever fires for a courier
        consignment — internal/self delivery has no CourierConsignment at
        all, so its full delivery charge stays 100% income, untouched.
        Guarded against the same webhook firing more than once for the same
        consignment.

        Also books a COD collection fee (CourierProvider.cod_fee_percent of
        consignment.cod_amount) when the provider charges one — e.g. Pathao
        deducts a cash-handling cut on top of the delivery fee itself before
        remitting a COD order's proceeds. 0% by default, so a provider with
        no such fee configured sees no change here at all."""
        cod_fee = (consignment.cod_amount * consignment.provider.cod_fee_percent / 100).quantize(Decimal('0.01')) \
            if consignment.provider.cod_fee_percent > 0 and consignment.cod_amount > 0 else Decimal('0')
        if consignment.delivery_charge <= 0 and cod_fee <= 0:
            return
        if JournalEntry.objects.filter(reference_type='EXPENSE', reference_id=consignment.order_id).exists():
            return
        if not user:
            logger.error(
                f'Courier delivery expense (৳{consignment.delivery_charge} + COD fee ৳{cod_fee}) known for order '
                f'{consignment.order.order_number} but no ADMIN-role user exists — no journal posted. '
                f'Needs manual reconciliation.'
            )
            return
        entry = JournalEntry.objects.create(
            entry_number=next_entry_number(), reference_type='EXPENSE',
            reference_id=consignment.order_id,
            description_bn=f'কুরিয়ার ডেলিভারি খরচ — {consignment.order.order_number}',
            description_en=f'Courier Delivery Expense — {consignment.order.order_number}',
            created_by=user, is_posted=True,
        )
        lines = []
        if consignment.delivery_charge > 0:
            lines.append(('6500', consignment.delivery_charge, Decimal('0')))  # Dr Delivery Expense
        if cod_fee > 0:
            lines.append(('6550', cod_fee, Decimal('0')))  # Dr Courier COD Fee
        total_cost = consignment.delivery_charge + cod_fee
        lines.append(('1000', Decimal('0'), total_cost))  # Cr Cash (courier's total cut of the COD)
        for code, debit, credit in lines:
            acct = self._acct(code)
            if acct:
                JournalLine.objects.create(journal_entry=entry, account=acct, debit=debit, credit=credit)
        logger.info(
            f'Delivery expense ৳{consignment.delivery_charge} (+ COD fee ৳{cod_fee}) posted for order '
            f'{consignment.order.order_number}'
        )

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
                # A later-stage event (in-transit, at-the-sorting-hub, ...)
                # arriving while still ASSIGNED means the courier skipped
                # sending a discrete pickup event — the package obviously
                # was picked up regardless, so backfill PICKED first rather
                # than jumping straight to ON_THE_WAY and losing that step.
                if order.status == 'ASSIGNED':
                    order = order_svc.pick_up(order, user)
                order_svc.dispatch(order, user)
            elif action == 'DELIVER' and order.status in ('ASSIGNED', 'PICKED', 'ON_THE_WAY'):
                if order.status == 'ASSIGNED':
                    order = order_svc.pick_up(order, user)
                if order.status in ('ASSIGNED', 'PICKED'):
                    order = order_svc.dispatch(order, user)
                delivered = order_svc.deliver(order, user)
                mail_service.send_order_delivered(delivered)
            elif action == 'RETURN' and order.status == 'DELIVERED':
                returned = order_svc.return_order(order, user)
                mail_service.send_order_returned(returned)
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

        # Auto-transition only applies for a real status payload
        # (delivery_status) — any other notification_type has no actual
        # status in it, just stale/leftover data, so it must never drive an
        # order transition. Notifying admins, on the other hand, happens for
        # every webhook hit no matter the type — _notify_admins shows the
        # real tracking message whenever Steadfast actually sent one (e.g.
        # partial_delivered's "Amount has been changed from X to Y" — the
        # exact detail an admin needs to reconcile it manually), falling
        # back to the generic "now **status**" wording only when there's no
        # message to show (a plain delivered/dispatched hit).
        if notification_type == 'delivery_status':
            action = self._STEADFAST_STATUS_ACTIONS.get(raw_status)
            # raw_status alone can't tell PICKED apart from ON_THE_WAY —
            # Steadfast reports 'pending' for both "processing for delivery"
            # and an inter-hub handoff ("Consignment sent to CHITTAGONG
            # WAREHOUSE, Dispatch ID: ..."), so the warehouse transfer has to
            # be read off tracking_message instead. Overrides the plain PICK
            # mapping since a hub transfer is strictly further along than
            # just being picked up. _apply_courier_status_to_order's DISPATCH
            # branch backfills PICKED first if the order is still ASSIGNED,
            # so this is safe even if Steadfast skipped a discrete pickup
            # message entirely.
            if raw_status == 'pending' and 'sent to' in message.lower():
                action = 'DISPATCH'
            self._apply_courier_status_to_order(consignment, action)
            # The courier's reported delivery_charge is only the REAL,
            # final cost once the parcel is actually delivered — an
            # intermediate status update (e.g. "in review", a hub transfer)
            # can carry a delivery_charge field too, and posting the
            # expense off that would book it before the order is DELIVERED.
            # Gated to the same action as the self-delivery expense (only
            # ever posted from OrderService.deliver()).
            if action == 'DELIVER':
                self._post_delivery_expense_if_needed(consignment, self._get_system_user())
        self._notify_admins(consignment, tracking_message=message)

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

        extra_note = self._pathao_extra_note(payload)
        message = payload.get('reason', '') or extra_note
        CourierTrackingEvent.objects.create(
            consignment=consignment,
            status=event,
            message=message,
            source='WEBHOOK',
        )
        logger.info(f'Pathao webhook applied to consignment {consignment.id} ({event})')

        action = self._PATHAO_EVENT_ACTIONS.get(event)
        self._apply_courier_status_to_order(consignment, action)
        # order.assigned-for-delivery means a rider has actually been handed
        # the parcel for the last-mile drop — a meaningfully different
        # moment from the generic "In Transit" (order.in-transit) that
        # usually already got the order to ON_THE_WAY, but one our own
        # state machine has no separate status for (it's still just
        # ON_THE_WAY). Logged as its own informational timeline entry
        # instead of a real SalesOrder.status transition, so the customer
        # sees "Rider Assigned" without inventing a new order status this
        # app-wide state machine would otherwise need to know about.
        # Steadfast has no equivalent granular signal (see
        # _STEADFAST_STATUS_ACTIONS' notes on its single 'pending' status
        # covering this whole span), so this is Pathao-only by construction.
        if event == 'order.assigned-for-delivery' and not consignment.order.status_logs.filter(to_status='RIDER_ASSIGNED').exists():
            OrderStatusLog.objects.create(
                order=consignment.order,
                from_status=consignment.order.status,
                to_status='RIDER_ASSIGNED',
                changed_by=self._get_system_user(),
            )
        # Pathao includes a delivery_fee on intermediate events too (e.g.
        # order.assigned-for-delivery, fired when a rider is assigned —
        # well before the parcel is actually delivered), not just on
        # order.delivered. Posting the expense off any webhook that merely
        # carries the field would book it before the order is DELIVERED —
        # gated to the same action as the self-delivery expense (only ever
        # posted from OrderService.deliver()).
        if action == 'DELIVER':
            self._post_delivery_expense_if_needed(consignment, self._get_system_user())
        # Every webhook hit notifies admins, no exceptions — including
        # order.created, even though that moment is also visible immediately
        # in the UI response to "Send to Courier" (this is Pathao's own
        # independent confirmation of the same thing, worth surfacing too).
        # extra_note surfaces any extra field Pathao sent on this event
        # (rider info, hub, etc.) alongside the usual "now **status**" label.
        self._notify_admins(consignment, extra_note=extra_note)

    def notify_webhook_verified(self, provider: CourierProvider) -> None:
        """Fired for the one-time webhook_integration handshake — no order/
        consignment involved (Pathao's dashboard just pinging to confirm the
        URL is reachable and correctly configured), so this is purely an
        informational ping for admins, not tied to any order."""
        admins = get_notified_users()
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
        send_courier_telegram_message(f'🔔🔔Webhook Verified🔔🔔\nCourier: {provider_short}')

    def _notify_admins(self, consignment: CourierConsignment, tracking_message: str = '', extra_note: str = '') -> None:
        """tracking_message: for a low-signal update with no actual status
        change (Steadfast's notification_type=tracking_update — a note like
        "customer asked to deliver to the office" rather than a status
        transition), showing that note is far more useful than repeating an
        unchanged status label. Falls back to the usual "now **status**"
        wording when there's no such note (the normal delivery_status /
        Pathao-event case). extra_note: additional detail (e.g. rider name/
        phone Pathao included on this event) appended alongside whichever
        wording above was used, rather than replacing it."""
        admins = get_notified_users()
        order = consignment.order
        provider_short = consignment.provider.code.title()
        if tracking_message:
            body_bn = f'{provider_short}: অর্ডার #{order.order_number} — {tracking_message}'
            body_en = f'{provider_short}: Order #{order.order_number} — {tracking_message}'
        else:
            label_bn, label_en = self._PATHAO_EVENT_LABELS.get(consignment.status, (consignment.status, consignment.status))
            body_bn = f'{provider_short}: অর্ডার #{order.order_number} এখন **{label_bn}**।'
            body_en = f'{provider_short}: Order #{order.order_number} is now **{label_en}**.'
        if extra_note:
            body_bn += f' ({extra_note})'
            body_en += f' ({extra_note})'
        notifications = [
            Notification(
                user=admin,
                title_bn=f'কুরিয়ার স্ট্যাটাস — {order.order_number}',
                title_en=f'Courier Status — {order.order_number}',
                body_bn=body_bn,
                body_en=body_en,
                reference_type='COURIER_STATUS',
                reference_id=order.id,
            )
            for admin in admins
        ]
        Notification.objects.bulk_create(notifications)
        broadcast_notifications(notifications)

        # Telegram-only layout (Bengali) — 🚚 [Provider] অর্ডার X এখন <status>,
        # then Tracking Code, then who/where to deliver to, then the rider
        # note (if Pathao sent one) last. Deliberately no Email/Payment
        # Method/Amount here — those matter for the order-lifecycle
        # messages, not for a delivery-logistics ping. tracking_message has
        # no Bengali translation (raw courier text), shown as-is either way.
        status_text_bn = tracking_message or label_bn
        name = order.shipping_name_bn or order.shipping_name_en or 'অতিথি'
        address = order.shipping_address_bn or order.shipping_address_en or ''
        location = ', '.join(p for p in [order.shipping_thana, order.shipping_district] if p)
        full_address = ', '.join(p for p in [address, location] if p)
        lines = [f'🚚 [{provider_short}] অর্ডার {order.order_number} এখন {status_text_bn}']
        if consignment.tracking_code:
            lines.append(f'ট্র্যাকিং কোড: {consignment.tracking_code}')
        lines.append(f'নাম: {name}')
        lines.append(f'ফোন: {order.shipping_phone or "-"}')
        lines.append(f'ঠিকানা: {full_address}')
        if extra_note:
            lines.append(f'নোট: {extra_note}')
        send_courier_telegram_message('\n'.join(lines))
