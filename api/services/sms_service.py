import logging
import re
import threading

import requests

from api.models import SiteSetting, SmsLog

logger = logging.getLogger(__name__)

_API_URL = 'http://bulksmsbd.net/api/smsapi'

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


def send_sms(phone: str, message: str, order=None) -> None:
    """Fire-and-forget customer SMS via BulkSMSBD — mirrors telegram_service's
    async pattern so it never blocks the request. No-ops quietly (no log row)
    if the api key/sender id aren't configured yet, same as Telegram; once
    configured, every real send attempt is recorded to SmsLog for the admin
    SMS dashboard regardless of outcome."""
    def _send():
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
    threading.Thread(target=_send, daemon=True).start()
