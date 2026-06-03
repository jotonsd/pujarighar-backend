import logging
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from api.models import (
    SalesOrder, SalesOrderItem, OrderStatusLog,
    StockMovement, ProductPackageItem,
    Account, JournalEntry, JournalLine,
)

logger = logging.getLogger(__name__)


class GuestCheckoutService:

    @transaction.atomic
    def checkout(self, validated_data: dict) -> SalesOrder:
        items          = validated_data['items']
        shipping       = validated_data
        payment_method = validated_data.get('payment_method', 'COD')

        # Validate stock for all items
        for item in items:
            self._validate_stock(item['product'], item['quantity'])

        # Generate order number
        today        = timezone.now().date()
        prefix       = f'PG-{today:%Y%m%d}-'
        last         = SalesOrder.objects.filter(order_number__startswith=prefix).count()
        order_number = f'{prefix}{last + 1:04d}'

        subtotal    = sum(i['product'].unit_price * i['quantity'] for i in items)
        grand_total = subtotal

        order = SalesOrder.objects.create(
            order_number        = order_number,
            customer            = None,
            is_guest            = True,
            guest_email         = shipping.get('email', ''),
            payment_method      = payment_method,
            payment_status      = 'UNPAID',
            status              = 'PENDING',
            shipping_name_bn    = shipping['name_bn'],
            shipping_name_en    = shipping.get('name_en', ''),
            shipping_phone      = shipping['phone'],
            shipping_address_bn = shipping['address_bn'],
            shipping_address_en = shipping.get('address_en', ''),
            shipping_district   = shipping['district'],
            shipping_thana      = shipping['thana'],
            shipping_post_code  = shipping['post_code'],
            notes_bn            = shipping.get('notes_bn', ''),
            subtotal            = subtotal,
            grand_total         = grand_total,
        )

        for item in items:
            SalesOrderItem.objects.create(
                order           = order,
                product         = item['product'],
                product_name_bn = item['product'].name_bn,
                product_name_en = item['product'].name_en,
                unit_price      = item['product'].unit_price,
                quantity        = item['quantity'],
                line_total      = item['product'].unit_price * item['quantity'],
            )
            self._deduct_stock(item['product'], item['quantity'], order.id)

        system_user = self._get_system_user()
        OrderStatusLog.objects.create(
            order=order, from_status='', to_status='PENDING',
            changed_by=system_user,
        )

        self._create_sale_journal(order)
        logger.info(f"Guest order created: {order.order_number} phone={order.shipping_phone}")
        return order

    # ── helpers ───────────────────────────────────────────────────────────────

    def _get_system_user(self):
        from api.models import User
        return User.objects.filter(role='ADMIN').first()

    def _validate_stock(self, product, quantity: Decimal) -> None:
        if product.is_package:
            for pi in ProductPackageItem.objects.filter(package=product).select_related('component'):
                if pi.component.stock_on_hand < pi.quantity * quantity:
                    raise ValidationError({
                        'message_bn': f'{pi.component.name_bn}: পর্যাপ্ত স্টক নেই',
                        'message_en': f'{pi.component.name_en}: Insufficient stock',
                    })
        else:
            if product.stock_on_hand < quantity:
                raise ValidationError({
                    'message_bn': 'পর্যাপ্ত স্টক নেই',
                    'message_en': 'Insufficient stock',
                })

    def _deduct_stock(self, product, quantity: Decimal, order_id) -> None:
        user = self._get_system_user()
        if product.is_package:
            for pi in ProductPackageItem.objects.filter(package=product).select_related('component'):
                StockMovement.objects.create(
                    product=pi.component, movement_type='SALE',
                    quantity=-(pi.quantity * quantity),
                    reference_id=order_id, created_by=user,
                )
        else:
            StockMovement.objects.create(
                product=product, movement_type='SALE',
                quantity=-quantity, reference_id=order_id, created_by=user,
            )

    def _create_sale_journal(self, order: SalesOrder) -> None:
        user = self._get_system_user()
        if not user:
            return

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
            description_bn=f'গেস্ট বিক্রয় — {order.order_number}',
            description_en=f'Guest Sale — {order.order_number}',
            created_by=user, is_posted=True,
        )

        def _acct(code):
            try:
                return Account.objects.get(code=code)
            except Account.DoesNotExist:
                return None

        for code, debit, credit in [
            ('1100', order.grand_total, Decimal('0')),
            ('4000', Decimal('0'),      order.subtotal),
            ('5000', cogs,              Decimal('0')),
            ('1300', Decimal('0'),      cogs),
        ]:
            acct = _acct(code)
            if acct and (debit or credit):
                JournalLine.objects.create(
                    journal_entry=entry, account=acct, debit=debit, credit=credit,
                )
