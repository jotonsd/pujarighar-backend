import logging
import math
import requests
from decimal import Decimal
from django.conf import settings
from django.db import transaction
from api.models import (
    SalesOrder, SalesOrderItem, PaymentTransaction, PendingCheckout, OrderStatusLog,
    User, Account, JournalEntry, JournalLine,
    StockMovement, ProductPackageItem, Notification, PaymentMethod, Cart,
)
from api.services import mail_service
from api.services.notification_recipients import get_notified_users
from api.services.notification_ws import broadcast_notification, broadcast_notifications
from api.services.push_service import send_push_to_user
from api.services.telegram_service import send_telegram_message
from api.utils.journal_number import next_entry_number
from api.utils.order_number import generate_order_number

logger = logging.getLogger(__name__)


class SSLCommerzService:

    def __init__(self):
        self.store_id   = settings.SSLCOMMERZ_STORE_ID
        self.store_pass = settings.SSLCOMMERZ_STORE_PASS
        self.api_url    = settings.SSLCOMMERZ_API_URL
        self.val_url    = settings.SSLCOMMERZ_VALIDATION_URL

    def _post_to_gateway(self, tran_id: str, amount: Decimal, num_items: int, backend_url: str,
                          cus_name: str, cus_email: str, cus_phone: str,
                          cus_add1: str, cus_city: str, post_code: str) -> str:
        """Shared by initiate_payment (an already-existing order — e.g. the
        "Pay Now" retry on an unpaid COD order) and initiate_payment_for_pending
        (a brand-new online checkout, order not created yet). Only the
        customer/amount details differ between those two callers."""
        post_data = {
            'store_id':        self.store_id,
            'store_passwd':    self.store_pass,
            'total_amount':    str(amount),
            'currency':        'BDT',
            'tran_id':         tran_id,
            'success_url':     f'{backend_url}/api/payments/success/',
            'fail_url':        f'{backend_url}/api/payments/fail/',
            'cancel_url':      f'{backend_url}/api/payments/cancel/',
            'ipn_url':         f'{backend_url}/api/payments/ipn/',
            'cus_name':        cus_name,
            'cus_email':       cus_email,
            'cus_phone':       cus_phone,
            'cus_add1':        cus_add1,
            'cus_city':        cus_city,
            'cus_postcode':    post_code,
            'cus_country':     'Bangladesh',
            'ship_name':       cus_name,
            'ship_add1':       cus_add1,
            'ship_city':       cus_city,
            'ship_postcode':   post_code,
            'ship_country':    'Bangladesh',
            'shipping_method': 'Courier',
            'product_name':    'Pujarighar Products',
            'product_category':'Religious Goods',
            'product_profile': 'general',
            'num_of_item':     str(num_items),
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

        return data['GatewayPageURL']

    def initiate_payment(self, order: SalesOrder, backend_url: str) -> str:
        """For an order that already exists — e.g. "Pay Now" on an unpaid
        COD order (see order_views.pay_order). Not used by the normal
        online-checkout flow anymore; see initiate_payment_for_pending for
        that (order isn't created until payment actually succeeds)."""
        tran_id = f'PG-{order.order_number}-{PaymentTransaction.objects.filter(order=order).count() + 1}'

        cus_email = 'guest@pujarighar.local'
        if order.customer and order.customer.email:
            cus_email = order.customer.email
        elif order.guest_email:
            cus_email = order.guest_email

        gateway_url = self._post_to_gateway(
            tran_id, order.grand_total, order.items.count(), backend_url,
            cus_name  = order.shipping_name_bn or order.shipping_name_en or 'Customer',
            cus_email = cus_email,
            cus_phone = order.shipping_phone,
            cus_add1  = order.shipping_address_bn or 'N/A',
            cus_city  = order.shipping_district or 'Dhaka',
            post_code = order.shipping_post_code or '1000',
        )

        # order is a OneToOneField on PaymentTransaction — a second "Pay
        # Now" click after backing out of a first attempt would otherwise
        # violate that uniqueness trying to INSERT another row for the same
        # order. update_or_create reuses/resets the existing one instead,
        # so a retry always gets a fresh tran_id/INITIATED status.
        PaymentTransaction.objects.update_or_create(
            order=order,
            defaults={'tran_id': tran_id, 'status': 'INITIATED', 'val_id': '', 'bank_tran_id': '', 'card_type': ''},
        )
        logger.info(f'SSLCommerz session created for existing order {order.order_number}')
        return gateway_url

    def initiate_payment_for_pending(self, pending: PendingCheckout, backend_url: str) -> str:
        """The normal online-checkout path — pending.tran_id was already
        generated when the PendingCheckout was created (see CheckoutService.
        initiate_online_checkout / GuestCheckoutService.initiate_online_checkout);
        the SalesOrder itself doesn't exist yet and won't until confirm_payment
        sees this succeed."""
        cus_email = 'guest@pujarighar.local'
        if pending.user and pending.user.email:
            cus_email = pending.user.email
        elif pending.guest_email:
            cus_email = pending.guest_email

        gateway_url = self._post_to_gateway(
            pending.tran_id, pending.grand_total, len(pending.items_snapshot), backend_url,
            cus_name  = pending.shipping_name_bn or pending.shipping_name_en or 'Customer',
            cus_email = cus_email,
            cus_phone = pending.shipping_phone,
            cus_add1  = pending.shipping_address_bn or 'N/A',
            cus_city  = pending.shipping_district or 'Dhaka',
            post_code = pending.shipping_post_code or '1000',
        )
        logger.info(f'SSLCommerz session created for pending checkout {pending.tran_id}')
        return gateway_url

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

    def confirm_payment(self, tran_id: str, val_id: str, post_data: dict) -> SalesOrder | None:
        """Validates the payment and either (a) marks an already-existing
        order paid (the "Pay Now" retry path), or (b) actually creates the
        order for the first time from its PendingCheckout snapshot (the
        normal online-checkout path). Returns the order if successful,
        None otherwise. Idempotent against a duplicate IPN/redirect
        callback for the same transaction either way."""
        txn = PaymentTransaction.objects.select_related('order').filter(tran_id=tran_id).first()
        if txn:
            return self._confirm_existing_order_payment(txn, val_id, post_data)

        pending = PendingCheckout.objects.filter(tran_id=tran_id).first()
        if pending:
            return self._confirm_pending_checkout(pending, val_id, post_data)

        logger.warning(f'No PaymentTransaction or PendingCheckout found for tran_id={tran_id}')
        return None

    @transaction.atomic
    def _confirm_existing_order_payment(self, txn: PaymentTransaction, val_id: str, post_data: dict) -> SalesOrder | None:
        if txn.status == 'PAID':
            return txn.order

        verification = self.verify_transaction(val_id)
        if verification.get('status') not in ('VALID', 'VALIDATED'):
            txn.status = 'FAILED'
            txn.save(update_fields=['status', 'updated_at'])
            logger.warning(f'SSLCommerz verification failed for tran_id={txn.tran_id}')
            return None

        txn.status       = 'PAID'
        txn.val_id       = val_id
        txn.bank_tran_id = post_data.get('bank_tran_id', '')
        txn.card_type    = post_data.get('card_type', '')
        txn.amount       = post_data.get('amount')
        txn.save()

        order = txn.order
        order.payment_status = 'PAID'
        # This "Pay Now" retry path is SSLCommerz-specific (initiate_payment
        # is only ever called for that gateway) — update the order's
        # recorded method to match reality instead of leaving it at
        # whatever it was chosen as originally (typically COD).
        order.payment_method = 'SSLCOMMERZ'
        # Stock was already committed when this order was first placed
        # (only COD/already-created orders reach this path — see
        # initiate_payment's docstring) — nothing to deduct here, unlike
        # the brand-new-order path in _confirm_pending_checkout.
        update_fields = ['payment_status', 'payment_method', 'updated_at']
        admin = User.objects.filter(role__code='ADMIN').first()
        if order.status == 'PENDING' and admin:
            order.status = 'CONFIRMED'
            update_fields.append('status')
        order.save(update_fields=update_fields)

        if admin:
            if order.status == 'CONFIRMED':
                OrderStatusLog.objects.create(
                    order=order, from_status='PENDING', to_status='CONFIRMED', changed_by=admin,
                )
            self._create_payment_journal(order, admin)
        else:
            logger.error(
                f'Payment confirmed for order {order.order_number} but no ADMIN-role user exists — '
                f'no PAYMENT journal was posted for this order. Needs manual reconciliation.'
            )

        mail_service.send_order_confirmed(order)
        self._notify_admins_paid(order)
        self._notify_customer_paid(order)
        self._send_payment_telegram(order)
        logger.info(f'Payment confirmed for existing order {order.order_number}')
        return order

    def _confirm_pending_checkout(self, pending: PendingCheckout, val_id: str, post_data: dict) -> SalesOrder | None:
        if pending.status == 'CONFIRMED':
            return pending.created_order
        if pending.status in ('FAILED', 'CANCELLED'):
            # A stale retry (e.g. duplicate IPN) after the customer already
            # backed out via payment_fail/payment_cancel — nothing to do.
            return pending.created_order

        verification = self.verify_transaction(val_id)
        if verification.get('status') not in ('VALID', 'VALIDATED'):
            pending.status = 'FAILED'
            pending.save(update_fields=['status', 'updated_at'])
            logger.warning(f'SSLCommerz verification failed for tran_id={pending.tran_id}')
            return None

        # DB work only, inside its own transaction — notifications (below,
        # outside it) include an async SMS thread (mail_service's
        # _send_async pattern) that writes to the DB on a separate
        # connection; firing that before this transaction commits raced
        # ahead of the order actually existing and hit a FK violation.
        order = self._create_order_from_pending(pending, val_id, post_data)

        mail_service.send_order_created(order)
        mail_service.send_order_confirmed(order)
        self._notify_admins_paid(order)
        self._notify_customer_paid(order)
        self._send_payment_telegram(order)
        logger.info(f'Payment confirmed, order created: {order.order_number} (was {pending.tran_id})')
        return order

    @transaction.atomic
    def _create_order_from_pending(self, pending: PendingCheckout, val_id: str, post_data: dict) -> SalesOrder:
        admin = User.objects.filter(role__code='ADMIN').first()
        order_user = pending.user

        order = SalesOrder.objects.create(
            order_number        = generate_order_number(),
            customer            = order_user,
            is_guest            = pending.is_guest,
            guest_email         = pending.guest_email,
            payment_method      = pending.payment_method,
            payment_status      = 'PAID',
            status              = 'CONFIRMED',
            shipping_name_bn    = pending.shipping_name_bn,
            shipping_name_en    = pending.shipping_name_en,
            shipping_phone      = pending.shipping_phone,
            shipping_address_bn = pending.shipping_address_bn,
            shipping_address_en = pending.shipping_address_en,
            shipping_district   = pending.shipping_district,
            shipping_thana      = pending.shipping_thana,
            shipping_post_code  = pending.shipping_post_code,
            notes_bn            = pending.notes_bn,
            source              = pending.source,
            subtotal            = pending.subtotal,
            discount_amount     = pending.discount_amount,
            first_order_discount_amount = pending.first_order_discount_amount,
            mobile_app_discount_amount  = pending.mobile_app_discount_amount,
            delivery_charge     = pending.delivery_charge,
            estimated_weight_kg = pending.estimated_weight_kg,
            gateway_charge_amount = pending.gateway_charge_amount,
            grand_total         = pending.grand_total,
            cashback_used       = pending.cashback_used_estimate,
        )

        # cashback_used_estimate was computed against the customer's balance
        # at checkout time — re-clamp the actual deduction against their
        # CURRENT balance (it could have shifted, e.g. spent on another
        # order, in the time it took to complete payment) so it can never
        # go negative. The order's own recorded numbers stay exactly what
        # SSLCommerz was actually told to charge, since that's real money
        # already moved.
        if order_user and pending.cashback_used_estimate > 0:
            profile = order_user.profile
            actual_deduction = min(pending.cashback_used_estimate, profile.cashback_balance)
            if actual_deduction > 0:
                profile.cashback_balance -= actual_deduction
                profile.save(update_fields=['cashback_balance'])

        for snap in pending.items_snapshot:
            SalesOrderItem.objects.create(
                order                = order,
                product_id           = snap['product_id'],
                product_name_bn      = snap['product_name_bn'],
                product_name_en      = snap['product_name_en'],
                original_unit_price  = Decimal(snap['original_unit_price']),
                unit_price           = Decimal(snap['unit_price']),
                quantity             = Decimal(snap['quantity']),
                line_total           = Decimal(snap['line_total']),
            )

        self._deduct_stock(order)

        OrderStatusLog.objects.create(
            order=order, from_status='', to_status='CONFIRMED',
            changed_by=order_user or admin,
        )

        if admin:
            self._create_payment_journal(order, admin)
        else:
            logger.error(
                f'Order {order.order_number} created from paid checkout but no ADMIN-role user '
                f'exists — no PAYMENT journal was posted. Needs manual reconciliation.'
            )

        # Registered customer only — a guest has no server-side Cart to
        # clear (their cart lives client-side, cleared by the frontend once
        # it lands on the success page).
        if order_user:
            cart = Cart.objects.filter(user=order_user).first()
            if cart:
                cart.items.all().delete()

        PaymentTransaction.objects.create(
            order=order, tran_id=pending.tran_id, val_id=val_id,
            bank_tran_id=post_data.get('bank_tran_id', ''),
            card_type=post_data.get('card_type', ''),
            amount=post_data.get('amount'), status='PAID',
        )

        pending.status = 'CONFIRMED'
        pending.created_order = order
        pending.save(update_fields=['status', 'created_order', 'updated_at'])
        return order

    def _send_payment_telegram(self, order: SalesOrder) -> None:
        _, method_en = self._method_label(order.payment_method)
        send_telegram_message(
            f"💳 <b>Payment Received — Order #{order.order_number}</b>\n"
            f"Customer: {order.shipping_name_bn or order.shipping_name_en}\n"
            f"Method: {method_en}\n"
            f"Total: ৳{math.ceil(order.grand_total):,}"
        )

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
        """In-app admin notification for a confirmed ONLINE payment."""
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
        send_push_to_user. No-ops quietly for a guest order (no account to
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
        """Package-aware SALE StockMovement per item — mirrors
        CheckoutService._deduct_stock / GuestCheckoutService._deduct_stock
        exactly, just triggered at payment confirmation instead of
        checkout time for an online order that's only just been created."""
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
        confirmed — Dr Cash+COGS / Cr Revenue+Delivery+Inventory, mirroring
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
