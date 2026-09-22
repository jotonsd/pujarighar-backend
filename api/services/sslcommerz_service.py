import logging
import math
import requests
from decimal import Decimal
from django.conf import settings
from django.db import transaction
from api.models import (
    SalesOrder, PaymentTransaction, OrderStatusLog, User, Account, JournalEntry, JournalLine,
    StockMovement, ProductPackageItem, Notification, PaymentMethod,
)
from api.services import mail_service
from api.services.notification_recipients import get_notified_users
from api.services.notification_ws import broadcast_notification, broadcast_notifications
from api.services.push_service import send_push_to_user
from api.services.telegram_service import send_telegram_message
from api.utils.journal_number import next_entry_number

logger = logging.getLogger(__name__)


class SSLCommerzService:

    def __init__(self):
        self.store_id   = settings.SSLCOMMERZ_STORE_ID
        self.store_pass = settings.SSLCOMMERZ_STORE_PASS
        self.api_url    = settings.SSLCOMMERZ_API_URL
        self.val_url    = settings.SSLCOMMERZ_VALIDATION_URL

    def initiate_payment(self, order: SalesOrder, backend_url: str) -> str:
        tran_id = f'PG-{order.order_number}'

        cus_email = 'guest@pujarighar.local'
        if order.customer and order.customer.email:
            cus_email = order.customer.email
        elif order.guest_email:
            cus_email = order.guest_email

        cus_name  = order.shipping_name_bn or order.shipping_name_en or 'Customer'
        cus_add1  = order.shipping_address_bn or 'N/A'
        cus_city  = order.shipping_district or 'Dhaka'
        post_code = order.shipping_post_code or '1000'

        post_data = {
            'store_id':        self.store_id,
            'store_passwd':    self.store_pass,
            'total_amount':    str(order.grand_total),
            'currency':        'BDT',
            'tran_id':         tran_id,
            'success_url':     f'{backend_url}/api/payments/success/',
            'fail_url':        f'{backend_url}/api/payments/fail/',
            'cancel_url':      f'{backend_url}/api/payments/cancel/',
            'ipn_url':         f'{backend_url}/api/payments/ipn/',
            # Customer info
            'cus_name':        cus_name,
            'cus_email':       cus_email,
            'cus_phone':       order.shipping_phone,
            'cus_add1':        cus_add1,
            'cus_city':        cus_city,
            'cus_postcode':    post_code,
            'cus_country':     'Bangladesh',
            # Shipping info (required by SSLCommerz)
            'ship_name':       cus_name,
            'ship_add1':       cus_add1,
            'ship_city':       cus_city,
            'ship_postcode':   post_code,
            'ship_country':    'Bangladesh',
            # Product info
            'shipping_method': 'Courier',
            'product_name':    'Pujarighar Products',
            'product_category':'Religious Goods',
            'product_profile': 'general',
            'num_of_item':     str(order.items.count()),
        }

        try:
            resp = requests.post(self.api_url, data=post_data, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            logger.error(f'SSLCommerz initiate request failed: {e}', exc_info=True)
            raise Exception('Payment gateway unreachable. Please try again.')

        if data.get('status') != 'SUCCESS':
            reason = data.get('failedreason', 'Payment initiation failed')
            logger.error(f'SSLCommerz initiation failed: {reason}')
            raise Exception(reason)

        PaymentTransaction.objects.create(
            order       = order,
            tran_id     = tran_id,
            session_key = data.get('sessionkey', ''),
        )

        logger.info(f'SSLCommerz session created for order {order.order_number}')
        return data['GatewayPageURL']

    def verify_transaction(self, val_id: str) -> dict:
        try:
            resp = requests.get(self.val_url, params={
                'val_id':      val_id,
                'store_id':    self.store_id,
                'store_passwd':self.store_pass,
                'format':      'json',
            }, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.error(f'SSLCommerz verification failed: {e}', exc_info=True)
            return {'status': 'FAILED'}

    @transaction.atomic
    def confirm_payment(self, tran_id: str, val_id: str, post_data: dict) -> SalesOrder | None:
        """
        Validate the payment and confirm the linked order.
        Returns the order if successful, None otherwise.
        Guards against double-processing.
        """
        try:
            txn = PaymentTransaction.objects.select_related('order').get(tran_id=tran_id)
        except PaymentTransaction.DoesNotExist:
            logger.warning(f'PaymentTransaction not found for tran_id={tran_id}')
            return None

        if txn.status == 'PAID':
            return txn.order

        verification = self.verify_transaction(val_id)
        if verification.get('status') not in ('VALID', 'VALIDATED'):
            txn.status = 'FAILED'
            txn.save(update_fields=['status', 'updated_at'])
            logger.warning(f'SSLCommerz verification failed for tran_id={tran_id}')
            return None

        txn.status       = 'PAID'
        txn.val_id       = val_id
        txn.bank_tran_id = post_data.get('bank_tran_id', '')
        txn.card_type    = post_data.get('card_type', '')
        txn.amount       = post_data.get('amount')
        txn.save()

        order = txn.order
        order.payment_status = 'PAID'
        order.status         = 'CONFIRMED'
        order.save(update_fields=['payment_status', 'status', 'updated_at'])

        # Stock was deliberately NOT committed at checkout for an online
        # order (see CheckoutService.checkout / GuestCheckoutService.
        # checkout) — this is the first point payment is actually
        # confirmed, so it's committed here instead. The top-of-function
        # `txn.status == 'PAID'` guard makes this idempotent against a
        # duplicate IPN/redirect callback for the same transaction.
        self._deduct_stock(order)

        admin = User.objects.filter(role__code='ADMIN').first()
        if admin:
            OrderStatusLog.objects.create(
                order=order, from_status='PENDING', to_status='CONFIRMED', changed_by=admin,
            )
            self._create_payment_journal(order, admin)
        else:
            # Real gateway money already moved and the order is already
            # marked PAID above — don't fail the customer's payment
            # confirmation over a missing admin account, but this must not
            # disappear silently: no journal gets posted for this order
            # until an admin exists again and someone reconciles it by hand.
            logger.error(
                f'Payment confirmed for order {order.order_number} but no ADMIN-role user exists — '
                f'no OrderStatusLog/PAYMENT journal was posted for this order. Needs manual reconciliation.'
            )

        mail_service.send_order_confirmed(order)
        self._notify_admins_paid(order)
        self._notify_customer_paid(order)
        _, method_en = self._method_label(order.payment_method)
        send_telegram_message(
            f"💳 <b>Payment Received — Order #{order.order_number}</b>\n"
            f"Customer: {order.shipping_name_bn or order.shipping_name_en}\n"
            f"Method: {method_en}\n"
            f"Total: ৳{math.ceil(order.grand_total):,}"
        )
        logger.info(f'Payment confirmed for order {order.order_number}')
        return order

    def _method_label(self, code: str) -> tuple[str, str]:
        """Friendly (bn, en) display name for a payment_method code, e.g.
        'SSLCOMMERZ' -> ('অনলাইন পেমেন্ট (SSLCommerz)', 'Online Payment
        (SSLCommerz)') — falls back to the raw code if the PaymentMethod
        row is somehow missing (never expected, but notifications should
        degrade gracefully rather than error)."""
        method = PaymentMethod.objects.filter(code=code).first()
        if method:
            return method.name_bn, method.name_en
        return code, code

    def _notify_admins_paid(self, order: SalesOrder) -> None:
        """In-app admin notification for a confirmed ONLINE payment —
        mirrors CheckoutService._notify_admins exactly, just fired at
        payment confirmation instead of order placement (which already
        got its own ORDER_CREATED notification via that method)."""
        admins  = get_notified_users()
        amount  = f'৳{math.ceil(order.grand_total):,}'
        name_bn = order.shipping_name_bn or order.shipping_name_en or '—'
        name_en = order.shipping_name_en or order.shipping_name_bn or '—'
        method_bn, method_en = self._method_label(order.payment_method)
        notifications = [
            Notification(
                user=admin,
                title_bn=f'পেমেন্ট সফল হয়েছে — {order.order_number}',
                title_en=f'Payment Received — {order.order_number}',
                body_bn=f'{name_bn}-এর অর্ডারের **{amount}** পেমেন্ট নিশ্চিত হয়েছে।\nপেমেন্ট পদ্ধতি: **{method_bn}**',
                body_en=f'Payment of **{amount}** confirmed for {name_en}\'s order.\nPayment Method: **{method_en}**',
                reference_type='PAYMENT_CONFIRMED',
                reference_id=order.id,
            )
            for admin in admins
        ]
        Notification.objects.bulk_create(notifications)
        broadcast_notifications(notifications)

    def _notify_customer_paid(self, order: SalesOrder) -> None:
        """In-app + push notification to the customer — reaches whichever
        device(s) they're registered on (mobile app included) via
        send_push_to_user, same mechanism CheckoutService uses for
        ORDER_CREATED. No-ops quietly for a guest order (no account to
        notify)."""
        if order.is_guest or not order.customer_id:
            return
        amount = f'৳{math.ceil(order.grand_total):,}'
        method_bn, method_en = self._method_label(order.payment_method)
        notification = Notification.objects.create(
            user=order.customer,
            title_bn=f'পেমেন্ট সফল হয়েছে — {order.order_number}',
            title_en=f'Payment Successful — {order.order_number}',
            body_bn=f'আপনার অর্ডার #{order.order_number}-এর **{amount}** পেমেন্ট সফলভাবে সম্পন্ন হয়েছে।\nপেমেন্ট পদ্ধতি: **{method_bn}**',
            body_en=f'Your **{amount}** payment for order #{order.order_number} was successful.\nPayment Method: **{method_en}**',
            reference_type='PAYMENT_CONFIRMED',
            reference_id=order.id,
        )
        broadcast_notification(notification)
        send_push_to_user(
            order.customer,
            title_bn=notification.title_bn, title_en=notification.title_en,
            body_bn=notification.body_bn, body_en=notification.body_en,
            data={'reference_type': 'PAYMENT_CONFIRMED', 'reference_id': str(order.id)},
        )

    def _deduct_stock(self, order: SalesOrder) -> None:
        """Mirrors CheckoutService._deduct_stock / GuestCheckoutService.
        _deduct_stock exactly — package-aware SALE StockMovement per item,
        just triggered at payment confirmation instead of checkout time."""
        # StockMovement.created_by is required (no guest equivalent) — same
        # ADMIN fallback GuestCheckoutService._get_system_user() already uses.
        user = order.customer or User.objects.filter(role__code='ADMIN').first()
        for item in order.items.select_related('product'):
            product = item.product
            if product.is_package:
                for pi in ProductPackageItem.objects.filter(package=product).select_related('component'):
                    StockMovement.objects.create(
                        product=pi.component, movement_type='SALE',
                        quantity=-(pi.quantity * item.quantity), reference_id=order.id,
                        created_by=user,
                    )
            else:
                StockMovement.objects.create(
                    product=product, movement_type='SALE',
                    quantity=-item.quantity, reference_id=order.id,
                    created_by=user,
                )

    def _create_payment_journal(self, order: SalesOrder, user) -> None:
        """Revenue is recognized here, at the moment payment is actually
        confirmed — checkout no longer posts a speculative SALE journal
        before the customer has paid (see CheckoutService._create_order,
        which used to post Dr AR / Cr Revenue immediately at checkout; a
        sale that fails or is abandoned mid-payment would have overstated
        the books until someone noticed). This posts the full entry in one
        go instead — Dr Cash+COGS / Cr Revenue+Delivery+Inventory — mirroring
        COD's own OrderService._create_payment_journal exactly, tagged the
        same way (reference_type='PAYMENT') so downstream idempotency checks
        (e.g. OrderService.deliver()) recognize this order as already settled.
        """
        if JournalEntry.objects.filter(reference_type='PAYMENT', reference_id=order.id).exists():
            return

        entry_number = next_entry_number()

        cogs = sum(
            item.product.cost_price * item.quantity
            for item in order.items.select_related('product')
        )

        entry = JournalEntry.objects.create(
            entry_number=entry_number, reference_type='PAYMENT', reference_id=order.id,
            description_bn=f'পেমেন্ট গৃহীত (অনলাইন) — {order.order_number}',
            description_en=f'Payment Received (Online) — {order.order_number}',
            created_by=user, is_posted=True,
        )

        def _acct(code):
            try:
                return Account.objects.get(code=code)
            except Account.DoesNotExist:
                return None

        cb_used = Decimal(str(order.cashback_used or 0))
        gateway_charge = Decimal(str(order.gateway_charge_amount or 0))
        lines = [
            ('1000', order.grand_total,                    Decimal('0')),  # Dr Cash
            ('5000', cogs,                                 Decimal('0')),  # Dr COGS
            ('4000', Decimal('0'),                         order.subtotal),  # Cr Revenue
            ('4200', Decimal('0'), Decimal(str(order.delivery_charge))),   # Cr Delivery Income
            ('1300', Decimal('0'),                         cogs),          # Cr Inventory
        ]
        if cb_used > 0:
            lines.append(('2250', cb_used, Decimal('0')))  # Dr Cashback Payable
        if gateway_charge > 0:
            # Balances the gateway charge folded into grand_total above —
            # a real charge passed on to the customer, booked as other
            # income (offset separately, if ever, against the actual
            # SSLCommerz merchant fee expense at settlement/reconciliation).
            lines.append(('4300', Decimal('0'), gateway_charge))  # Cr Other Income

        for code, debit, credit in lines:
            acct = _acct(code)
            if acct and (debit or credit):
                JournalLine.objects.create(journal_entry=entry, account=acct, debit=debit, credit=credit)
