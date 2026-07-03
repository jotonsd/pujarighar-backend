import logging
from decimal import Decimal
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from api.models import (
    SalesOrder, OrderStatusLog, DeliveryAssignment, User,
    StockMovement, Account, JournalEntry, JournalLine, Notification,
    ReferralBonus,
)
from api.utils.dates import local_day_start, local_day_end_exclusive
from api.services.notification_ws import broadcast_notification

logger = logging.getLogger(__name__)


class OrderService:

    def list_orders(self, user: User, params: dict):
        role = user.role
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

    def get_order(self, pk: str) -> SalesOrder:
        return SalesOrder.objects.prefetch_related('items__product', 'status_logs', 'delivery').get(pk=pk)

    def confirm(self, order: SalesOrder, user: User) -> SalesOrder:
        return self._transition(order, 'CONFIRMED', user)

    def pack(self, order: SalesOrder, user: User) -> SalesOrder:
        return self._transition(order, 'PACKED', user)

    @transaction.atomic
    def assign_delivery(self, order: SalesOrder, delivery_person_id: str, user: User) -> SalesOrder:
        delivery_person = User.objects.get(id=delivery_person_id, role='DELIVERY')
        DeliveryAssignment.objects.update_or_create(order=order, defaults={'delivery_person': delivery_person})
        updated = self._transition(order, 'ASSIGNED', user)
        self._notify_delivery_person(updated, delivery_person)
        return updated

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
        amount = Decimal('8.00')
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
