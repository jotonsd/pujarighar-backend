import logging
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from api.models import (
    Cart, SalesOrder, SalesOrderItem, OrderStatusLog,
    StockMovement, ProductPackageItem,
    Account, JournalEntry, JournalLine,
    ShippingAddress,
)

logger = logging.getLogger(__name__)


class CheckoutService:

    @transaction.atomic
    def checkout(self, user, payment_method: str = 'COD', shipping_address_id: str | None = None) -> SalesOrder:
        cart  = Cart.objects.select_for_update().get(user=user)
        items = list(cart.items.select_related('product').select_for_update())

        if not items:
            from rest_framework.exceptions import ValidationError
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

        subtotal    = sum(i.product.unit_price * i.quantity for i in items)
        grand_total = subtotal

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
            grand_total         = grand_total,
        )

        for item in items:
            SalesOrderItem.objects.create(
                order           = order,
                product         = item.product,
                product_name_bn = item.product.name_bn,
                product_name_en = item.product.name_en,
                unit_price      = item.product.unit_price,
                quantity        = item.quantity,
                line_total      = item.product.unit_price * item.quantity,
            )
            self._deduct_stock(item.product, item.quantity, order.id, user)

        OrderStatusLog.objects.create(
            order=order, from_status='', to_status='PENDING', changed_by=user,
        )

        self._create_sale_journal(order, user)
        cart.items.all().delete()

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
        last         = JournalEntry.objects.filter(entry_number__startswith=prefix).count()
        entry_number = f'{prefix}{last + 1:04d}'

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

        for code, debit, credit in [
            ('1100', order.grand_total,  Decimal('0')),
            ('4000', Decimal('0'),       order.subtotal),
            ('2100', Decimal('0'),       order.tax_amount),
            ('5000', cogs,               Decimal('0')),
            ('1300', Decimal('0'),       cogs),
        ]:
            acct = _acct(code)
            if acct and (debit or credit):
                JournalLine.objects.create(
                    journal_entry=entry, account=acct, debit=debit, credit=credit,
                )
