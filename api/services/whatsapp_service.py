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


def _post_message(payload: dict) -> None:
    s = SiteSetting.get()
    if not is_configured():
        logger.warning('WhatsApp send skipped: not configured')
        return
    url = f'https://graph.facebook.com/{_API_VERSION}/{s.whatsapp_phone_number_id}/messages'
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


def send_whatsapp_message(to: str, text: str) -> None:
    _post_message({
        'messaging_product': 'whatsapp',
        'to': to,
        'type': 'text',
        'text': {'body': text[:_MAX_WHATSAPP_TEXT]},
    })


def send_whatsapp_image(to: str, image_url: str, caption: str = '') -> None:
    _post_message({
        'messaging_product': 'whatsapp',
        'to': to,
        'type': 'image',
        'image': {'link': image_url, 'caption': caption[:1024]},
    })


def send_whatsapp_list(to: str, body_text: str, button_text: str, items: list[dict]) -> None:
    """A real tappable menu (WhatsApp's 'interactive list' message) — the
    closest equivalent to the website's clickable product cards. Sent
    alongside the image messages (list rows can't show pictures), so the
    customer gets both a look at the products and a one-tap way to pick one
    instead of having to type the exact name back."""
    rows = []
    for item in items[:10]:  # WhatsApp's own cap on list rows
        name = item.get('name_bn') or item.get('name_en') or ''
        price = item.get('price')
        rows.append({
            'id': name[:200],
            'title': name[:24] or '-',
            'description': f'৳{price}' if price else '',
        })
    if not rows:
        return
    _post_message({
        'messaging_product': 'whatsapp',
        'to': to,
        'type': 'interactive',
        'interactive': {
            'type': 'list',
            'body': {'text': body_text[:1024]},
            'action': {'button': button_text[:20], 'sections': [{'rows': rows}]},
        },
    })


def _download_whatsapp_media(media_id: str) -> tuple[bytes | None, str | None]:
    """Media messages only carry an id — the actual file lives behind a
    short-lived, auth-gated URL that has to be looked up first, then fetched
    with the same bearer token (Meta doesn't serve media publicly)."""
    s = SiteSetting.get()
    headers = {'Authorization': f'Bearer {s.whatsapp_access_token}'}
    try:
        meta_resp = requests.get(f'https://graph.facebook.com/{_API_VERSION}/{media_id}', headers=headers, timeout=15)
        if not meta_resp.ok:
            logger.error(f'WhatsApp media lookup failed: {meta_resp.status_code} {meta_resp.text}')
            return None, None
        info = meta_resp.json()
        media_url = info.get('url')
        mime_type = info.get('mime_type', 'image/jpeg')
        if not media_url:
            return None, None
        file_resp = requests.get(media_url, headers=headers, timeout=30)
        if not file_resp.ok:
            logger.error(f'WhatsApp media download failed: {file_resp.status_code}')
            return None, None
        return file_resp.content, mime_type
    except requests.RequestException as e:
        logger.error(f'WhatsApp media fetch error: {e}', exc_info=True)
        return None, None


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
    elif msg_type == 'interactive':
        # A tap on one of our own send_whatsapp_list() rows — id carries the
        # exact product name (see send_whatsapp_list), so this flows into
        # answer() exactly like the customer typed that name themselves,
        # same as clicking a candidate card does on the website.
        list_reply = (message.get('interactive') or {}).get('list_reply') or {}
        text = list_reply.get('id') or list_reply.get('title', '')
    elif msg_type == 'image':
        media_id = (message.get('image') or {}).get('id')
        image_bytes, mime_type = _download_whatsapp_media(media_id) if media_id else (None, None)
        text = support_chat_service.describe_image_for_search(image_bytes, mime_type) if image_bytes else ''
    else:
        text = ''

    if not text:
        reply_text = (
            'দুঃখিত, ছবিটি বুঝতে সমস্যা হচ্ছে। আপনার প্রশ্নটি লিখে পাঠান।'
            ' | Sorry, having trouble understanding that — please describe what you\'re looking for in text.'
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
    # 'candidates' carries the same shape (name/price/image_url) but comes
    # from a different path — an order tool (add_order_item/propose_order)
    # hitting an ambiguous match (e.g. several dresses sharing a name), where
    # the website shows the same options as clickable cards for the customer
    # to disambiguate. Missing this here meant the AI would say "pick one
    # from the options below" on WhatsApp with nothing actually below it.
    candidates = result.get('candidates') or []
    send_whatsapp_message(wa_id, reply or '...')

    # Send each product/candidate as a real WhatsApp image message (name +
    # price as the caption) rather than a plain-text list — WhatsApp can't
    # render the website widget's clickable image/price cards, but it can
    # send actual photos, which reads far better than a text bullet list.
    # Capped at 5 combined to avoid flooding the chat on a broad search.
    for p in (products + candidates)[:5]:
        name = p.get('name_bn') or p.get('name_en') or ''
        price = p.get('price')
        caption = f'{name} — ৳{price}' if price else name
        image_url = p.get('image_url')
        if image_url:
            send_whatsapp_image(wa_id, image_url, caption)
        else:
            send_whatsapp_message(wa_id, f'• {caption}')

    # A real tappable menu, sent after the photos — list rows can't carry
    # images, so this is deliberately in addition to (not instead of) the
    # image messages above: pictures for reference, list for actually
    # picking one without having to type the exact name back.
    combined = (products + candidates)[:10]
    if len(combined) > 1:
        send_whatsapp_list(
            wa_id,
            body_text='নিচের তালিকা থেকে একটি বেছে নিন।',
            button_text='বেছে নিন',
            items=combined,
        )

    history = list(conversation.history or [])
    history.append({'role': 'user', 'text': text})
    history.append({'role': 'model', 'text': reply})
    conversation.history = history[-support_chat_service._MAX_HISTORY_TURNS:]
    conversation.pending_order = result.get('pending_order')
    conversation.last_message_id = message_id
    conversation.save(update_fields=['history', 'pending_order', 'last_message_id', 'updated_at'])
