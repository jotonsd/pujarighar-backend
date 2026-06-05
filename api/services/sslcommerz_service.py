import logging
import requests
from django.conf import settings
from api.models import SalesOrder, PaymentTransaction, OrderStatusLog, User

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

        admin = User.objects.filter(role='ADMIN').first()
        if admin:
            OrderStatusLog.objects.create(
                order=order, from_status='PENDING', to_status='CONFIRMED', changed_by=admin,
            )

        logger.info(f'Payment confirmed for order {order.order_number}')
        return order
