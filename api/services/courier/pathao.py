import logging
from datetime import timedelta
from decimal import Decimal

import requests
from django.utils import timezone

from api.models import CourierConsignment, CourierProvider, SalesOrder
from api.utils.crypto import decrypt_token, encrypt_token
from .base import BaseCourierService

logger = logging.getLogger(__name__)

TIMEOUT = 30
# Refresh a bit before the token's real expiry so an in-flight request never
# races a token that just went stale.
EXPIRY_SAFETY_MARGIN = timedelta(minutes=5)


class PathaoCourierService(BaseCourierService):
    """Pathao Courier's Merchant API is OAuth2 (password grant), unlike
    Steadfast's static API-key/secret — client_id/client_secret reuse
    CourierProvider's api_key/secret_key fields, and the issued
    access/refresh token is cached on the provider row itself (see
    _ensure_token) so most requests don't need a fresh login."""

    def __init__(self, provider: CourierProvider):
        self.provider = provider
        self.base_url = (provider.base_url or 'https://api-hermes.pathao.com').rstrip('/')
        self.client_id = decrypt_token(provider.api_key_encrypted)
        self.client_secret = decrypt_token(provider.secret_key_encrypted)
        self.username = decrypt_token(provider.username_encrypted)
        self.password = decrypt_token(provider.password_encrypted)

    def _request(self, method: str, path: str, auth: bool = True, **kwargs) -> dict:
        url = f'{self.base_url}{path}'
        headers = {'Content-Type': 'application/json'}
        if auth:
            headers['Authorization'] = f'Bearer {self._ensure_token()}'
        try:
            resp = requests.request(method, url, headers=headers, timeout=TIMEOUT, **kwargs)
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError:
            # Pathao actually responded, just not with success (expired/bad
            # credentials, invalid store_id, a rejected payload, their own
            # 5xx, etc.) — surface what it said instead of the generic
            # "unreachable" message below, which made every failure look
            # identical to a real network outage and impossible to debug
            # from the admin-facing error alone.
            detail = resp.text[:300]
            try:
                body = resp.json()
                message = body.get('message') or body.get('error') or str(body)
                # A 422 "Please fix the given errors" on its own says nothing
                # — Pathao's validation responses (Laravel-style) put the
                # actual field-level reasons in a separate "errors" object,
                # e.g. {"errors": {"recipient_city": ["invalid city id"]}}.
                errors = body.get('errors')
                detail = f'{message} — {errors}' if errors else message
            except ValueError:
                pass
            logger.error(f'Pathao request failed: {method} {url} — {resp.status_code} {detail}', exc_info=True)
            raise Exception(f'Pathao rejected the request ({resp.status_code}): {detail}')
        except requests.RequestException as e:
            # Pathao never actually responded — a genuine connection failure
            # or timeout, unlike the HTTPError case above.
            logger.error(f'Pathao request failed: {method} {url} — {e}', exc_info=True)
            raise Exception('Pathao is unreachable right now. Please try again.')

    def _ensure_token(self) -> str:
        """Returns a valid access token, transparently issuing or refreshing
        one and persisting it back onto the provider row when needed."""
        if (
            self.provider.access_token_encrypted
            and self.provider.token_expires_at
            and timezone.now() < self.provider.token_expires_at - EXPIRY_SAFETY_MARGIN
        ):
            return decrypt_token(self.provider.access_token_encrypted)

        if self.provider.refresh_token_encrypted:
            try:
                return self._issue_token(grant_type='refresh_token')
            except Exception as e:
                logger.warning(f'Pathao token refresh failed, falling back to password grant: {e}')

        return self._issue_token(grant_type='password')

    def _issue_token(self, grant_type: str) -> str:
        payload = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'grant_type': grant_type,
        }
        if grant_type == 'password':
            payload['username'] = self.username
            payload['password'] = self.password
        else:
            payload['refresh_token'] = decrypt_token(self.provider.refresh_token_encrypted)

        data = self._request('POST', '/aladdin/api/v1/issue-token', auth=False, json=payload)

        self.provider.access_token_encrypted = encrypt_token(data['access_token'])
        self.provider.refresh_token_encrypted = encrypt_token(data['refresh_token'])
        self.provider.token_expires_at = timezone.now() + timedelta(seconds=data.get('expires_in', 432000))
        self.provider.save(update_fields=[
            'access_token_encrypted', 'refresh_token_encrypted', 'token_expires_at', 'updated_at',
        ])
        return data['access_token']

    def create_order(self, order: SalesOrder, weight=None, note=None) -> dict:
        if not self.provider.store_id:
            raise Exception('Pathao store_id is not configured for this provider — set it in courier provider settings first.')

        name = order.shipping_name_en or order.shipping_name_bn or 'Customer'
        address_parts = [
            order.shipping_address_en or order.shipping_address_bn or '',
            order.shipping_thana, order.shipping_district, order.shipping_post_code,
        ]
        address = ', '.join(p for p in address_parts if p)
        amount_to_collect = order.grand_total if (order.payment_method == 'COD' and order.payment_status == 'UNPAID') else Decimal('0')
        items = order.items.select_related('product').all()
        item_description = ', '.join(
            (i.product_name_en or i.product_name_bn) for i in items
        )
        # Pathao requires 0.5–10 kg — fall back to the minimum when no weight
        # was provided (mirrors Steadfast's item_weight being optional there).
        item_weight = str(weight) if weight else '0.5'

        payload = {
            'store_id': int(self.provider.store_id),
            'merchant_order_id': order.order_number,
            'recipient_name': name,
            'recipient_phone': order.shipping_phone,
            'recipient_address': address,
            'delivery_type': 48,   # Normal Delivery
            'item_type': 2,        # Parcel
            'special_instruction': note or order.notes_en or order.notes_bn or '',
            'item_quantity': items.count() or 1,
            'item_weight': item_weight,
            'item_description': item_description,
            'amount_to_collect': int(amount_to_collect),
        }
        result = self._request('POST', '/aladdin/api/v1/orders', json=payload)
        data = result.get('data', result)

        # Normalized to the same {'consignment': {...}} envelope Steadfast
        # returns, so CourierService.send_order's unwrap logic (result.get
        # ('consignment', result)) works unchanged for both providers.
        return {
            'consignment': {
                'consignment_id': data.get('consignment_id', ''),
                'tracking_code':  data.get('consignment_id', ''),
                'status':         data.get('order_status', 'Pending'),
                'cod_amount':     str(amount_to_collect),
            },
            'pathao_response': result,
        }

    def check_status(self, consignment: CourierConsignment) -> dict:
        result = self._request('GET', f'/aladdin/api/v1/orders/{consignment.consignment_id}/info')
        data = result.get('data', result)
        # Normalized to a top-level 'status' key, matching what
        # CourierService.refresh_status reads (result.get('delivery_status',
        # result.get('status', ...))).
        return {
            'status': data.get('order_status_slug') or data.get('order_status', consignment.status),
            'pathao_response': result,
        }

    def get_balance(self) -> dict:
        raise NotImplementedError('Pathao does not provide a balance API')

    def list_stores(self) -> dict:
        """Not part of BaseCourierService — used by admin settings to help
        pick/verify a store_id rather than during normal order flow."""
        return self._request('GET', '/aladdin/api/v1/stores')
