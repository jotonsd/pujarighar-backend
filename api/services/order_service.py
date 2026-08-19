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
)
from api.utils.dates import local_day_start, local_day_end_exclusive
from api.services.notification_ws import broadcast_notification

# Same district-set checkout_service.py/guest_service.py use to pick a zone
# when the customer didn't explicitly choose one — the order itself has no
# stored zone to recompute from later, so this is re-derived the same way.
_DHAKA_DISTRICTS = {'dhaka', 'ঢাকা'}

logger = logging.getLogger(__name__)


class OrderService:

    def list_orders(self, user: User, params: dict):
        role = user.role.code
        qs   = SalesOrder.objects.select_related('customer', 'delivery').prefetch_related('items', 'status_logs')

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
    def assign_delivery(self, order: SalesOrder, delivery_person_id: str | None, user: User, weight: Decimal = None) -> SalesOrder:
        delivery_person = None
        if delivery_person_id:
            delivery_person = User.objects.get(id=delivery_person_id, role__code='DELIVERY')
        DeliveryAssignment.objects.update_or_create(order=order, defaults={'delivery_person': delivery_person})
        # Already ASSIGNED (e.g. assigned earlier without a delivery person) — just
        # attaching/updating the delivery person now, no status transition needed.
        updated = order if order.status == 'ASSIGNED' else self._transition(order, 'ASSIGNED', user)
        self.recalculate_delivery_charge(updated, weight)
        if delivery_person:
            self._notify_delivery_person(updated, delivery_person)
        return updated

    def recalculate_delivery_charge(self, order: SalesOrder, weight: Decimal = None) -> SalesOrder:
        """Re-price delivery for the actual package weight, entered by admin
        at assignment time (mirrors the courier flow's existing manual-weight
        entry — no product in the catalog carries a weight field). A no-op
        unless a weight was actually given, and skipped once the order is
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

    def dispatch(self, order: SalesOrder, user: User) -> SalesOrder:
        order = self._transition(order, 'ON_THE_WAY', user)
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
        # layered on top of whatever's already in subtotal/discount_amount, clamped
        # so the order can't go negative.
        extra_discount = Decimal('0')
        if discount_type == 'PERCENTAGE':
            extra_discount = (order.subtotal * discount_value / 100).quantize(Decimal('0.01'))
        elif discount_type == 'FLAT':
            extra_discount = discount_value
        extra_discount = min(extra_discount, order.subtotal)

        order.subtotal -= extra_discount
        order.discount_amount += extra_discount
        order.grand_total = order.subtotal + order.delivery_charge
        order.save(update_fields=['subtotal', 'discount_amount', 'grand_total'])

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
        self._reverse_referral_bonus(order, user)
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
        # Only reverse accounting if a payment journal was already posted
        # (pre-delivery COD cancellations have no prior financial entry)
        if JournalEntry.objects.filter(reference_id=order.id, reference_type='PAYMENT').exists():
            self._create_return_journal(order, user)
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
        today  = timezone.now().date()
        prefix = f'JE-{today:%Y%m%d}-'
        last   = JournalEntry.objects.filter(entry_number__startswith=prefix).order_by('-entry_number').values_list('entry_number', flat=True).first()
        seq    = int(last.rsplit('-', 1)[1]) if last else 0
        return f'{prefix}{seq + 1:04d}'

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
        subtotal = SalesOrderItem.objects.filter(order=order).aggregate(total=Sum('line_total'))['total'] or Decimal('0')
        order.subtotal    = subtotal
        order.grand_total = subtotal + order.delivery_charge + order.tax_amount - order.cashback_used
        order.save(update_fields=['subtotal', 'grand_total'])

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

    def _create_return_journal(self, order: SalesOrder, user: User) -> None:
        cogs = sum(
            item.product.cost_price * item.quantity
            for item in order.items.select_related('product')
        )
        revenue = order.subtotal  # already net of discount
        entry = JournalEntry.objects.create(
            entry_number=self._next_entry_number(), reference_type='RETURN',
            reference_id=order.id,
            description_bn=f'পণ্য ফেরত — {order.order_number}',
            description_en=f'Goods Return — {order.order_number}',
            created_by=user, is_posted=True,
        )
        lines = [
            ('4000', revenue,       Decimal('0')),  # Dr Sales Revenue (reversal)
            ('1300', cogs,          Decimal('0')),  # Dr Inventory (stock back)
            ('1000', Decimal('0'),  revenue),       # Cr Cash (refund)
            ('5000', Decimal('0'),  cogs),          # Cr COGS (reversal)
        ]
        for code, debit, credit in lines:
            acct = self._acct(code)
            if acct and (debit or credit):
                JournalLine.objects.create(journal_entry=entry, account=acct, debit=debit, credit=credit)

    def _create_reversal_journal(self, order: SalesOrder, user: User) -> None:
        cogs = sum(
            item.product.cost_price * item.quantity
            for item in order.items.select_related('product')
        )
        revenue = order.subtotal  # already net of discount
        entry = JournalEntry.objects.create(
            entry_number=self._next_entry_number(), reference_type='RETURN',
            reference_id=order.id,
            description_bn=f'বিক্রয় বিপরীত — {order.order_number}',
            description_en=f'Sale reversal — {order.order_number}',
            created_by=user, is_posted=True,
        )
        lines = [
            ('4000', revenue,      Decimal('0')),  # Dr Sales Revenue
            ('1300', cogs,         Decimal('0')),  # Dr Inventory
            ('1100', Decimal('0'), revenue),       # Cr AR
            ('5000', Decimal('0'), cogs),          # Cr COGS
        ]
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
