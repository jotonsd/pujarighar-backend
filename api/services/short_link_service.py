from django.conf import settings

from api.models import ShortLink


def get_short_url(target_url: str) -> str:
    """Reuses an existing ShortLink for the same target instead of minting a
    new code every time (e.g. re-sending the same order's tracking SMS)."""
    link, _ = ShortLink.objects.get_or_create(target_url=target_url)
    return f'{settings.BACKEND_URL}/t/{link.code}'
