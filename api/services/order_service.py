import logging
from decimal import Decimal
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from api.models import (
    SalesOrder, SalesOrderItem, OrderStatusLog, DeliveryAssignment, User,
    StockMovement, Account, JournalEntry, JournalLine, Notification,
    ReferralBonus, SiteSetting, ProductPackageItem, DeliveryCharge,
    Exchange, ExchangeItem, CashbackTier,
)
from api.utils.dates import local_day_start, local_day_end_exclusive
from api.utils.order_number import generate_order_number
from api.utils.journal_number import next_entry_number
from api.services.notification_ws import broadcast_notification

# Same district-set checkout_service.py/guest_service.py use to pick a zone
# when the customer didn't explicitly choose one — the order itself has no
# stored zone to recompute from later, so this is re-derived the same way.
_DHAKA_DISTRICTS = {'dhaka', 'ঢাকা'}

logger = logging.getLogger(__name__)


class OrderService:

    def list_orders(self, user: User, params: dict):
        role = user.role.code
        # Matched to exactly what SalesOrderSerializer walks for a list page:
        # items -> product -> images (product_image) and -> package_items ->
        # component (for package products), delivery -> delivery_person ->
        # profile, courier_consignment -> provider and -> events (tracking
        # history). Without these, each was a separate query PER ORDER ROW
        # (N+1 — some of them N+1 *inside* an N+1, for package items) — the
        # actual cause of a slow list page, not the page size itself.
        # status_logs was being prefetched here too but the list serializer
        # never reads it — that was a wasted query on every single page load.
        qs = (
            SalesOrder.objects
            .select_related(
                'customer', 'delivery', 'delivery__delivery_person', 'delivery__delivery_person__profile',
                'courier_consignment', 'courier_consignment__provider',
            )
            .prefetch_related(
                'items__product__images',
                'items__product__package_items__component',
                'courier_consignment__events',
            )
        )

        if role == 'CUSTOMER':
            qs = qs.filter(customer=user)
        elif role == 'WAREHOUSE':
            pass
        elif role == 'DELIVERY':
            qs = qs.filter(delivery__delivery_person=user)

        if params.get('status'):
            qs = qs.filter(status=params['status'])
        if params.get('payment_status'):
            qs = qs.filter(payment_status=params['payment_status'])
        if params.get('order_number'):
            qs = qs.filter(order_number__icontains=params['order_number'])
        if params.get('phone'):
            qs = qs.filter(shipping_phone__icontains=params['phone'])
        if params.get('name'):
            qs = qs.filter(shipping_name_bn__icontains=params['name'])
        if params.get('customer') and role == 'ADMIN':
            qs = qs.filter(customer_id=params['customer'])
        if params.get('from'):
            qs = qs.filter(created_at__gte=local_day_start(params['from']))
        if params.get('to'):
            qs = qs.filter(created_at__lt=local_day_end_exclusive(params['to']))
        return qs

    def get_sales_report(self, params: dict) -> dict:
        """Order-level sales listing for the admin Reports menu — one row per
        order (the natural unit for "what did we sell and to whom"), distinct
        from Sales Summary (period-aggregated chart) and the Income Report
        (ledger entries, not orders). Same date/payment filter conventions as
        the other Reports-menu endpoints (purchases, supplier returns)."""
        qs = SalesOrder.objects.select_related('customer').prefetch_related('items')

        if params.get('from'):
            qs = qs.filter(created_at__gte=local_day_start(params['from']))
        if params.get('to'):
            qs = qs.filter(created_at__lt=local_day_end_exclusive(params['to']))
        if params.get('payment_method'):
            qs = qs.filter(payment_method=params['payment_method'])
        if params.get('payment_status'):
            qs = qs.filter(payment_status=params['payment_status'])
        if params.get('status'):
            qs = qs.filter(status=params['status'])

        rows = []
        total_amount = Decimal('0')
        for order in qs.order_by('-created_at'):
            total_amount += order.grand_total
            rows.append({
                'id': str(order.id),
                'date': order.created_at.isoformat(),
                'order_number': order.order_number,
                'customer_name': order.shipping_name_bn or order.shipping_name_en,
                'phone': order.shipping_phone,
                'payment_method': order.payment_method,
                'payment_status': order.payment_status,
                'status': order.status,
                # len(...all()) reuses the prefetch_related cache — .count()
                # on a related manager always issues its own fresh query
                # regardless of prefetching, which would mean one extra query
                # per order in the report.
                'items_count': len(order.items.all()),
                'subtotal': str(order.subtotal),
                'discount_amount': str(order.discount_amount),
                'delivery_charge': str(order.delivery_charge),
                'grand_total': str(order.grand_total),
            })

        return {
            'rows': rows,
            'total_orders': len(rows),
            'total_amount': str(total_amount),
        }

    def find_recent_shipping_by_phone(self, phone: str) -> SalesOrder | None:
        """POS auto-fill fallback for repeat guest customers — lookup_user_by_phone
        only finds people with an actual registered account, but most walk-in/
        phone orders are guest checkouts with no User row at all. This searches
        past orders' shipping snapshot directly instead, regardless of whether
        the order was placed as a guest or a registered customer."""
        return (
            SalesOrder.objects.filter(shipping_phone=phone)
            .order_by('-created_at')
            .first()
        )

    def get_order(self, pk: str) -> SalesOrder:
        return SalesOrder.objects.prefetch_related('items__product__images', 'status_logs', 'delivery').get(pk=pk)

    def confirm(self, order: SalesOrder, user: User) -> SalesOrder:
        return self._transition(order, 'CONFIRMED', user)

    def pack(self, order: SalesOrder, user: User) -> SalesOrder:
        return self._transition(order, 'PACKED', user)

    @transaction.atomic
    def assign_delivery(self, order: SalesOrder, delivery_person_id: str | None, user: User, weight: Decimal = None, note: str = '') -> SalesOrder:
        delivery_person = None
        if delivery_person_id:
            delivery_person = User.objects.get(id=delivery_person_id, role__code='DELIVERY')
        defaults = {'delivery_person': delivery_person}
        if note:
            defaults['tracking_note'] = note
        DeliveryAssignment.objects.update_or_create(order=order, defaults=defaults)
        # Already ASSIGNED (e.g. assigned earlier without a delivery person) — just
        # attaching/updating the delivery person now, no status transition needed.
        updated = order if order.status == 'ASSIGNED' else self._transition(order, 'ASSIGNED', user)
        self.recalculate_delivery_charge(updated, weight)
        if delivery_person:
            self._notify_delivery_person(updated, delivery_person)
        return updated

    def recalculate_delivery_charge(self, order: SalesOrder, weight: Decimal = None) -> SalesOrder:
        """Re-price delivery for a given package weight — checkout already
        prices delivery_charge from order.estimated_weight_kg (see
        CheckoutService), so this only matters when a manual weight override
        is explicitly passed in (e.g. the courier flow, when it differs from
        the estimate). A no-op unless a weight was actually given, and
        skipped once the order is
        already PAID (changing an already-collected amount would desync the
        books — same reasoning waive_delivery_charge applies elsewhere), so
        this never blocks assignment itself, it just silently leaves the
        charge as-is in that case."""
        if not weight or order.payment_status == 'PAID':
            return order

        zone = 'inside' if (order.shipping_district or '').strip().lower() in _DHAKA_DISTRICTS else 'outside'
        new_charge = DeliveryCharge.get().charge_for(zone, weight)
        if new_charge == order.delivery_charge:
            return order

        order.delivery_charge = new_charge
        order.grand_total = order.subtotal + order.delivery_charge + order.tax_amount - order.cashback_used
        order.save(update_fields=['delivery_charge', 'grand_total'])
        self._resync_order_item_journal(order)
        logger.info(f'Delivery charge recalculated for order {order.order_number}: ৳{new_charge} (weight={weight}kg, zone={zone})')
        return order

    def pick_up(self, order: SalesOrder, user: User) -> SalesOrder:
        order = self._transition(order, 'PICKED', user)
        order.delivery.picked_up_at = timezone.now()
        order.delivery.save(update_fields=['picked_up_at'])
        return order

    def dispatch(self, order: SalesOrder, user: User) -> SalesOrder:
        order = self._transition(order, 'ON_THE_WAY', user)
        # Only set picked_up_at here if pick_up() didn't already (ASSIGNED
        # can go straight to ON_THE_WAY, skipping PICKED entirely — see
        # ALLOWED_TRANSITIONS) — don't clobber the real, earlier pickup
        # timestamp with "now" if it was already recorded.
        if not order.delivery.picked_up_at:
            order.delivery.picked_up_at = timezone.now()
            order.delivery.save(update_fields=['picked_up_at'])
        return order

    @transaction.atomic
    def deliver(self, order: SalesOrder, user: User) -> SalesOrder:
        order = self._transition(order, 'DELIVERED', user)
        order.delivery.delivered_at = timezone.now()
        order.delivery.save(update_fields=['delivered_at'])
        if order.payment_method == 'COD' and order.payment_status == 'UNPAID':
            order.payment_status = 'PAID'
            order.save(update_fields=['payment_status'])
        if not JournalEntry.objects.filter(reference_type='PAYMENT', reference_id=order.id).exists():
            self._create_payment_journal(order, user)
        # Self-delivery (no courier involved) pays the rider the full
        # delivery charge collected from the customer — a pass-through, not
        # income — so it's expensed here too. Courier-delivered orders get
        # their own expense posted separately once the courier reports
        # their actual fee (see CourierService._post_delivery_expense_if_needed).
        if not hasattr(order, 'courier_consignment'):
            self._create_self_delivery_expense_journal(order, user)
        # Credit cashback earned to customer's balance
        cb = Decimal(str(order.cashback_amount or 0))
        if cb > 0 and not order.is_guest and order.customer_id:
            order.customer.profile.cashback_balance = F('cashback_balance') + cb
            order.customer.profile.save(update_fields=['cashback_balance'])
            self._create_cashback_earned_journal(order, cb, user)
        # Credit referral bonus to referrer (one-time per referred user)
        self._process_referral_bonus(order, user)
        return order

    @transaction.atomic
    def mark_cod_paid(self, order: SalesOrder, user: User) -> SalesOrder:
        if order.payment_method != 'COD':
            raise ValidationError({
                'message_bn': 'শুধুমাত্র ক্যাশ অন ডেলিভারি অর্ডারের জন্য প্রযোজ্য',
                'message_en': 'Only applicable for Cash on Delivery orders',
            })
        if order.payment_status == 'PAID':
            raise ValidationError({
                'message_bn': 'এই অর্ডার ইতিমধ্যে পরিশোধিত',
                'message_en': 'Order is already paid',
            })
        order.payment_status = 'PAID'
        order.save(update_fields=['payment_status'])
        # Create payment journal only if one doesn't exist yet for this order
        if not JournalEntry.objects.filter(reference_type='PAYMENT', reference_id=order.id).exists():
            self._create_payment_journal(order, user)
        logger.info(f'COD payment marked for order {order.order_number} by {user.email}')
        return order

    @transaction.atomic
    def apply_discount(self, order: SalesOrder, discount_type: str, discount_value: Decimal, user: User) -> SalesOrder:
        if order.status not in ('PENDING', 'CONFIRMED'):
            raise ValidationError({
                'message_bn': 'শুধুমাত্র পেন্ডিং বা নিশ্চিত অর্ডারে ছাড় প্রয়োগ করা যায়',
                'message_en': 'Discount can only be applied to pending or confirmed orders',
            })
        if order.payment_status == 'PAID':
            raise ValidationError({
                'message_bn': 'পরিশোধিত অর্ডারে ছাড় প্রয়োগ করা যাবে না',
                'message_en': 'Discount cannot be applied to an already-paid order',
            })
        if discount_value <= 0 or (discount_type == 'PERCENTAGE' and discount_value > 100):
            raise ValidationError({
                'message_bn': 'সঠিক ছাড়ের পরিমাণ দিন',
                'message_en': 'Enter a valid discount value',
            })

        # Same POS staff-discount calculation used at checkout (guest_service.py) —
        # layered on top of whatever's already in subtotal, clamped so the
        # order can't go negative. Stored in staff_discount_amount (not just
        # folded into subtotal/discount_amount directly) so it survives a
        # later item add/quantity-change/removal — see _recalc_order_totals.
        extra_discount = Decimal('0')
        if discount_type == 'PERCENTAGE':
            extra_discount = (order.subtotal * discount_value / 100).quantize(Decimal('0.01'))
        elif discount_type == 'FLAT':
            extra_discount = discount_value
        extra_discount = min(extra_discount, order.subtotal)

        order.staff_discount_amount = (order.staff_discount_amount or Decimal('0')) + extra_discount
        order.save(update_fields=['staff_discount_amount'])
        self._recalc_order_totals(order)
        # A non-COD order posts its SALE journal immediately at checkout —
        # before payment is even confirmed, while still PENDING/UNPAID, which
        # is exactly the window a discount can be applied in. Without this,
        # the journal's Revenue/AR would stay stale at the pre-discount
        # amount while order.subtotal/grand_total move to the discounted
        # figure. No-ops if no SALE/PAYMENT journal exists yet (the common
        # COD case) — same as the other item-editing methods.
        self._resync_order_item_journal(order)

        logger.info(f'Discount applied to order {order.order_number} by {user.email}: {discount_type} {discount_value} (৳{extra_discount})')
        return order

    @transaction.atomic
    def waive_delivery_charge(self, order: SalesOrder, user: User) -> SalesOrder:
        """Let staff skip/waive the delivery charge on a not-yet-shipped order
        — same gate as apply_discount. One-way (like apply_discount): there's
        no stored delivery zone on the order to recompute the original rate
        from, so this isn't a togglable checkbox, just a deliberate waiver."""
        if order.status not in ('PENDING', 'CONFIRMED'):
            raise ValidationError({
                'message_bn': 'শুধুমাত্র পেন্ডিং বা নিশ্চিত অর্ডারের ডেলিভারি চার্জ মওকুফ করা যায়',
                'message_en': 'Delivery charge can only be waived on pending or confirmed orders',
            })
        if order.payment_status == 'PAID':
            raise ValidationError({
                'message_bn': 'পরিশোধিত অর্ডারের ডেলিভারি চার্জ মওকুফ করা যাবে না',
                'message_en': 'Delivery charge cannot be waived on an already-paid order',
            })
        if order.delivery_charge <= 0:
            raise ValidationError({
                'message_bn': 'এই অর্ডারে ইতিমধ্যে কোনো ডেলিভারি চার্জ নেই',
                'message_en': 'This order already has no delivery charge',
            })

        waived = order.delivery_charge
        order.delivery_charge = Decimal('0')
        order.grand_total = order.subtotal + order.tax_amount - order.cashback_used
        order.save(update_fields=['delivery_charge', 'grand_total'])
        self._resync_order_item_journal(order)

        logger.info(f'Delivery charge waived on order {order.order_number} by {user.email} (৳{waived})')
        return order

    @transaction.atomic
    def update_item_quantity(self, order: SalesOrder, item: SalesOrderItem, new_quantity: Decimal, user: User) -> SalesOrder:
        """Correct a mistaken quantity on a not-yet-shipped order — adjusts the
        already-deducted stock by the delta, recomputes order totals from the
        item's own locked-in unit_price (never re-priced against the product's
        current price), and — since a non-COD order posts its SALE journal
        immediately at checkout, before payment is even confirmed — resyncs
        whatever journal entry (SALE or PAYMENT) already exists for this order
        so COGS/Revenue/AR stay correct. Scoped to PENDING/CONFIRMED only,
        mirroring apply_discount's own gate.
        """
        if order.status not in ('PENDING', 'CONFIRMED'):
            raise ValidationError({
                'message_bn': 'শুধুমাত্র পেন্ডিং বা নিশ্চিত অর্ডারের পরিমাণ পরিবর্তন করা যায়',
                'message_en': 'Quantity can only be changed on pending or confirmed orders',
            })
        if order.payment_status == 'PAID':
            raise ValidationError({
                'message_bn': 'পরিশোধিত অর্ডারের পরিমাণ পরিবর্তন করা যাবে না',
                'message_en': 'Quantity cannot be changed on an already-paid order',
            })
        if new_quantity <= 0:
            raise ValidationError({
                'message_bn': 'পরিমাণ শূন্যের বেশি হতে হবে — বাদ দিতে অর্ডার বাতিল করুন',
                'message_en': 'Quantity must be greater than zero — cancel the order to remove an item',
            })

        delta = new_quantity - item.quantity
        if delta == 0:
            return order

        self._adjust_order_item_stock(item.product, delta, order.id, user)

        item.quantity   = new_quantity
        item.line_total = item.unit_price * new_quantity
        item.save(update_fields=['quantity', 'line_total'])

        self._recalc_order_totals(order)
        self._resync_order_item_journal(order)

        logger.info(f'Order {order.order_number} item {item.id} quantity corrected: {item.quantity - delta} → {new_quantity} by {user.email}')
        return order

    @transaction.atomic
    def add_item(self, order: SalesOrder, product, quantity: Decimal, user: User) -> SalesOrder:
        """Add a product to a not-yet-shipped order — same gate as
        update_item_quantity/delete_item. If the product's already on the
        order, bumps that line's quantity instead of creating a duplicate
        row (mirrors how re-adding an item already in the cart behaves at
        checkout)."""
        if order.status not in ('PENDING', 'CONFIRMED'):
            raise ValidationError({
                'message_bn': 'শুধুমাত্র পেন্ডিং বা নিশ্চিত অর্ডারে পণ্য যোগ করা যায়',
                'message_en': 'Products can only be added to pending or confirmed orders',
            })
        if order.payment_status == 'PAID':
            raise ValidationError({
                'message_bn': 'পরিশোধিত অর্ডারে পণ্য যোগ করা যাবে না',
                'message_en': 'Products cannot be added to an already-paid order',
            })
        if quantity <= 0:
            raise ValidationError({
                'message_bn': 'পরিমাণ শূন্যের বেশি হতে হবে',
                'message_en': 'Quantity must be greater than zero',
            })

        existing = order.items.filter(product=product).first()
        if existing:
            return self.update_item_quantity(order, existing, existing.quantity + quantity, user)

        self._adjust_order_item_stock(product, quantity, order.id, user)

        SalesOrderItem.objects.create(
            order=order, product=product,
            product_name_bn=product.name_bn, product_name_en=product.name_en,
            original_unit_price=product.original_price,
            unit_price=product.effective_price,
            quantity=quantity,
            line_total=product.effective_price * quantity,
        )

        self._recalc_order_totals(order)
        self._resync_order_item_journal(order)

        logger.info(f'Order {order.order_number} item added: {product.sku} x{quantity} by {user.email}')
        return order

    @transaction.atomic
    def delete_item(self, order: SalesOrder, item: SalesOrderItem, user: User) -> SalesOrder:
        """Remove a mistakenly-added line item from a not-yet-shipped order —
        same gate and stock/totals/journal reconciliation as
        update_item_quantity, treating the removal as a drop to zero."""
        if order.status not in ('PENDING', 'CONFIRMED'):
            raise ValidationError({
                'message_bn': 'শুধুমাত্র পেন্ডিং বা নিশ্চিত অর্ডার থেকে পণ্য মুছা যায়',
                'message_en': 'Items can only be removed from pending or confirmed orders',
            })
        if order.payment_status == 'PAID':
            raise ValidationError({
                'message_bn': 'পরিশোধিত অর্ডার থেকে পণ্য মুছা যাবে না',
                'message_en': 'Items cannot be removed from an already-paid order',
            })
        if order.items.count() <= 1:
            raise ValidationError({
                'message_bn': 'অর্ডারে অন্তত একটি পণ্য থাকতে হবে — সম্পূর্ণ অর্ডার বাতিল করতে অর্ডার বাতিল করুন',
                'message_en': 'An order must keep at least one item — cancel the whole order instead to remove everything',
            })

        self._adjust_order_item_stock(item.product, -item.quantity, order.id, user)
        product_name = item.product_name_en
        item.delete()

        self._recalc_order_totals(order)
        self._resync_order_item_journal(order)

        logger.info(f'Order {order.order_number} item removed ({product_name}) by {user.email}')
        return order

    @transaction.atomic
    def return_order(self, order: SalesOrder, user: User, note_bn: str = '', note_en: str = '') -> SalesOrder:
        order = self._transition(order, 'RETURNED', user, note_bn, note_en)
        if order.payment_status == 'PAID':
            order.payment_status = 'UNPAID'
            order.save(update_fields=['payment_status'])
        for item in order.items.select_related('product'):
            StockMovement.objects.create(
                product=item.product, movement_type='RETURN',
                quantity=item.quantity, reference_id=order.id, created_by=user,
            )
        self._create_return_journal(order, user)
        # Refund any store credit spent on this order back to the customer's
        # wallet — the journal side of this (re-instating the liability) is
        # posted inside _create_return_journal above.
        cb_used = Decimal(str(order.cashback_used or 0))
        if cb_used > 0 and not order.is_guest and order.customer_id:
            order.customer.profile.cashback_balance = F('cashback_balance') + cb_used
            order.customer.profile.save(update_fields=['cashback_balance'])
        # Claw back cashback earned on this order, and any referral bonus it
        # triggered — same treatment partial_deliver() already gives a
        # returned slice, applied consistently to a full return too.
        self._reverse_cashback(order, user)
        self._reverse_referral_bonus(order, user)
        return order

    @transaction.atomic
    def partial_deliver(self, order: SalesOrder, user: User, returned_items: list,
                        note_bn: str = '', note_en: str = '') -> SalesOrder:
        """returned_items: [{'item_id': <SalesOrderItem id>, 'quantity': Decimal}, ...] —
        the items (and how much of each) that did NOT actually reach the
        customer. Always a manual, admin-picked call — couriers only ever
        report a lump collected_amount, never which item failed, so there's
        nothing to auto-drive this from a webhook.

        Reachable from ON_THE_WAY (courier reports the shortfall directly)
        or from an already-DELIVERED order (the more common real case: full
        delivery got recorded first, and the gap only surfaces once COD is
        reconciled) — the latter has already posted the full payment/
        cashback/referral journals via deliver(), so those get reversed for
        just the returned slice here rather than assuming a clean slate.

        No cashback or referral bonus for the delivered portion either way
        (deliberately skipped, not prorated) — mirrors return_order()'s
        all-or-nothing treatment rather than inventing a partial-credit rule.
        """
        came_from_delivered = order.status == 'DELIVERED'

        # Validate + resolve every returned line BEFORE transitioning, so the
        # auto-generated item summary can be appended to the status log's
        # note in the same _transition() call that creates it (rather than
        # editing the log row after the fact).
        items_by_id = {str(i.id): i for i in order.items.select_related('product')}
        resolved = []
        for entry in returned_items:
            item = items_by_id.get(str(entry['item_id']))
            if not item:
                raise ValidationError({
                    'message_bn': 'অর্ডারে এই আইটেম পাওয়া যায়নি',
                    'message_en': 'Item not found on this order',
                })
            qty = Decimal(str(entry['quantity']))
            if qty <= 0 or qty > item.quantity:
                raise ValidationError({
                    'message_bn': f'{item.product_name_bn} এর জন্য সঠিক পরিমাণ দিন',
                    'message_en': f'Enter a valid quantity for {item.product_name_en}',
                })
            resolved.append((item, qty))

        summary_bn = 'ফেরত: ' + ', '.join(f'{i.product_name_bn} x{q}' for i, q in resolved)
        summary_en = 'Returned: ' + ', '.join(f'{i.product_name_en or i.product_name_bn} x{q}' for i, q in resolved)
        full_note_bn = f'{note_bn} — {summary_bn}' if note_bn else summary_bn
        full_note_en = f'{note_en} — {summary_en}' if note_en else summary_en

        order = self._transition(order, 'PARTIALLY_DELIVERED', user, full_note_bn, full_note_en)
        if not order.delivery.delivered_at:
            order.delivery.delivered_at = timezone.now()
            order.delivery.save(update_fields=['delivered_at'])

        returned_value = Decimal('0')
        returned_cogs = Decimal('0')
        for item, qty in resolved:
            StockMovement.objects.create(
                product=item.product, movement_type='RETURN',
                quantity=qty, reference_id=order.id, created_by=user,
            )
            returned_value += item.unit_price * qty
            returned_cogs += item.product.cost_price * qty

        if came_from_delivered:
            # Full payment already posted — reverse just the returned slice,
            # then claw back any cashback/referral that order's DELIVERED
            # transition already credited.
            self._create_partial_return_journal(order, user, returned_value, returned_cogs)
            self._reverse_cashback(order, user)
            self._reverse_referral_bonus(order, user)
        else:
            # Fresh from ON_THE_WAY — nothing posted yet, so post one
            # payment journal scoped to what was actually delivered.
            if order.payment_method == 'COD' and order.payment_status == 'UNPAID':
                order.payment_status = 'PAID'
                order.save(update_fields=['payment_status'])
            if not JournalEntry.objects.filter(reference_type='PAYMENT', reference_id=order.id).exists():
                self._create_partial_payment_journal(order, user, returned_value, returned_cogs)
            if not hasattr(order, 'courier_consignment'):
                self._create_self_delivery_expense_journal(order, user)

        logger.info(
            f'Order {order.order_number} partially delivered by {user.email} '
            f'(returned value ৳{returned_value}, from {"DELIVERED" if came_from_delivered else "ON_THE_WAY"})'
        )
        return order

    @transaction.atomic
    def cancel(self, order: SalesOrder, user: User, note_bn: str = '', note_en: str = '') -> SalesOrder:
        order = self._transition(order, 'CANCELLED', user, note_bn, note_en)
        # Reverse stock
        for item in order.items.select_related('product'):
            StockMovement.objects.create(
                product=item.product, movement_type='RETURN',
                quantity=item.quantity, reference_id=order.id, created_by=user,
            )
        # Only reverse accounting if a journal was already posted for this
        # order — either PAYMENT (COD/online already paid before cancelling)
        # or SALE (a POS non-COD order, which books its journal immediately
        # at creation against Accounts Receivable rather than Cash — see
        # guest_service._create_sale_journal — so it needs its own reversal
        # that credits the same account back rather than crediting Cash for
        # money never booked there). Checking PAYMENT alone missed the SALE
        # case entirely, leaving those cancelled orders' revenue/COGS/AR
        # permanently on the books. Pre-delivery COD cancellations have
        # neither, so nothing to reverse.
        if JournalEntry.objects.filter(reference_id=order.id, reference_type='PAYMENT').exists():
            self._create_return_journal(order, user)
        elif JournalEntry.objects.filter(reference_id=order.id, reference_type='SALE').exists():
            self._create_sale_reversal_journal(order, user)
        # Refund cashback that was used on this order back to the customer
        cb_used = Decimal(str(order.cashback_used or 0))
        if cb_used > 0 and not order.is_guest and order.customer_id:
            order.customer.profile.cashback_balance = F('cashback_balance') + cb_used
            order.customer.profile.save(update_fields=['cashback_balance'])
        return order

    # ── private ───────────────────────────────────────────────────────────────

    def _transition(self, order: SalesOrder, to_status: str, user: User,
                    note_bn: str = '', note_en: str = '') -> SalesOrder:
        if not order.can_transition_to(to_status):
            raise ValidationError({
                'message_bn': 'এই অবস্থায় পরিবর্তন করা যাবে না',
                'message_en': f'Cannot transition from {order.status} to {to_status}',
            })
        prev = order.status
        order.status = to_status
        order.save(update_fields=['status'])
        OrderStatusLog.objects.create(
            order=order, from_status=prev, to_status=to_status,
            changed_by=user, note_bn=note_bn, note_en=note_en,
        )
        self._notify_customer(order, to_status)
        logger.info(f"Order {order.order_number}: {prev} → {to_status}")
        return order

    def _next_entry_number(self) -> str:
        return next_entry_number()

    def _acct(self, code: str):
        try:
            return Account.objects.get(code=code)
        except Account.DoesNotExist:
            return None

    def _adjust_order_item_stock(self, product, delta: Decimal, order_id, user: User) -> None:
        """delta > 0 (quantity increased) needs MORE stock deducted; delta < 0
        (quantity decreased) restores stock. Packages have no stock movement
        of their own — deduct/restore each component instead, same as the
        original checkout-time deduction."""
        if product.is_package:
            for pi in ProductPackageItem.objects.filter(package=product).select_related('component'):
                self._create_order_stock_movement(pi.component, -(pi.quantity * delta), order_id, user)
        else:
            self._create_order_stock_movement(product, -delta, order_id, user)

    def _create_order_stock_movement(self, product, qty_change: Decimal, order_id, user: User) -> None:
        if qty_change == 0:
            return
        if qty_change < 0 and product.stock_on_hand + qty_change < 0:
            raise ValidationError({
                'message_bn': f'{product.name_bn} এর পর্যাপ্ত স্টক নেই',
                'message_en': f'Insufficient stock for {product.name_en}',
            })
        StockMovement.objects.create(
            product=product, movement_type='SALE', quantity=qty_change,
            reference_id=order_id, created_by=user,
        )

    def _recalc_order_totals(self, order: SalesOrder) -> None:
        # order.items.all() would silently reuse get_order()'s prefetch_related
        # cache here — stale from before this same request's delete/quantity
        # change — so query the base manager directly to force a fresh read.
        #
        # discount_amount mixes two different things that both need to
        # survive an item being added/changed/removed: each item's own
        # product-level discount (original_unit_price vs unit_price — already
        # baked into line_total, so it's recomputed fresh from current items
        # every time) and a manually-applied staff/POS discount, which isn't
        # stored on any item at all. Without separately tracking the latter
        # in staff_discount_amount, a naive "subtotal = sum(line_total)"
        # here would silently wipe out any staff discount the moment an item
        # changed — discount_amount would stay stale while the customer-
        # facing subtotal jumped back up as if the discount never happened.
        items = list(SalesOrderItem.objects.filter(order=order))
        raw_total = sum((i.line_total for i in items), Decimal('0'))
        original_total = sum(((i.original_unit_price or i.unit_price) * i.quantity for i in items), Decimal('0'))
        product_discount = original_total - raw_total
        staff_discount = order.staff_discount_amount or Decimal('0')
        first_order_discount = order.first_order_discount_amount or Decimal('0')

        order.subtotal        = raw_total - staff_discount - first_order_discount
        order.discount_amount = product_discount + staff_discount + first_order_discount
        order.grand_total     = order.subtotal + order.delivery_charge + order.tax_amount - order.cashback_used
        order.save(update_fields=['subtotal', 'discount_amount', 'grand_total'])

    def _resync_order_item_journal(self, order: SalesOrder) -> None:
        """A non-COD order posts its SALE journal immediately at checkout
        (before payment is even confirmed), and a COD order can be marked
        paid — posting a PAYMENT journal — while still PENDING/CONFIRMED. So
        by the time a quantity is corrected, one of those may already exist
        with stale COGS/Revenue/AR-or-Cash amounts; rebuild it in place. If
        neither exists yet (the common case — unpaid COD), there's nothing
        posted to fix; whichever journal is created later will compute COGS
        fresh from the now-corrected item quantities."""
        entry = JournalEntry.objects.filter(
            reference_id=order.id, reference_type__in=('SALE', 'PAYMENT'),
        ).first()
        if not entry:
            return

        cogs    = sum((i.product.cost_price * i.quantity for i in SalesOrderItem.objects.filter(order=order).select_related('product')), Decimal('0'))
        cb_used = Decimal(str(order.cashback_used or 0))

        if entry.reference_type == 'SALE':
            lines = [
                ('1100', order.grand_total,                  Decimal('0')),  # Dr AR
                ('4000', Decimal('0'),                       order.subtotal),  # Cr Revenue
                ('4200', Decimal('0'), Decimal(str(order.delivery_charge))),   # Cr Delivery
                ('2100', Decimal('0'),                       order.tax_amount),  # Cr Tax
                ('5000', cogs,                                Decimal('0')),  # Dr COGS
                ('1300', Decimal('0'),                       cogs),          # Cr Inventory
            ]
        else:  # PAYMENT
            lines = [
                ('1000', order.grand_total,                  Decimal('0')),  # Dr Cash
                ('5000', cogs,                                Decimal('0')),  # Dr COGS
                ('4000', Decimal('0'),                       order.subtotal),  # Cr Revenue
                ('4200', Decimal('0'), Decimal(str(order.delivery_charge))),   # Cr Delivery
                ('1300', Decimal('0'),                       cogs),          # Cr Inventory
            ]
        if cb_used > 0:
            lines.append(('2250', cb_used, Decimal('0')))  # Dr Cashback Payable

        entry.lines.all().delete()
        for code, debit, credit in lines:
            acct = self._acct(code)
            if acct and (debit or credit):
                JournalLine.objects.create(journal_entry=entry, account=acct, debit=debit, credit=credit)

    def _create_payment_journal(self, order: SalesOrder, user: User) -> None:
        cogs    = sum(item.product.cost_price * item.quantity for item in order.items.select_related('product'))
        revenue = order.subtotal  # already net of discount
        entry = JournalEntry.objects.create(
            entry_number=self._next_entry_number(), reference_type='PAYMENT',
            reference_id=order.id,
            description_bn=f'পেমেন্ট — {order.order_number}',
            description_en=f'Payment — {order.order_number}',
            created_by=user, is_posted=True,
        )
        cb_used = Decimal(str(order.cashback_used or 0))
        lines = [
            ('1000', order.grand_total,                    Decimal('0')),  # Dr Cash (already net of cashback)
            ('5000', cogs,                                 Decimal('0')),  # Dr COGS
            ('4000', Decimal('0'),                         revenue),       # Cr Revenue
            ('4200', Decimal('0'), Decimal(str(order.delivery_charge))),   # Cr Delivery Income
            ('1300', Decimal('0'),                         cogs),          # Cr Inventory
        ]
        if cb_used > 0:
            lines.append(('2250', cb_used, Decimal('0')))  # Dr Cashback Payable (discharged)
        for code, debit, credit in lines:
            acct = self._acct(code)
            if acct and (debit or credit):
                JournalLine.objects.create(journal_entry=entry, account=acct, debit=debit, credit=credit)

    def _create_cashback_earned_journal(self, order: SalesOrder, amount: Decimal, user: User) -> None:
        entry = JournalEntry.objects.create(
            entry_number=self._next_entry_number(), reference_type='CASHBACK',
            reference_id=order.id,
            description_bn=f'ক্যাশব্যাক অর্জিত — {order.order_number}',
            description_en=f'Cashback Earned — {order.order_number}',
            created_by=user, is_posted=True,
        )
        for code, debit, credit in [
            ('6350', amount,         Decimal('0')),  # Dr Cashback Expense
            ('2250', Decimal('0'),   amount),        # Cr Cashback Payable
        ]:
            acct = self._acct(code)
            if acct:
                JournalLine.objects.create(journal_entry=entry, account=acct, debit=debit, credit=credit)

    def _process_referral_bonus(self, order: SalesOrder, actor: User) -> None:
        if order.is_guest or not order.customer_id:
            return
        customer = order.customer
        referrer = getattr(customer, 'referred_by', None)
        if not referrer:
            return
        # Only pay once per referrer–referred pair
        if ReferralBonus.objects.filter(referrer=referrer, referred_user=customer).exists():
            return
        amount = SiteSetting.get().referral_bonus_amount
        ReferralBonus.objects.create(referrer=referrer, referred_user=customer, order=order, amount=amount)
        referrer.profile.cashback_balance = F('cashback_balance') + amount
        referrer.profile.save(update_fields=['cashback_balance'])
        self._create_referral_bonus_journal(referrer, order, amount, actor)
        logger.info(f'Referral bonus ৳{amount} credited to {referrer.email} for referring {customer.email}')

    def _create_referral_bonus_journal(self, referrer: User, order: SalesOrder, amount: Decimal, actor: User) -> None:
        entry = JournalEntry.objects.create(
            entry_number=self._next_entry_number(), reference_type='REFERRAL',
            reference_id=order.id,
            description_bn=f'রেফারেল বোনাস — {order.order_number} ({referrer.email})',
            description_en=f'Referral Bonus — {order.order_number} ({referrer.email})',
            created_by=actor, is_posted=True,
        )
        for code, debit, credit in [
            ('6300', amount,         Decimal('0')),  # Dr Marketing & Advertising
            ('2250', Decimal('0'),   amount),        # Cr Cashback Payable
        ]:
            acct = self._acct(code)
            if acct:
                JournalLine.objects.create(journal_entry=entry, account=acct, debit=debit, credit=credit)

    def _reverse_referral_bonus(self, order: SalesOrder, actor: User) -> None:
        bonus = ReferralBonus.objects.filter(order=order).select_related('referrer__profile').first()
        if not bonus:
            return
        referrer = bonus.referrer
        amount   = bonus.amount
        referrer.profile.cashback_balance = F('cashback_balance') - amount
        referrer.profile.save(update_fields=['cashback_balance'])
        # Post reversal journal: DR Cashback Payable / CR Marketing Expense
        entry = JournalEntry.objects.create(
            entry_number=self._next_entry_number(), reference_type='REFERRAL_REVERSAL',
            reference_id=order.id,
            description_bn=f'রেফারেল বোনাস বিপরীত — {order.order_number} ({referrer.email})',
            description_en=f'Referral Bonus Reversed — {order.order_number} ({referrer.email})',
            created_by=actor, is_posted=True,
        )
        for code, debit, credit in [
            ('2250', amount,         Decimal('0')),  # Dr Cashback Payable (liability cleared)
            ('6300', Decimal('0'),   amount),        # Cr Marketing & Advertising (expense reversed)
        ]:
            acct = self._acct(code)
            if acct:
                JournalLine.objects.create(journal_entry=entry, account=acct, debit=debit, credit=credit)
        bonus.delete()  # allow bonus to fire again if referred user places a new delivered order
        logger.info(f'Referral bonus ৳{amount} reversed from {referrer.email} for returned order {order.order_number}')

    def _reverse_cashback(self, order: SalesOrder, actor: User) -> None:
        """Claws back the cashback deliver() credited, for the partial_deliver()
        case where an order already went through DELIVERED before the
        shortfall surfaced. No-ops if deliver() never actually credited any
        (guest order, no customer, or cashback_amount was 0)."""
        if not JournalEntry.objects.filter(reference_type='CASHBACK', reference_id=order.id).exists():
            return
        amount = Decimal(str(order.cashback_amount or 0))
        if amount <= 0 or order.is_guest or not order.customer_id:
            return
        order.customer.profile.cashback_balance = F('cashback_balance') - amount
        order.customer.profile.save(update_fields=['cashback_balance'])
        entry = JournalEntry.objects.create(
            entry_number=self._next_entry_number(), reference_type='CASHBACK_REVERSAL',
            reference_id=order.id,
            description_bn=f'ক্যাশব্যাক বিপরীত — {order.order_number}',
            description_en=f'Cashback Reversed — {order.order_number}',
            created_by=actor, is_posted=True,
        )
        for code, debit, credit in [
            ('2250', amount,         Decimal('0')),  # Dr Cashback Payable (liability cleared)
            ('6350', Decimal('0'),   amount),        # Cr Cashback Expense (reversed)
        ]:
            acct = self._acct(code)
            if acct:
                JournalLine.objects.create(journal_entry=entry, account=acct, debit=debit, credit=credit)
        logger.info(f'Cashback ৳{amount} reversed for partially-returned order {order.order_number}')

    def _create_return_journal(self, order: SalesOrder, user: User) -> None:
        cogs = sum(
            item.product.cost_price * item.quantity
            for item in order.items.select_related('product')
        )
        revenue  = order.subtotal  # already net of discount
        delivery = order.delivery_charge or Decimal('0')
        cb_used  = Decimal(str(order.cashback_used or 0))
        # Only the cash actually collected gets refunded — whatever portion
        # was paid with store credit was never real cash to begin with; that
        # portion is restored to the customer's wallet (see return_order()/
        # cancel()) and its liability re-instated below instead of being
        # double-refunded as cash on top of the credit.
        cash_refund = revenue + delivery - cb_used
        entry = JournalEntry.objects.create(
            entry_number=self._next_entry_number(), reference_type='RETURN',
            reference_id=order.id,
            description_bn=f'পণ্য ফেরত — {order.order_number}',
            description_en=f'Goods Return — {order.order_number}',
            created_by=user, is_posted=True,
        )
        lines = [
            ('4000', revenue,       Decimal('0')),  # Dr Sales Revenue (reversal)
            ('4200', delivery,      Decimal('0')),  # Dr Delivery Income (reversal — nothing was kept delivered)
            ('1300', cogs,          Decimal('0')),  # Dr Inventory (stock back)
            ('1000', Decimal('0'),  cash_refund),   # Cr Cash (refund, net of any store credit used)
            ('5000', Decimal('0'),  cogs),          # Cr COGS (reversal)
        ]
        if cb_used > 0:
            lines.append(('2250', Decimal('0'), cb_used))  # Cr Cashback Payable (re-instate spent credit)
        for code, debit, credit in lines:
            acct = self._acct(code)
            if acct and (debit or credit):
                JournalLine.objects.create(journal_entry=entry, account=acct, debit=debit, credit=credit)

    def _create_sale_reversal_journal(self, order: SalesOrder, user: User) -> None:
        """Reverses a SALE-type entry — a POS non-COD order, whose journal
        posts immediately at creation against 1100 Accounts Receivable
        rather than 1000 Cash (see guest_service._create_sale_journal) —
        used by cancel() when it finds a SALE journal instead of a PAYMENT
        one, so the credit side matches what was actually debited
        originally rather than crediting Cash for money never booked there."""
        cogs = sum(
            item.product.cost_price * item.quantity
            for item in order.items.select_related('product')
        )
        revenue  = order.subtotal
        delivery = order.delivery_charge or Decimal('0')
        entry = JournalEntry.objects.create(
            entry_number=self._next_entry_number(), reference_type='RETURN',
            reference_id=order.id,
            description_bn=f'বিক্রয় বাতিল — {order.order_number}',
            description_en=f'Sale Cancelled — {order.order_number}',
            created_by=user, is_posted=True,
        )
        lines = [
            ('4000', revenue,                Decimal('0')),  # Dr Sales Revenue (reversal)
            ('4200', delivery,               Decimal('0')),  # Dr Delivery Income (reversal)
            ('1300', cogs,                   Decimal('0')),  # Dr Inventory (stock back)
            ('1100', Decimal('0'), revenue + delivery),       # Cr Accounts Receivable
            ('5000', Decimal('0'),           cogs),          # Cr COGS (reversal)
        ]
        for code, debit, credit in lines:
            acct = self._acct(code)
            if acct and (debit or credit):
                JournalLine.objects.create(journal_entry=entry, account=acct, debit=debit, credit=credit)

    def _create_partial_return_journal(self, order: SalesOrder, user: User,
                                       returned_value: Decimal, returned_cogs: Decimal) -> None:
        """Same shape as _create_return_journal, scoped to just the returned
        items' slice — used when partial_deliver() is correcting an order
        that already went through deliver() (full payment journal already
        posted for the whole order), so only the shortfall gets reversed."""
        entry = JournalEntry.objects.create(
            entry_number=self._next_entry_number(), reference_type='RETURN',
            reference_id=order.id,
            description_bn=f'আংশিক ফেরত — {order.order_number}',
            description_en=f'Partial Return — {order.order_number}',
            created_by=user, is_posted=True,
        )
        lines = [
            ('4000', returned_value, Decimal('0')),  # Dr Sales Revenue (reversal, returned slice only)
            ('1300', returned_cogs,  Decimal('0')),  # Dr Inventory (stock back)
            ('1000', Decimal('0'),   returned_value),# Cr Cash (refund, returned slice only)
            ('5000', Decimal('0'),   returned_cogs), # Cr COGS (reversal)
        ]
        for code, debit, credit in lines:
            acct = self._acct(code)
            if acct and (debit or credit):
                JournalLine.objects.create(journal_entry=entry, account=acct, debit=debit, credit=credit)

    @transaction.atomic
    def create_exchange(self, original_order: SalesOrder, user: User,
                        returned_items: list, replacement_items: list,
                        delivery_charge_waived: bool = False,
                        discount_type: str = '', discount_value: Decimal = None,
                        note_bn: str = '', note_en: str = '') -> tuple[SalesOrder, SalesOrder]:
        """Exchange (full or partial) of a delivered order's item(s) for a
        different product. original_order transitions to EXCHANGED (terminal,
        like RETURNED/PARTIALLY_DELIVERED) and is never reopened; the
        replacement ships as a brand-new SalesOrder, which gets delivery
        assignment/invoice/tracking/apply_discount()/_create_payment_journal
        entirely for free through the normal pipeline, with zero changes to
        any of that machinery.

        returned_items: [{'item_id': <SalesOrderItem id>, 'quantity': Decimal}, ...]
        replacement_items: [{'product': <Product>, 'quantity': Decimal}, ...]

        Settlement for the returned value: a registered customer gets store
        credit (profile.cashback_balance, auto-applied against the new
        order's total, same as at checkout). A guest order has no wallet to
        credit, so it settles in cash instead — the return-reversal journal
        credits Cash (reusing _create_partial_return_journal unmodified) and
        the new order is just a normal order the guest pays for in full,
        with the cash difference settled at the counter.
        """
        if original_order.status != 'DELIVERED':
            raise ValidationError({
                'message_bn': 'শুধুমাত্র ডেলিভারি হওয়া অর্ডার বিনিময় করা যায়',
                'message_en': 'Only a delivered order can be exchanged',
            })
        if not returned_items:
            raise ValidationError({
                'message_bn': 'অন্তত একটি ফেরতযোগ্য পণ্য নির্বাচন করুন',
                'message_en': 'Select at least one item to return',
            })
        if not replacement_items:
            raise ValidationError({
                'message_bn': 'অন্তত একটি প্রতিস্থাপন পণ্য নির্বাচন করুন',
                'message_en': 'Select at least one replacement product',
            })

        # Merge duplicate lines (same item/product picked more than once in
        # one request) BEFORE validating — otherwise two lines for the same
        # item would each pass the "not over remaining quantity" check
        # independently, since neither sees the other's claim yet.
        returned_qty_by_id: dict[str, Decimal] = {}
        for entry in returned_items:
            key = str(entry['item_id'])
            returned_qty_by_id[key] = returned_qty_by_id.get(key, Decimal('0')) + Decimal(str(entry['quantity']))

        replacement_qty_by_product: dict = {}   # product.id -> (product, total_qty)
        for entry in replacement_items:
            product = entry['product']
            _, prev_qty = replacement_qty_by_product.get(product.id, (product, Decimal('0')))
            replacement_qty_by_product[product.id] = (product, prev_qty + Decimal(str(entry['quantity'])))

        items_by_id = {str(i.id): i for i in original_order.items.select_related('product')}
        resolved_returns = []
        for item_id, qty in returned_qty_by_id.items():
            item = items_by_id.get(item_id)
            if not item:
                raise ValidationError({
                    'message_bn': 'অর্ডারে এই আইটেম পাওয়া যায়নি',
                    'message_en': 'Item not found on this order',
                })
            already = ExchangeItem.objects.filter(original_item=item).aggregate(total=Sum('quantity'))['total'] or Decimal('0')
            if qty <= 0 or already + qty > item.quantity:
                raise ValidationError({
                    'message_bn': f'{item.product_name_bn} এর জন্য সঠিক পরিমাণ দিন',
                    'message_en': f'Enter a valid quantity for {item.product_name_en}',
                })
            resolved_returns.append((item, qty))

        resolved_replacements = list(replacement_qty_by_product.values())
        for product, qty in resolved_replacements:
            if qty <= 0:
                raise ValidationError({
                    'message_bn': 'পরিমাণ শূন্যের বেশি হতে হবে',
                    'message_en': 'Quantity must be greater than zero',
                })

        original_order = self._transition(original_order, 'EXCHANGED', user, note_bn, note_en)

        returned_value = Decimal('0')
        returned_cogs  = Decimal('0')
        for item, qty in resolved_returns:
            self._return_order_item_stock(item.product, qty, original_order.id, user)
            returned_value += item.unit_price * qty
            returned_cogs  += item.product.cost_price * qty

        profile = None
        if original_order.is_guest or not original_order.customer_id:
            # Guest: no wallet to credit — settle in cash (reuses the
            # existing cash-refund journal shape unmodified).
            self._create_partial_return_journal(original_order, user, returned_value, returned_cogs)
        else:
            self._create_exchange_return_journal(original_order, user, returned_value, returned_cogs)
            profile = original_order.customer.profile
            profile.cashback_balance = F('cashback_balance') + returned_value
            profile.save(update_fields=['cashback_balance'])
            profile.refresh_from_db(fields=['cashback_balance'])  # F() doesn't update the in-memory value

        # Claw back cashback earned on the original order, and any referral
        # bonus it triggered — same treatment partial_deliver()/return_order()
        # already give a return, applied consistently here too. Both are
        # no-ops if nothing was ever earned on this order.
        self._reverse_cashback(original_order, user)
        self._reverse_referral_bonus(original_order, user)
        if profile is not None:
            # _reverse_cashback (if it fired) left an unresolved F()
            # expression on this same cached profile instance — refresh
            # before the cashback_used clamp below reads it as a real number.
            profile.refresh_from_db(fields=['cashback_balance'])

        original_subtotal = sum((p.original_price * q for p, q in resolved_replacements), Decimal('0'))
        subtotal          = sum((p.effective_price * q for p, q in resolved_replacements), Decimal('0'))
        total_weight      = sum(((p.weight_kg or Decimal('0')) * q for p, q in resolved_replacements), Decimal('0'))

        zone = 'inside' if (original_order.shipping_district or '').strip().lower() in _DHAKA_DISTRICTS else 'outside'
        delivery = Decimal('0') if delivery_charge_waived else DeliveryCharge.get().charge_for(zone, total_weight)

        # cashback_used starts at 0 here deliberately — it's only computed
        # AFTER any discretionary discount is applied below (apply_discount's
        # own _recalc_order_totals reads the current stored cashback_used to
        # rebuild grand_total; if cashback had already been clamped against
        # the pre-discount total, a big enough discount would push
        # grand_total negative instead of the discount simply amplifying how
        # much cashback should have been usable).
        new_order = SalesOrder.objects.create(
            order_number=generate_order_number(),
            customer=original_order.customer, is_guest=original_order.is_guest,
            guest_email=original_order.guest_email,
            exchanged_from=original_order,
            payment_method=original_order.payment_method, payment_status='UNPAID', status='PENDING',
            shipping_name_bn=original_order.shipping_name_bn, shipping_name_en=original_order.shipping_name_en,
            shipping_phone=original_order.shipping_phone,
            shipping_address_bn=original_order.shipping_address_bn, shipping_address_en=original_order.shipping_address_en,
            shipping_district=original_order.shipping_district, shipping_thana=original_order.shipping_thana,
            shipping_post_code=original_order.shipping_post_code,
            source=original_order.source,
            subtotal=subtotal, discount_amount=original_subtotal - subtotal,
            delivery_charge=delivery, estimated_weight_kg=total_weight,
            grand_total=subtotal + delivery, cashback_used=Decimal('0'),
        )

        for product, qty in resolved_replacements:
            SalesOrderItem.objects.create(
                order=new_order, product=product,
                product_name_bn=product.name_bn, product_name_en=product.name_en,
                original_unit_price=product.original_price, unit_price=product.effective_price,
                quantity=qty, line_total=product.effective_price * qty,
            )
            self._adjust_order_item_stock(product, qty, new_order.id, user)

        OrderStatusLog.objects.create(order=new_order, from_status='', to_status='PENDING', changed_by=user)

        if discount_type and discount_value:
            new_order = self.apply_discount(new_order, discount_type, discount_value, user)

        if profile is not None:
            cashback_used = min(profile.cashback_balance, new_order.grand_total)
            if cashback_used > 0:
                new_order.grand_total -= cashback_used
                new_order.cashback_used = cashback_used
                new_order.save(update_fields=['grand_total', 'cashback_used'])
                profile.cashback_balance -= cashback_used
                profile.save(update_fields=['cashback_balance'])
            cashback_earned = CashbackTier.calculate(new_order.grand_total)
            if cashback_earned > 0:
                new_order.cashback_amount = cashback_earned
                new_order.save(update_fields=['cashback_amount'])

        exchange = Exchange.objects.create(
            original_order=original_order, new_order=new_order,
            note_bn=note_bn, note_en=note_en,
            delivery_charge_waived=delivery_charge_waived,
            returned_value=returned_value, created_by=user,
        )
        for item, qty in resolved_returns:
            ExchangeItem.objects.create(
                exchange=exchange, original_item=item, quantity=qty,
                unit_price=item.unit_price, cost_price=item.product.cost_price,
            )

        logger.info(
            f'Exchange created: {original_order.order_number} → {new_order.order_number} '
            f'by {user.email} (returned ৳{returned_value}, settled via {"credit" if profile is not None else "cash"})'
        )
        return original_order, new_order

    def _return_order_item_stock(self, product, qty: Decimal, order_id, user: User) -> None:
        """RETURN-side counterpart to _adjust_order_item_stock — fans a
        returned package out to its components. return_order()/
        partial_deliver() instead create a single RETURN movement against
        the package product itself, which silently restocks nothing since
        Product.stock_on_hand for a package is derived only from its
        components' movements — this new path deliberately doesn't repeat
        that gap."""
        if product.is_package:
            for pi in ProductPackageItem.objects.filter(package=product).select_related('component'):
                StockMovement.objects.create(
                    product=pi.component, movement_type='RETURN',
                    quantity=pi.quantity * qty, reference_id=order_id, created_by=user,
                )
        else:
            StockMovement.objects.create(
                product=product, movement_type='RETURN',
                quantity=qty, reference_id=order_id, created_by=user,
            )

    def _create_exchange_return_journal(self, order: SalesOrder, user: User,
                                        returned_value: Decimal, returned_cogs: Decimal) -> None:
        """Same shape as _create_partial_return_journal, except credits 2250
        Cashback Payable instead of 1000 Cash — a registered customer's
        exchange settles as store credit, not a cash refund (create_exchange
        uses this only for non-guest orders; guests use the Cash-crediting
        version directly)."""
        entry = JournalEntry.objects.create(
            entry_number=self._next_entry_number(), reference_type='RETURN',
            reference_id=order.id,
            description_bn=f'বিনিময় ফেরত — {order.order_number}',
            description_en=f'Exchange Return — {order.order_number}',
            created_by=user, is_posted=True,
        )
        lines = [
            ('4000', returned_value, Decimal('0')),  # Dr Sales Revenue (reversal)
            ('1300', returned_cogs,  Decimal('0')),  # Dr Inventory (stock back)
            ('5000', Decimal('0'),   returned_cogs), # Cr COGS (reversal)
            ('2250', Decimal('0'),   returned_value),# Cr Cashback Payable (store credit issued)
        ]
        for code, debit, credit in lines:
            acct = self._acct(code)
            if acct and (debit or credit):
                JournalLine.objects.create(journal_entry=entry, account=acct, debit=debit, credit=credit)

    def _create_self_delivery_expense_journal(self, order: SalesOrder, user: User) -> None:
        """Internal delivery (no courier) pays the rider the full
        order.delivery_charge collected from the customer — a pure
        pass-through per the business's own rider-pay model, so it's
        expensed here rather than left as pure income. Courier-delivered
        orders never reach this (guarded by the caller checking
        courier_consignment) — those get CourierService's own expense entry
        once the courier reports their actual (possibly different) fee.
        Guarded against double-posting if called from both deliver() and
        partial_deliver()'s fresh-COD branch for the same order."""
        delivery = order.delivery_charge or Decimal('0')
        if delivery <= 0:
            return
        if JournalEntry.objects.filter(reference_type='EXPENSE', reference_id=order.id).exists():
            return
        entry = JournalEntry.objects.create(
            entry_number=self._next_entry_number(), reference_type='EXPENSE',
            reference_id=order.id,
            description_bn=f'ডেলিভারি রাইডার খরচ — {order.order_number}',
            description_en=f'Delivery Rider Expense — {order.order_number}',
            created_by=user, is_posted=True,
        )
        for code, debit, credit in [
            ('6500', delivery,      Decimal('0')),  # Dr Delivery Expense
            ('1000', Decimal('0'),  delivery),       # Cr Cash (paid to the rider)
        ]:
            acct = self._acct(code)
            if acct and (debit or credit):
                JournalLine.objects.create(journal_entry=entry, account=acct, debit=debit, credit=credit)

    def _create_partial_payment_journal(self, order: SalesOrder, user: User,
                                        returned_value: Decimal, returned_cogs: Decimal) -> None:
        """Same shape as _create_payment_journal, scoped to only what was
        actually delivered — used when partial_deliver() applies fresh from
        ON_THE_WAY (no payment journal posted yet), so revenue/COGS/cash are
        recognized for the delivered portion only, never the returned one."""
        full_cogs = sum(item.product.cost_price * item.quantity for item in order.items.select_related('product'))
        delivered_revenue = order.subtotal - returned_value  # subtotal already net of discount
        delivered_cogs = full_cogs - returned_cogs
        cb_used = Decimal(str(order.cashback_used or 0))
        # Mirrors grand_total's own formula (revenue + delivery - cashback_used),
        # just scoped to the delivered slice, so the entry balances exactly
        # the same way _create_payment_journal's full-order version does.
        cash_received = delivered_revenue + Decimal(str(order.delivery_charge)) - cb_used
        entry = JournalEntry.objects.create(
            entry_number=self._next_entry_number(), reference_type='PAYMENT',
            reference_id=order.id,
            description_bn=f'আংশিক পেমেন্ট — {order.order_number}',
            description_en=f'Partial Payment — {order.order_number}',
            created_by=user, is_posted=True,
        )
        lines = [
            ('1000', cash_received,   Decimal('0')),                        # Dr Cash (delivered slice + delivery fee)
            ('5000', delivered_cogs,  Decimal('0')),                        # Dr COGS (delivered slice only)
            ('4000', Decimal('0'),    delivered_revenue),                   # Cr Revenue (delivered slice only)
            ('4200', Decimal('0'),    Decimal(str(order.delivery_charge))), # Cr Delivery Income
            ('1300', Decimal('0'),    delivered_cogs),                      # Cr Inventory (delivered slice only)
        ]
        if cb_used > 0:
            lines.append(('2250', cb_used, Decimal('0')))  # Dr Cashback Payable (discharged)
        for code, debit, credit in lines:
            acct = self._acct(code)
            if acct and (debit or credit):
                JournalLine.objects.create(journal_entry=entry, account=acct, debit=debit, credit=credit)

    def _notify_customer(self, order: SalesOrder, to_status: str) -> None:
        if order.is_guest or not order.customer_id:
            return
        STATUS_LABELS = {
            'CONFIRMED':  {'bn': 'নিশ্চিত হয়েছে',        'en': 'Confirmed'},
            'PACKED':     {'bn': 'প্যাক হয়েছে',           'en': 'Packed'},
            'ASSIGNED':   {'bn': 'ডেলিভারি এসাইন্ড হয়েছে', 'en': 'Delivery Assigned'},
            'ON_THE_WAY': {'bn': 'পথে আছে',               'en': 'Out for Delivery'},
            'DELIVERED':  {'bn': 'ডেলিভারি হয়েছে',        'en': 'Delivered'},
            'RETURNED':   {'bn': 'ফেরত হয়েছে',            'en': 'Returned'},
            'EXCHANGED':  {'bn': 'বিনিময় হয়েছে',           'en': 'Exchanged'},
            'CANCELLED':  {'bn': 'বাতিল হয়েছে',           'en': 'Cancelled'},
        }
        label = STATUS_LABELS.get(to_status)
        if not label:
            return
        notification = Notification.objects.create(
            user_id=order.customer_id,
            title_bn=f'অর্ডার {label["bn"]} — {order.order_number}',
            title_en=f'Order {label["en"]} — {order.order_number}',
            body_bn=f'আপনার অর্ডার #{order.order_number} এখন {label["bn"]}।',
            body_en=f'Your order #{order.order_number} is now {label["en"]}.',
            reference_type='STATUS_CHANGED',
            reference_id=order.id,
        )
        broadcast_notification(notification)

    def _notify_delivery_person(self, order: SalesOrder, delivery_person: User) -> None:
        notification = Notification.objects.create(
            user=delivery_person,
            title_bn=f'নতুন ডেলিভারি এসাইন্ড — {order.order_number}',
            title_en=f'New Delivery Assigned — {order.order_number}',
            body_bn=f'অর্ডার #{order.order_number} আপনার কাছে ডেলিভারির জন্য এসাইন্ড করা হয়েছে।',
            body_en=f'Order #{order.order_number} has been assigned to you for delivery.',
            reference_type='STATUS_CHANGED',
            reference_id=order.id,
        )
        broadcast_notification(notification)
