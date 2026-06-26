import logging
import math
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from api.models import (
    Cart, CashbackTier, DeliveryCharge, SalesOrder, SalesOrderItem, OrderStatusLog,
    StockMovement, ProductPackageItem,
    Account, JournalEntry, JournalLine,
    ShippingAddress, Notification, User,
)

_DHAKA_DISTRICTS = {'dhaka', 'ঢাকা'}

def _delivery_charge(district: str, zone: str | None = None) -> Decimal:
    cfg = DeliveryCharge.get()
    if zone == 'inside':
        return cfg.inside_dhaka
    if zone == 'outside':
        return cfg.outside_dhaka
    return cfg.inside_dhaka if district.strip().lower() in _DHAKA_DISTRICTS else cfg.outside_dhaka

logger = logging.getLogger(__name__)


class CheckoutService:

    @transaction.atomic
    def checkout(self, user, payment_method: str = 'COD', shipping_address_id: str | None = None, delivery_zone: str | None = None) -> SalesOrder:
        cart  = Cart.objects.select_for_update().get(user=user)
        items = list(cart.items.select_related('product').select_for_update())

        if not items:
            raise ValidationError({'message_bn': 'কার্ট খালি', 'message_en': 'Cart is empty'})

        # Resolve shipping address: explicit id → default saved → profile fallback
        addr = None
        if shipping_address_id:
            addr = ShippingAddress.objects.filter(id=shipping_address_id, user=user).first()
        if addr is None:
            addr = ShippingAddress.objects.filter(user=user, is_default=True).first()

        if addr:
            s_name_bn    = addr.full_name_bn
            s_name_en    = addr.full_name_en
            s_phone      = addr.phone
            s_address_bn = addr.address_bn
            s_address_en = addr.address_en
            s_district   = addr.district
            s_thana      = addr.thana
            s_post_code  = addr.post_code
        else:
            profile      = user.profile
            s_name_bn    = profile.full_name_bn
            s_name_en    = profile.full_name_en
            s_phone      = user.phone
            s_address_bn = profile.address_bn
            s_address_en = profile.address_en
            s_district   = profile.district
            s_thana      = profile.thana
            s_post_code  = profile.post_code

        # Generate order number
        today        = timezone.now().date()
        prefix       = f'PG-{today:%Y%m%d}-'
        last         = SalesOrder.objects.filter(order_number__startswith=prefix).count()
        order_number = f'{prefix}{last + 1:04d}'

        original_subtotal = sum(i.product.original_price * i.quantity for i in items)
        subtotal          = sum(i.product.effective_price * i.quantity for i in items)
        discount_amount   = original_subtotal - subtotal
        delivery          = _delivery_charge(s_district or '', delivery_zone)
        grand_total       = subtotal + delivery

        # Auto-apply user's cashback balance
        profile          = user.profile
        cashback_used    = min(profile.cashback_balance, grand_total)
        grand_total      = grand_total - cashback_used

        order = SalesOrder.objects.create(
            order_number        = order_number,
            customer            = user,
            payment_method      = payment_method,
            payment_status      = 'UNPAID',
            status              = 'PENDING',
            shipping_name_bn    = s_name_bn,
            shipping_name_en    = s_name_en,
            shipping_phone      = s_phone,
            shipping_address_bn = s_address_bn,
            shipping_address_en = s_address_en,
            shipping_district   = s_district,
            shipping_thana      = s_thana,
            shipping_post_code  = s_post_code,
            subtotal            = subtotal,
            discount_amount     = discount_amount,
            delivery_charge     = delivery,
            grand_total         = grand_total,
            cashback_used       = cashback_used,
        )

        if cashback_used > 0:
            profile.cashback_balance -= cashback_used
            profile.save(update_fields=['cashback_balance'])

        for item in items:
            SalesOrderItem.objects.create(
                order                = order,
                product              = item.product,
                product_name_bn      = item.product.name_bn,
                product_name_en      = item.product.name_en,
                original_unit_price  = item.product.original_price,
                unit_price           = item.product.effective_price,
                quantity             = item.quantity,
                line_total           = item.product.effective_price * item.quantity,
            )
            self._deduct_stock(item.product, item.quantity, order.id, user)

        OrderStatusLog.objects.create(
            order=order, from_status='', to_status='PENDING', changed_by=user,
        )

        cashback = CashbackTier.calculate(grand_total)
        if cashback > 0:
            order.cashback_amount = cashback
            order.save(update_fields=['cashback_amount'])

        if payment_method != 'COD':
            self._create_sale_journal(order, user)
        cart.items.all().delete()
        self._notify_admins(order)

        logger.info(f"Order created: {order.order_number} customer={user.email} payment={payment_method}")
        return order

    # ── helpers ───────────────────────────────────────────────────────────────

    def _deduct_stock(self, product, quantity: Decimal, order_id, user) -> None:
        if product.is_package:
            for pi in ProductPackageItem.objects.filter(package=product).select_related('component'):
                StockMovement.objects.create(
                    product=pi.component, movement_type='SALE',
                    quantity=-(pi.quantity * quantity), reference_id=order_id, created_by=user,
                )
        else:
            StockMovement.objects.create(
                product=product, movement_type='SALE',
                quantity=-quantity, reference_id=order_id, created_by=user,
            )

    def _create_sale_journal(self, order: SalesOrder, user) -> None:
        today        = timezone.now().date()
        prefix       = f'JE-{today:%Y%m%d}-'
        last         = JournalEntry.objects.filter(entry_number__startswith=prefix).order_by('-entry_number').values_list('entry_number', flat=True).first()
        entry_number = f'{prefix}{(int(last.rsplit("-", 1)[1]) if last else 0) + 1:04d}'

        cogs = sum(
            item.product.cost_price * item.quantity
            for item in order.items.select_related('product')
        )

        entry = JournalEntry.objects.create(
            entry_number=entry_number, reference_type='SALE', reference_id=order.id,
            description_bn=f'বিক্রয় — {order.order_number}',
            description_en=f'Sale — {order.order_number}',
            created_by=user, is_posted=True,
        )

        def _acct(code):
            try:
                return Account.objects.get(code=code)
            except Account.DoesNotExist:
                return None

        lines = [
            ('1100', order.grand_total,                    Decimal('0')),  # Dr AR
            ('4000', Decimal('0'),                         order.subtotal),  # Cr Revenue
            ('4200', Decimal('0'), Decimal(str(order.delivery_charge))),  # Cr Delivery
            ('2100', Decimal('0'),                         order.tax_amount),  # Cr Tax
            ('5000', cogs,                                 Decimal('0')),  # Dr COGS
            ('1300', Decimal('0'),                         cogs),  # Cr Inventory
        ]
        cb_used = Decimal(str(order.cashback_used or 0))
        if cb_used > 0:
            lines.append(('2250', cb_used, Decimal('0')))  # Dr Cashback Payable

        for code, debit, credit in lines:
            acct = _acct(code)
            if acct and (debit or credit):
                JournalLine.objects.create(
                    journal_entry=entry, account=acct, debit=debit, credit=credit,
                )

    def _notify_admins(self, order: SalesOrder) -> None:
        admins  = User.objects.filter(role='ADMIN', is_active=True)
        amount  = f'৳{math.ceil(order.grand_total):,}'
        name_bn = order.shipping_name_bn or order.shipping_name_en or '—'
        name_en = order.shipping_name_en or order.shipping_name_bn or '—'
        notifications = [
            Notification(
                user=admin,
                title_bn=f'নতুন অর্ডার — {order.order_number}',
                title_en=f'New Order — {order.order_number}',
                body_bn=f'{name_bn} থেকে **{amount}** মূল্যের অর্ডার।',
                body_en=f'Order of **{amount}** from {name_en}.',
                reference_type='ORDER_CREATED',
                reference_id=order.id,
            )
            for admin in admins
        ]
        Notification.objects.bulk_create(notifications)
