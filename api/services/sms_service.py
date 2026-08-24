import logging
import re
import threading
import time

import requests

from api.models import SiteSetting, SmsLog

logger = logging.getLogger(__name__)

_API_URL = 'http://bulksmsbd.net/api/smsapi'
_BULK_SEND_DELAY_SECONDS = 0.3

_RESPONSE_MEANINGS = {
    202:  'SMS Submitted Successfully',
    1001: 'Invalid Number',
    1002: 'Sender ID not correct/disabled',
    1003: 'Required fields missing',
    1005: 'Internal Error',
    1006: 'Balance Validity Not Available',
    1007: 'Balance Insufficient',
    1011: 'User Id not found',
    1012: 'Masking SMS must be sent in Bengali',
    1013: 'Sender Id has no Gateway by api key',
    1014: 'Sender Type Name not found by api key',
    1015: 'Sender Id has no valid Gateway by api key',
    1016: 'Sender Type Name Active Price Info not found',
    1017: 'Sender Type Name Price Info not found',
    1018: 'Account owner disabled',
    1019: 'Account price disabled',
    1020: 'Parent account not found',
    1021: 'Parent active price not found',
    1031: 'Account not verified',
    1032: 'IP not whitelisted',
}


def _normalize_bd_phone(phone: str) -> str | None:
    """BulkSMSBD expects 8801XXXXXXXXX (13 digits, country code, no plus) —
    every phone number in this app is stored in the local 01XXXXXXXXX form."""
    digits = re.sub(r'\D', '', phone or '')
    if digits.startswith('880') and len(digits) == 13:
        return digits
    if digits.startswith('0') and len(digits) == 11:
        return '880' + digits[1:]
    return None


def _send_one(phone: str, message: str, order=None) -> None:
    """Synchronous single-recipient send + SmsLog write — the shared core
    behind both the fire-and-forget single send and the sequential bulk
    sender, so every SMS this app sends (order-triggered or bulk) is logged
    to the same place. No-ops quietly (no log row) if the api key/sender id
    aren't configured yet, same as Telegram."""
    try:
        s = SiteSetting.get()
        if not s.sms_api_key or not s.sms_sender_id:
            return
        number = _normalize_bd_phone(phone)
        if not number:
            SmsLog.objects.create(
                order=order, phone=phone or '', message=message,
                status='FAILED', response_text='Invalid phone number',
            )
            logger.warning(f'SMS skipped (invalid phone): {phone!r}')
            return

        resp = requests.get(
            _API_URL,
            params={
                'api_key':  s.sms_api_key,
                'type':     'text',
                'number':   number,
                'senderid': s.sms_sender_id,
                'message':  message,
            },
            timeout=10,
        )
        text = resp.text.strip()
        try:
            code = int(text)
        except ValueError:
            try:
                code = resp.json().get('response_code')
            except Exception:
                code = None

        success = code == 202
        SmsLog.objects.create(
            order=order, phone=number, message=message,
            status='SUCCESS' if success else 'FAILED',
            response_code=str(code) if code is not None else '',
            response_text=_RESPONSE_MEANINGS.get(code, text[:255]),
        )
        if not success:
            logger.warning(f'SMS send failed: code={code} body={text}')
        else:
            logger.info(f'SMS sent to {number}')
    except Exception as e:
        logger.error(f'SMS send error: {e}', exc_info=True)
        try:
            SmsLog.objects.create(
                order=order, phone=phone or '', message=message,
                status='FAILED', response_text=str(e)[:255],
            )
        except Exception:
            pass


def send_sms(phone: str, message: str, order=None) -> None:
    """Fire-and-forget customer SMS via BulkSMSBD — mirrors telegram_service's
    async pattern so it never blocks the request."""
    threading.Thread(target=_send_one, args=(phone, message, order), daemon=True).start()


def send_bulk_sms(phones: list[str], message: str) -> None:
    """Fire-and-forget promotional blast to many recipients — runs
    sequentially in ONE background thread (not one thread per recipient)
    with a small stagger between sends, so a large recipient list doesn't
    hammer the gateway with hundreds of concurrent requests at once. Each
    recipient still gets its own SmsLog row via _send_one, so the dashboard
    Logs tab shows bulk sends the same way it shows order confirmations."""
    def _send_all():
        for phone in phones:
            _send_one(phone, message)
            time.sleep(_BULK_SEND_DELAY_SECONDS)
    threading.Thread(target=_send_all, daemon=True).start()
