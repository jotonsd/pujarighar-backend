import logging
import threading

import requests

from api.models import SiteSetting

logger = logging.getLogger(__name__)

_API_URL = 'https://api.telegram.org/bot{token}/sendMessage'


def send_telegram_message(text: str) -> None:
    """Fire-and-forget admin notification to the configured Telegram group —
    mirrors mail_service._send_async's async pattern so it never blocks the
    request. No-ops quietly if the bot token/chat id aren't configured yet."""
    def _send():
        try:
            s = SiteSetting.get()
            token, chat_id = s.telegram_bot_token, s.telegram_chat_id
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
