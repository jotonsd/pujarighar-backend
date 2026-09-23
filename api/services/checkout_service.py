import logging
import math
import uuid
from decimal import Decimal
from django.db import transaction
from rest_framework.exceptions import ValidationError
from api.models import (
    Cart, CashbackTier, DeliveryCharge, SalesOrder, SalesOrderItem, OrderStatusLog,
    StockMovement, ProductPackageItem,
    ShippingAddress, Notification, SiteSetting, PaymentMethod, PendingCheckout,
)
from api.services.notification_recipients import get_notified_users
from api.services.notification_ws import broadcast_notification, broadcast_notifications
from api.services.push_service import send_push_to_user
from api.utils.order_number import generate_order_number

_DHAKA_DISTRICTS = {'dhaka', 'ঢাকা'}

def _delivery_charge(district: str, zone: str | None = None, weight: Decimal | None = None) -> Decimal:
    resolved_zone = zone if zone in ('inside', 'outside') else (
        'inside' if district.strip().lower() in _DHAKA_DISTRICTS else 'outside'
    )
    return DeliveryCharge.get().charge_for(resolved_zone, weight)


def _cart_weight(items) -> Decimal:
    """Sum of product.weight_kg * quantity across cart items — items without
    a weight set contribute 0, so this only affects pricing once weight
    brackets are actually configured (DeliveryCharge.charge_for falls back
    to the flat zone rate when none are)."""
    return sum(((i.product.weight_kg or Decimal('0')) * i.quantity for i in items), Decimal('0'))

logger = logging.getLogger(__name__)


class CheckoutService:

    @transaction.atomic
    def checkout(self, user, payment_method: str = 'COD', shipping_address_id: str | None = None,
                 delivery_zone: str | None = None, source: str = 'WEBSITE', notes_bn: str = '') -> SalesOrder:
        """COD only — an online gateway method never reaches this method
        (see cart_views.checkout, which routes those to
        initiate_online_checkout instead). COD is paid on delivery, so
        there's nothing to defer: the order, its stock deduction and its
        cart-clear all happen immediately, exactly as before."""
        cart  = Cart.objects.select_for_update().get(user=user)
        items = list(cart.items.select_related('product').select_for_update())

        if not items:
            raise ValidationError({'message_bn': 'কার্ট খালি', 'message_en': 'Cart is empty'})

        for item in items:
            self._validate_stock(item.product, item.quantity)

        shipping = self._resolve_shipping(user, shipping_address_id)
        pricing  = self._price_cart(items, user, source, payment_method, shipping['shipping_district'], delivery_zone)

        order = SalesOrder.objects.create(
            order_number        = generate_order_number(),
            customer            = user,
            payment_method      = payment_method,
            payment_status      = 'UNPAID',
            status              = 'PENDING',
            notes_bn            = notes_bn,
            source              = source,
            **shipping,
            **pricing['order_fields'],
        )

        if pricing['cashback_used'] > 0:
            profile = user.profile
            profile.cashback_balance -= pricing['cashback_used']
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

        cashback = CashbackTier.calculate(order.grand_total)
        if cashback > 0:
            order.cashback_amount = cashback
            order.save(update_fields=['cashback_amount'])

        cart.items.all().delete()
        self._notify_admins(order)
        self._notify_customer_created(order, user)

        logger.info(f"Order created: {order.order_number} customer={user.email} payment={payment_method}")
        return order

    @transaction.atomic
    def initiate_online_checkout(self, user, payment_method: str, shipping_address_id: str | None = None,
                                  delivery_zone: str | None = None, source: str = 'WEBSITE',
                                  notes_bn: str = '') -> PendingCheckout:
        """Prices the cart and snapshots everything needed to build the real
        order later, but does NOT create a SalesOrder, deduct stock, spend
        cashback or touch the cart — SSLCommerzService.confirm_payment does
        all of that, only once payment actually succeeds. Backing out of
        the gateway page this way leaves no abandoned order and the cart
        intact, unlike creating a PENDING/UNPAID order upfront."""
        cart  = Cart.objects.select_for_update().get(user=user)
        items = list(cart.items.select_related('product').select_for_update())

        if not items:
            raise ValidationError({'message_bn': 'কার্ট খালি', 'message_en': 'Cart is empty'})

        for item in items:
            self._validate_stock(item.product, item.quantity)

        shipping = self._resolve_shipping(user, shipping_address_id)
        pricing  = self._price_cart(items, user, source, payment_method, shipping['shipping_district'], delivery_zone)
        of       = pricing['order_fields']

        items_snapshot = [
            {
                'product_id':          str(item.product.id),
                'product_name_bn':     item.product.name_bn,
                'product_name_en':     item.product.name_en,
                'quantity':            str(item.quantity),
                'unit_price':          str(item.product.effective_price),
                'original_unit_price': str(item.product.original_price),
                'line_total':          str(item.product.effective_price * item.quantity),
            }
            for item in items
        ]

        pending = PendingCheckout.objects.create(
            tran_id      = f'PG-{uuid.uuid4().hex[:20].upper()}',
            user         = user,
            is_guest     = False,
            payment_method = payment_method,
            source       = source,
            notes_bn     = notes_bn,
            items_snapshot = items_snapshot,
            shipping_name_bn    = shipping['shipping_name_bn'],
            shipping_name_en    = shipping['shipping_name_en'],
            shipping_phone      = shipping['shipping_phone'],
            shipping_address_bn = shipping['shipping_address_bn'],
            shipping_address_en = shipping['shipping_address_en'],
            shipping_district   = shipping['shipping_district'],
            shipping_thana      = shipping['shipping_thana'],
            shipping_post_code  = shipping['shipping_post_code'],
            subtotal                    = of['subtotal'],
            discount_amount             = of['discount_amount'],
            first_order_discount_amount = of['first_order_discount_amount'],
            mobile_app_discount_amount  = of['mobile_app_discount_amount'],
            delivery_charge             = of['delivery_charge'],
            estimated_weight_kg         = of['estimated_weight_kg'],
            gateway_charge_amount       = of['gateway_charge_amount'],
            grand_total                 = of['grand_total'],
            cashback_used_estimate      = of['cashback_used'],
        )
        logger.info(f"Pending checkout created: {pending.tran_id} customer={user.email} payment={payment_method}")
        return pending

    # ── helpers ───────────────────────────────────────────────────────────────

    def _resolve_shipping(self, user, shipping_address_id: str | None) -> dict:
        addr = None
        if shipping_address_id:
            addr = ShippingAddress.objects.filter(id=shipping_address_id, user=user).first()
        if addr is None:
            addr = ShippingAddress.objects.filter(user=user, is_default=True).first()

        if addr:
            return {
                'shipping_name_bn': addr.full_name_bn, 'shipping_name_en': addr.full_name_en,
                'shipping_phone': addr.phone,
                'shipping_address_bn': addr.address_bn, 'shipping_address_en': addr.address_en,
                'shipping_district': addr.district, 'shipping_thana': addr.thana,
                'shipping_post_code': addr.post_code,
            }
        profile = user.profile
        return {
            'shipping_name_bn': profile.full_name_bn, 'shipping_name_en': profile.full_name_en,
            'shipping_phone': user.phone,
            'shipping_address_bn': profile.address_bn, 'shipping_address_en': profile.address_en,
            'shipping_district': profile.district, 'shipping_thana': profile.thana,
            'shipping_post_code': profile.post_code,
        }

    def _price_cart(self, items, user, source: str, payment_method: str,
                     district: str, delivery_zone: str | None) -> dict:
        """Shared by checkout() and initiate_online_checkout() — everything
        needed to price a registered customer's cart, identically either
        way. Returns {'order_fields': {...SalesOrder-ready kwargs...},
        'cashback_used': Decimal}."""
        original_subtotal = sum(i.product.original_price * i.quantity for i in items)
        subtotal          = sum(i.product.effective_price * i.quantity for i in items)
        product_discount  = original_subtotal - subtotal

        # Welcome discount — a registered customer's very first order only.
        # "First" means no prior SalesOrder at all, regardless of its
        # status, so cancel-and-reorder can't be used to re-earn it.
        first_order_discount_amount = Decimal('0')
        is_first_order = not SalesOrder.objects.filter(customer=user).exists()
        if is_first_order:
            pct = SiteSetting.get().first_order_discount_percent
            if pct > 0:
                first_order_discount_amount = min(
                    (subtotal * pct / Decimal('100')).quantize(Decimal('0.01')),
                    subtotal,
                )
        subtotal -= first_order_discount_amount

        # Standing app-adoption incentive — applies to every mobile-app
        # order, not just a first one, and stacks with the discount above.
        mobile_app_discount_amount = Decimal('0')
        if source == 'MOBILE_APP':
            pct = SiteSetting.get().mobile_app_order_discount_percent
            if pct > 0:
                mobile_app_discount_amount = min(
                    (subtotal * pct / Decimal('100')).quantize(Decimal('0.01')),
                    subtotal,
                )
        subtotal -= mobile_app_discount_amount

        discount_amount = product_discount + first_order_discount_amount + mobile_app_discount_amount
        total_weight     = _cart_weight(items)
        delivery         = _delivery_charge(district or '', delivery_zone, total_weight)
        free_delivery_min = SiteSetting.get().free_delivery_min_subtotal
        if free_delivery_min > 0 and subtotal >= free_delivery_min:
            delivery = Decimal('0')
        grand_total = subtotal + delivery

        profile       = user.profile
        cashback_used = min(profile.cashback_balance, grand_total)
        grand_total  -= cashback_used

        gateway_charge = Decimal('0')
        if payment_method != 'COD':
            method = PaymentMethod.objects.filter(code=payment_method).first()
            if method:
                gateway_charge = method.charge_for(grand_total)
        grand_total += gateway_charge
        if payment_method != 'COD':
            ceiled = Decimal(math.ceil(grand_total))
            gateway_charge += ceiled - grand_total
            grand_total = ceiled

        return {
            'order_fields': {
                'subtotal': subtotal,
                'discount_amount': discount_amount,
                'first_order_discount_amount': first_order_discount_amount,
                'mobile_app_discount_amount': mobile_app_discount_amount,
                'delivery_charge': delivery,
                'estimated_weight_kg': total_weight,
                'gateway_charge_amount': gateway_charge,
                'grand_total': grand_total,
                'cashback_used': cashback_used,
            },
            'cashback_used': cashback_used,
        }

    def _validate_stock(self, product, quantity: Decimal) -> None:
        if product.is_package:
            for pi in ProductPackageItem.objects.filter(package=product).select_related('component'):
                needed = pi.quantity * quantity
                if pi.component.stock_on_hand < needed:
                    raise ValidationError({
                        'message_bn': f'{pi.component.name_bn}: পর্যাপ্ত স্টক নেই',
                        'message_en': f'{pi.component.name_en}: Insufficient stock',
                    })
        else:
            if product.stock_on_hand < quantity:
                raise ValidationError({
                    'message_bn': f'{product.name_bn}: পর্যাপ্ত স্টক নেই',
                    'message_en': f'{product.name_en}: Insufficient stock',
                })

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

    def _notify_admins(self, order: SalesOrder) -> None:
        admins  = get_notified_users()
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
        broadcast_notifications(notifications)

    def _notify_customer_created(self, order: SalesOrder, user) -> None:
        # This checkout() path is only ever reached by a logged-in customer
        # (guest checkout is the separate GuestCheckoutService) — every
        # subsequent status change already pushes to them via
        # OrderService._notify_customer; this is just the missing first one,
        # confirming the order itself was placed.
        notification = Notification.objects.create(
            user=user,
            title_bn=f'অর্ডার সফল হয়েছে — {order.order_number}',
            title_en=f'Order Placed — {order.order_number}',
            body_bn=f'আপনার অর্ডার #{order.order_number} সফলভাবে গৃহীত হয়েছে।',
            body_en=f'Your order #{order.order_number} has been placed successfully.',
            reference_type='ORDER_CREATED',
            reference_id=order.id,
        )
        broadcast_notification(notification)
        send_push_to_user(
            user,
            title_bn=notification.title_bn, title_en=notification.title_en,
            body_bn=notification.body_bn, body_en=notification.body_en,
            data={'reference_type': 'ORDER_CREATED', 'reference_id': str(order.id)},
        )
