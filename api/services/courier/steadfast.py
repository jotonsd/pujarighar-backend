import logging
from decimal import Decimal

import requests

from api.models import CourierConsignment, CourierProvider, SalesOrder
from api.utils.crypto import decrypt_token
from .base import BaseCourierService

logger = logging.getLogger(__name__)

TIMEOUT = 30


class SteadfastCourierService(BaseCourierService):

    def __init__(self, provider: CourierProvider):
        self.provider = provider
        self.base_url = (provider.base_url or 'https://portal.packzy.com/api/v1').rstrip('/')
        self.api_key = decrypt_token(provider.api_key_encrypted)
        self.secret_key = decrypt_token(provider.secret_key_encrypted)

    def _headers(self) -> dict:
        return {
            'Api-Key': self.api_key,
            'Secret-Key': self.secret_key,
            'Content-Type': 'application/json',
        }

    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f'{self.base_url}{path}'
        try:
            resp = requests.request(method, url, headers=self._headers(), timeout=TIMEOUT, **kwargs)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.error(f'Steadfast request failed: {method} {url} — {e}', exc_info=True)
            raise Exception('Steadfast is unreachable right now. Please try again.')

    def create_order(self, order: SalesOrder, weight=None) -> dict:
        name = order.shipping_name_en or order.shipping_name_bn or 'Customer'
        address_parts = [
            order.shipping_address_en or order.shipping_address_bn or '',
            order.shipping_thana, order.shipping_district, order.shipping_post_code,
        ]
        address = ', '.join(p for p in address_parts if p)
        cod_amount = order.grand_total if (order.payment_method == 'COD' and order.payment_status == 'UNPAID') else Decimal('0')
        items = order.items.select_related('product').all()
        item_description = ', '.join(
            (i.product_name_en or i.product_name_bn) for i in items
        )

        payload = {
            'invoice': order.order_number,
            'recipient_name': name,
            'recipient_phone': order.shipping_phone,
            'recipient_address': address,
            'cod_amount': str(cod_amount),
            'note': order.notes_en or order.notes_bn or '',
            'item_description': item_description,
            'total_lot': items.count(),
        }
        if weight:
            payload['item_weight'] = str(weight)
        return self._request('POST', '/create_order', json=payload)

    def check_status(self, consignment: CourierConsignment) -> dict:
        cid = consignment.consignment_id
        if cid:
            return self._request('GET', f'/status_by_cid/{cid}')
        if consignment.tracking_code:
            return self._request('GET', f'/status_by_trackingcode/{consignment.tracking_code}')
        return self._request('GET', f'/status_by_invoice/{consignment.order.order_number}')

    def get_balance(self) -> dict:
        return self._request('GET', '/get_balance')

    def create_return_request(self, consignment: CourierConsignment, reason: str) -> dict:
        payload = {'consignment_id': consignment.consignment_id, 'reason': reason}
        return self._request('POST', '/create_return_request', json=payload)

    def get_return_request(self, provider_request_id: str) -> dict:
        return self._request('GET', f'/get_return_request/{provider_request_id}')

    def list_return_requests(self) -> dict:
        return self._request('GET', '/get_return_requests')

    def list_payments(self) -> dict:
        return self._request('GET', '/payments')

    def get_payment(self, payment_id: str) -> dict:
        return self._request('GET', f'/payments/{payment_id}')

    def list_police_stations(self) -> dict:
        return self._request('GET', '/police_stations')
