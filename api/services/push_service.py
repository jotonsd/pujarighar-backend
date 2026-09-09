import logging
from django.conf import settings

logger = logging.getLogger(__name__)

_firebase_app = None
_firebase_unavailable = False


def _get_app():
    """Lazily initializes the Firebase Admin app once per process. Returns
    None (never raises) when no service-account key is configured or it
    fails to load — every caller in this module treats that as "push is
    off", which just means the in-app Notification row (created regardless,
    by the caller) is all the customer gets. Push is additive, never a
    dependency the rest of checkout/order flows can break on."""
    global _firebase_app, _firebase_unavailable
    if _firebase_app is not None:
        return _firebase_app
    if _firebase_unavailable:
        return None
    key_path = getattr(settings, 'FIREBASE_SERVICE_ACCOUNT_PATH', '')
    if not key_path:
        _firebase_unavailable = True
        return None
    try:
        import firebase_admin
        from firebase_admin import credentials
        cred = credentials.Certificate(key_path)
        _firebase_app = firebase_admin.initialize_app(cred)
        return _firebase_app
    except Exception as e:
        logger.error(f"Firebase init error: {e}", exc_info=True)
        _firebase_unavailable = True
        return None


def _logo_image_url() -> str | None:
    """The full-color logo shown in the notification's large-icon slot —
    only matters for a background/terminated-app push, which Android
    auto-displays straight from this URL (downloaded on-device) rather
    than through any of the app's own code. The foreground path instead
    uses a bundled drawable (see mobile-app's NotificationService) since
    the app doesn't need a network fetch for something it already ships."""
    from api.models import SiteSetting
    setting = SiteSetting.get()
    if not setting.logo:
        return None
    try:
        # .url is relative (MEDIA_URL='/media/') — FCM needs an absolute,
        # publicly fetchable URL since Android downloads it directly.
        return f'{settings.BACKEND_URL}{setting.logo.url}'
    except Exception:
        return None


def _drop_invalid_tokens(tokens: list[str], responses) -> None:
    from api.models import DeviceToken
    invalid = [
        tokens[i] for i, r in enumerate(responses)
        if not r.success and r.exception is not None
        and 'registration-token-not-registered' in str(r.exception).lower()
    ]
    if invalid:
        DeviceToken.objects.filter(token__in=invalid).delete()


def _send_to_tokens(tokens: list[str], title_bn: str, body_bn: str, data: dict | None = None) -> int:
    """Sends one multicast per <=500 tokens (FCM's hard cap per call) and
    returns how many actually succeeded. Title/body are Bangla-only — see
    send_push_to_user's docstring for why per-recipient language can't be
    honored here."""
    app = _get_app()
    if app is None or not tokens:
        return 0
    import firebase_admin.messaging as messaging

    image_url = _logo_image_url()
    sent = 0
    for i in range(0, len(tokens), 500):
        batch = tokens[i:i + 500]
        message = messaging.MulticastMessage(
            notification=messaging.Notification(title=title_bn, body=body_bn, image=image_url),
            data={k: str(v) for k, v in (data or {}).items()},
            tokens=batch,
        )
        try:
            response = messaging.send_each_for_multicast(message, app=app)
            sent += response.success_count
            if response.failure_count:
                _drop_invalid_tokens(batch, response.responses)
        except Exception as e:
            logger.error(f"Push multicast error: {e}", exc_info=True)
    return sent


def send_push_to_user(user, title_bn: str, title_en: str, body_bn: str, body_en: str,
                       data: dict | None = None) -> None:
    """Best-effort push for a single event tied to one customer (order
    status change, etc). Bangla text always — the app's language toggle is
    a client-side, per-viewer setting the backend has no way to know at
    send time, unlike the in-app Notification row where the app itself
    picks bn/en from the pair it already fetched."""
    from api.models import DeviceToken
    tokens = list(DeviceToken.objects.filter(user=user).values_list('token', flat=True))
    if not tokens:
        return
    _send_to_tokens(tokens, title_bn, body_bn, data)


def send_promo_push(title_bn: str, body_bn: str, data: dict | None = None) -> int:
    """Broadcasts to every registered device across all customers. Returns
    how many pushes actually succeeded (for the admin panel's confirmation
    / campaign log), 0 if Firebase isn't configured — callers should still
    treat that as "the in-app Notification rows went out fine" since this
    only ever adds an OS-level push on top of those."""
    from api.models import DeviceToken
    tokens = list(DeviceToken.objects.values_list('token', flat=True).distinct())
    return _send_to_tokens(tokens, title_bn, body_bn, data)
