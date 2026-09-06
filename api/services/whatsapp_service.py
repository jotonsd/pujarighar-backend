import hashlib
import hmac
import logging

import requests
from django.db import transaction

from api.models import SiteSetting, WhatsAppConversation
from api.services import support_chat_service

logger = logging.getLogger(__name__)

_API_VERSION = 'v21.0'
_MAX_WHATSAPP_TEXT = 4096  # WhatsApp's own hard limit per text message


def is_configured() -> bool:
    s = SiteSetting.get()
    return bool(s.whatsapp_enabled and s.whatsapp_phone_number_id and s.whatsapp_access_token)


def verify_signature(raw_body: bytes, signature_header: str | None) -> bool:
    """X-Hub-Signature-256 is 'sha256=<hex digest>' of the raw request body,
    HMAC'd with the app secret — same role Pathao's X-PATHAO-Signature plays
    on the courier webhook, just Meta's own header/format."""
    s = SiteSetting.get()
    if not s.whatsapp_app_secret or not signature_header:
        return False
    try:
        algo, sig = signature_header.split('=', 1)
    except ValueError:
        return False
    if algo != 'sha256':
        return False
    expected = hmac.new(s.whatsapp_app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


def send_whatsapp_message(to: str, text: str) -> None:
    s = SiteSetting.get()
    if not is_configured():
        logger.warning('WhatsApp send skipped: not configured')
        return
    url = f'https://graph.facebook.com/{_API_VERSION}/{s.whatsapp_phone_number_id}/messages'
    payload = {
        'messaging_product': 'whatsapp',
        'to': to,
        'type': 'text',
        'text': {'body': text[:_MAX_WHATSAPP_TEXT]},
    }
    try:
        resp = requests.post(
            url, json=payload,
            headers={'Authorization': f'Bearer {s.whatsapp_access_token}'},
            timeout=15,
        )
        if not resp.ok:
            logger.error(f'WhatsApp send failed: {resp.status_code} {resp.text}')
    except requests.RequestException as e:
        logger.error(f'WhatsApp send error: {e}', exc_info=True)


@transaction.atomic
def handle_incoming_message(payload: dict) -> None:
    """Parses one WhatsApp Cloud API webhook payload, routes text messages
    through the exact same support_chat_service.answer() the website widget
    uses, and sends the reply back. Non-text messages (image/audio/sticker/
    etc.) get a short fallback reply rather than silently no-op'ing, since a
    customer who gets no response at all looks like the business ignored
    them."""
    try:
        entry = payload.get('entry', [{}])[0]
        change = entry.get('changes', [{}])[0]
        value = change.get('value', {})
        messages = value.get('messages')
        if not messages:
            # Delivery/read status callbacks land here too — nothing to reply to.
            return
        message = messages[0]
        wa_id = message.get('from')
        message_id = message.get('id')
        msg_type = message.get('type')
    except (IndexError, AttributeError):
        logger.warning(f'WhatsApp webhook: unrecognized payload shape: {payload!r}')
        return

    if not wa_id or not message_id:
        return

    conversation, _ = WhatsAppConversation.objects.select_for_update().get_or_create(wa_id=wa_id)
    if conversation.last_message_id == message_id:
        # Meta redelivered a webhook we've already processed — never reply twice.
        return

    if msg_type == 'text':
        text = (message.get('text') or {}).get('body', '').strip()
    else:
        text = ''

    if not text:
        reply_text = (
            'দুঃখিত, আমরা এখন শুধু লেখা বার্তা বুঝতে পারি। আপনার প্রশ্নটি লিখে পাঠান।'
            ' | Sorry, we can currently only understand text messages — please type your question.'
        )
        send_whatsapp_message(wa_id, reply_text)
        conversation.last_message_id = message_id
        conversation.save(update_fields=['last_message_id', 'updated_at'])
        return

    try:
        result = support_chat_service.answer(text, conversation.history, conversation.pending_order)
    except Exception as e:
        logger.error(f'WhatsApp support chat error for {wa_id}: {e}', exc_info=True)
        send_whatsapp_message(
            wa_id,
            'দুঃখিত, এই মুহূর্তে উত্তর দিতে সমস্যা হচ্ছে। একটু পরে আবার চেষ্টা করুন।'
            ' | Sorry, having trouble replying right now — please try again shortly.',
        )
        conversation.last_message_id = message_id
        conversation.save(update_fields=['last_message_id', 'updated_at'])
        return

    reply = result.get('reply') or ''
    products = result.get('products') or []
    if products:
        # WhatsApp text messages can't render the image/price cards the
        # website widget shows — append a plain-text product list instead so
        # the customer still gets the same information.
        lines = [reply, '']
        for p in products[:8]:
            name = p.get('name_bn') or p.get('name_en') or ''
            price = p.get('price')
            lines.append(f'• {name} — ৳{price}' if price else f'• {name}')
        reply = '\n'.join(lines)

    send_whatsapp_message(wa_id, reply or '...')

    history = list(conversation.history or [])
    history.append({'role': 'user', 'text': text})
    history.append({'role': 'model', 'text': reply})
    conversation.history = history[-support_chat_service._MAX_HISTORY_TURNS:]
    conversation.pending_order = result.get('pending_order')
    conversation.last_message_id = message_id
    conversation.save(update_fields=['history', 'pending_order', 'last_message_id', 'updated_at'])
