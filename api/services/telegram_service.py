import logging
import threading

import requests

from api.models import SiteSetting

logger = logging.getLogger(__name__)

_API_URL = 'https://api.telegram.org/bot{token}/sendMessage'


def _send_to(token: str, chat_id: str, text: str) -> None:
    def _send():
        try:
            if not token or not chat_id:
                return
            resp = requests.post(
                _API_URL.format(token=token),
                json={'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'},
                timeout=10,
            )
            if not resp.ok:
                logger.warning(f'Telegram send failed: {resp.status_code} {resp.text}')
        except Exception as e:
            logger.error(f'Telegram send error: {e}', exc_info=True)
    threading.Thread(target=_send, daemon=True).start()


def send_telegram_message(text: str) -> None:
    """Fire-and-forget admin notification to the configured Telegram group —
    mirrors mail_service._send_async's async pattern so it never blocks the
    request. No-ops quietly if the bot token/chat id aren't configured yet."""
    s = SiteSetting.get()
    _send_to(s.telegram_bot_token, s.telegram_chat_id, text)


def send_courier_telegram_message(text: str) -> None:
    """Same bot, a separate group — courier webhook traffic (Pathao/
    Steadfast) is far higher-volume than the order-lifecycle events
    send_telegram_message carries, so it gets its own chat rather than
    drowning that group out. No-ops quietly if telegram_courier_chat_id
    isn't configured (independent of whether the main chat id is)."""
    s = SiteSetting.get()
    _send_to(s.telegram_bot_token, s.telegram_courier_chat_id, text)
