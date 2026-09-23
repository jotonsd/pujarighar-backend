import logging
import math
import uuid
from decimal import Decimal
from django.db import transaction
from rest_framework.exceptions import ValidationError
from api.models import (
    DeliveryCharge, SalesOrder, SalesOrderItem, OrderStatusLog,
    StockMovement, ProductPackageItem,
    Account, JournalEntry, JournalLine,
    User, Notification, PaymentMethod, SiteSetting, PendingCheckout,
)
from api.services.notification_recipients import get_notified_users
from api.services.notification_ws import broadcast_notifications
from api.utils.order_number import generate_order_number
from api.utils.journal_number import next_entry_number

_DHAKA_DISTRICTS = {'dhaka', 'ঢাকা'}

def _delivery_charge(district: str, zone: str | None = None, weight: Decimal | None = None) -> Decimal:
    resolved_zone = zone if zone in ('inside', 'outside') else (
        'inside' if district.strip().lower() in _DHAKA_DISTRICTS else 'outside'
    )
    return DeliveryCharge.get().charge_for(resolved_zone, weight)


def _cart_weight(items) -> Decimal:
    """Sum of product.weight_kg * quantity across cart items — see the
    identical helper in checkout_service.py (this one takes dict items,
    that one CartItem model instances, otherwise the same logic)."""
    return sum(
        ((i['product'].weight_kg or Decimal('0')) * i['quantity'] for i in items),
        Decimal('0'),
    )

logger = logging.getLogger(__name__)


class GuestCheckoutService:

    @transaction.atomic
    def checkout(self, validated_data: dict, customer: User | None = None,
                 discount_type: str = 'NONE', discount_value: Decimal = Decimal('0'),
                 is_pos: bool = False, is_mobile_app: bool = False) -> SalesOrder:
        items          = validated_data['items']
        shipping       = validated_data
        payment_method = validated_data.get('payment_method', 'COD')
        # is_pos identifies a staff POS sale unambiguously (passed by the
        # caller, never by request data); placed_via_ai is the AI chatbot's
        # own signal on the same public endpoint a real guest also uses;
        # is_mobile_app is the same idea for a guest checkout placed from
        # the Flutter app (view reads it off the X-Client-Platform header).
        if is_pos:
            source = 'POS'
        elif validated_data.get('placed_via_ai'):
            source = 'AI_CHATBOT'
        elif is_mobile_app:
            source = 'MOBILE_APP'
        else:
            source = 'WEBSITE'

        # Validate stock for all items
        for item in items:
            self._validate_stock(item['product'], item['quantity'])

        order_number = generate_order_number()

        original_subtotal = sum(i['product'].original_price * i['quantity'] for i in items)
        subtotal          = sum(i['product'].effective_price * i['quantity'] for i in items)

        # Optional staff-applied order discount (POS only) — layered on top of
        # any product-level discount, clamped so revenue can't go negative.
        # Not booked as its own ledger line: it just lowers the subtotal that
        # gets credited to Sales Revenue, same as product-level discounts do.
        extra_discount = Decimal('0')
        if discount_type == 'PERCENTAGE' and discount_value > 0:
            extra_discount = (subtotal * discount_value / 100).quantize(Decimal('0.01'))
        elif discount_type == 'FLAT' and discount_value > 0:
            extra_discount = discount_value
        extra_discount = min(extra_discount, subtotal)
        subtotal -= extra_discount

        # Standing app-adoption incentive — mirrors CheckoutService.checkout's
        # identical block; source='MOBILE_APP' already rules out POS/AI/website.
        mobile_app_discount_amount = Decimal('0')
        if source == 'MOBILE_APP':
            pct = SiteSetting.get().mobile_app_order_discount_percent
            if pct > 0:
                mobile_app_discount_amount = min(
                    (subtotal * pct / Decimal('100')).quantize(Decimal('0.01')),
                    subtotal,
                )
        subtotal -= mobile_app_discount_amount

        discount_amount   = original_subtotal - subtotal
        apply_deliv       = validated_data.get('apply_delivery', True)
        zone              = validated_data.get('delivery_zone')
        total_weight      = _cart_weight(items)
        delivery          = _delivery_charge(shipping.get('district', ''), zone, total_weight) if apply_deliv else Decimal('0')
        free_delivery_min = SiteSetting.get().free_delivery_min_subtotal
        if apply_deliv and free_delivery_min > 0 and subtotal >= free_delivery_min:
            delivery = Decimal('0')
        grand_total       = subtotal + delivery

        # Gateway charge — public guest checkout only (POS's own in-person
        # card/mobile-money payments aren't run through an online gateway,
        # so they never carry this charge). payment_method is the specific
        # gateway code itself (e.g. 'SSLCOMMERZ'), not a generic bucket.
        gateway_charge = Decimal('0')
        if payment_method != 'COD' and not is_pos:
            method = PaymentMethod.objects.filter(code=payment_method).first()
            if method:
                gateway_charge = method.charge_for(grand_total)
        grand_total += gateway_charge
        # Round up to a whole Taka — see CheckoutService.checkout's
        # identical block for why (this domain never charges poisha).
        if payment_method != 'COD' and not is_pos:
            ceiled = Decimal(math.ceil(grand_total))
            gateway_charge += ceiled - grand_total
            grand_total = ceiled

        order = SalesOrder.objects.create(
            order_number        = order_number,
            customer            = customer,
            is_guest            = customer is None,
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
            source                = source,
            subtotal              = subtotal,
            discount_amount       = discount_amount,
            staff_discount_amount = extra_discount,
            mobile_app_discount_amount = mobile_app_discount_amount,
            delivery_charge       = delivery,
            estimated_weight_kg   = total_weight,
            gateway_charge_amount = gateway_charge,
            grand_total           = grand_total,
        )

        for item in items:
            SalesOrderItem.objects.create(
                order                = order,
                product              = item['product'],
                product_name_bn      = item['product'].name_bn,
                product_name_en      = item['product'].name_en,
                original_unit_price  = item['product'].original_price,
                unit_price           = item['product'].effective_price,
                quantity             = item['quantity'],
                line_total           = item['product'].effective_price * item['quantity'],
            )
            # Same deferral as CheckoutService.checkout — COD (and POS,
            # which has already collected payment in person regardless of
            # payment_method) commits stock now; a public ONLINE guest
            # checkout defers to SSLCommerzService.confirm_payment so an
            # abandoned/failed payment never holds real inventory hostage.
            if payment_method == 'COD' or is_pos:
                self._deduct_stock(item['product'], item['quantity'], order.id)

        system_user = self._get_system_user()
        OrderStatusLog.objects.create(
            order=order, from_status='', to_status='PENDING',
            changed_by=system_user,
        )

        # POS: staff already collected the payment in person before hitting
        # submit — there's no separate gateway/confirmation step coming
        # later, so this IS the moment payment is confirmed; post now.
        # Public guest checkout: an ONLINE order redirects to SSLCommerz and
        # isn't actually paid yet — deferred to SSLCommerzService.confirm_payment,
        # same reasoning as the logged-in checkout flow.
        if payment_method != 'COD' and is_pos:
            self._create_sale_journal(order)
        self._notify_admins(order)
        logger.info(f"Guest order created: {order.order_number} phone={order.shipping_phone}")
        return order

    @transaction.atomic
    def initiate_online_checkout(self, validated_data: dict, is_mobile_app: bool = False) -> PendingCheckout:
        """Public guest checkout's online-payment path only (never POS/AI —
        those already collect payment differently, see checkout() above).
        Mirrors CheckoutService.initiate_online_checkout: prices the cart
        and snapshots it, but creates no SalesOrder, deducts no stock, until
        SSLCommerzService.confirm_payment sees the payment actually
        succeed — so backing out of the gateway page leaves nothing
        abandoned behind."""
        items    = validated_data['items']
        shipping = validated_data
        payment_method = validated_data.get('payment_method', 'COD')
        source = 'MOBILE_APP' if is_mobile_app else 'WEBSITE'

        for item in items:
            self._validate_stock(item['product'], item['quantity'])

        original_subtotal = sum(i['product'].original_price * i['quantity'] for i in items)
        subtotal          = sum(i['product'].effective_price * i['quantity'] for i in items)

        mobile_app_discount_amount = Decimal('0')
        if source == 'MOBILE_APP':
            pct = SiteSetting.get().mobile_app_order_discount_percent
            if pct > 0:
                mobile_app_discount_amount = min(
                    (subtotal * pct / Decimal('100')).quantize(Decimal('0.01')),
                    subtotal,
                )
        subtotal -= mobile_app_discount_amount

        discount_amount   = original_subtotal - subtotal
        apply_deliv       = validated_data.get('apply_delivery', True)
        zone              = validated_data.get('delivery_zone')
        total_weight      = _cart_weight(items)
        delivery          = _delivery_charge(shipping.get('district', ''), zone, total_weight) if apply_deliv else Decimal('0')
        free_delivery_min = SiteSetting.get().free_delivery_min_subtotal
        if apply_deliv and free_delivery_min > 0 and subtotal >= free_delivery_min:
            delivery = Decimal('0')
        grand_total = subtotal + delivery

        gateway_charge = Decimal('0')
        method = PaymentMethod.objects.filter(code=payment_method).first()
        if method:
            gateway_charge = method.charge_for(grand_total)
        grand_total += gateway_charge
        ceiled = Decimal(math.ceil(grand_total))
        gateway_charge += ceiled - grand_total
        grand_total = ceiled

        items_snapshot = [
            {
                'product_id':          str(item['product'].id),
                'product_name_bn':     item['product'].name_bn,
                'product_name_en':     item['product'].name_en,
                'quantity':            str(item['quantity']),
                'unit_price':          str(item['product'].effective_price),
                'original_unit_price': str(item['product'].original_price),
                'line_total':          str(item['product'].effective_price * item['quantity']),
            }
            for item in items
        ]

        pending = PendingCheckout.objects.create(
            tran_id      = f'PG-{uuid.uuid4().hex[:20].upper()}',
            user         = None,
            is_guest     = True,
            guest_email  = shipping.get('email', ''),
            payment_method = payment_method,
            source       = source,
            notes_bn     = shipping.get('notes_bn', ''),
            items_snapshot = items_snapshot,
            shipping_name_bn    = shipping['name_bn'],
            shipping_name_en    = shipping.get('name_en', ''),
            shipping_phone      = shipping['phone'],
            shipping_address_bn = shipping['address_bn'],
            shipping_address_en = shipping.get('address_en', ''),
            shipping_district   = shipping['district'],
            shipping_thana      = shipping['thana'],
            shipping_post_code  = shipping['post_code'],
            subtotal                    = subtotal,
            discount_amount             = discount_amount,
            mobile_app_discount_amount  = mobile_app_discount_amount,
            delivery_charge             = delivery,
            estimated_weight_kg         = total_weight,
            gateway_charge_amount       = gateway_charge,
            grand_total                 = grand_total,
        )
        logger.info(f"Guest pending checkout created: {pending.tran_id} phone={shipping['phone']}")
        return pending

    # ── helpers ───────────────────────────────────────────────────────────────

    def _get_system_user(self):
        return User.objects.filter(role__code='ADMIN').first()

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

        entry_number = next_entry_number()

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
            ('1100', order.grand_total,        Decimal('0')),
            ('4000', Decimal('0'),             order.subtotal),
            ('4200', Decimal('0'),             Decimal(str(order.delivery_charge))),
            ('2100', Decimal('0'),             order.tax_amount),
            ('5000', cogs,                     Decimal('0')),
            ('1300', Decimal('0'),             cogs),
        ]:
            acct = _acct(code)
            if acct and (debit or credit):
                JournalLine.objects.create(
                    journal_entry=entry, account=acct, debit=debit, credit=credit,
                )

    def _notify_admins(self, order: SalesOrder) -> None:
        admins  = get_notified_users()
        amount  = f'৳{math.ceil(order.grand_total):,}'
        name_bn = order.shipping_name_bn or order.shipping_name_en or '—'
        name_en = order.shipping_name_en or order.shipping_name_bn or '—'
        is_guest = order.is_guest
        notifications = [
            Notification(
                user=admin,
                title_bn=f'নতুন অর্ডার {"(গেস্ট) " if is_guest else ""}— {order.order_number}',
                title_en=f'New {"Guest " if is_guest else ""}Order — {order.order_number}',
                body_bn=f'{name_bn} থেকে **{amount}** মূল্যের অর্ডার।',
                body_en=f'Order of **{amount}** from {name_en}.',
                reference_type='ORDER_CREATED',
                reference_id=order.id,
            )
            for admin in admins
        ]
        Notification.objects.bulk_create(notifications)
        broadcast_notifications(notifications)
