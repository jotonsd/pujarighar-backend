from django.db.models import F
from rest_framework.exceptions import ValidationError

from api.models import PromoCode, SalesOrder


def validate_promo_code(code: str, source: str, *, user) -> PromoCode:
    """Raises ValidationError if `code` can't be used for this checkout.
    Registered customers only (checkout_service.py is the only caller that
    ever passes a promo_code; guest checkout never does). Which channel a
    code is redeemable from is per-code (see PromoCode.scope), checked
    against the request's actual source — so a WEBSITE-scoped code rejects
    an app order and vice versa. Eligibility is "first order on that same
    channel", not "first order ever": a website regular's first APP order
    still qualifies for an APP-scoped code, and symmetrically for WEBSITE."""
    promo = PromoCode.objects.filter(code__iexact=code.strip()).first()
    if not promo or not promo.is_valid_now():
        raise ValidationError({
            'message_bn': 'প্রোমো কোডটি সঠিক নয় অথবা মেয়াদ শেষ হয়ে গেছে',
            'message_en': 'This promo code is invalid or has expired',
        })

    channel_bn = 'অ্যাপ' if source == 'MOBILE_APP' else 'ওয়েবসাইট'
    channel_en = 'app' if source == 'MOBILE_APP' else 'website'

    if promo.scope != source:
        promo_channel_bn = 'অ্যাপ' if promo.scope == 'MOBILE_APP' else 'ওয়েবসাইট'
        promo_channel_en = 'app' if promo.scope == 'MOBILE_APP' else 'website'
        raise ValidationError({
            'message_bn': f'এই প্রোমো কোড শুধুমাত্র {promo_channel_bn}-এ ব্যবহার করা যাবে',
            'message_en': f'This promo code can only be used on the {promo_channel_en}',
        })

    if SalesOrder.objects.filter(source=source, customer=user).exists():
        raise ValidationError({
            'message_bn': f'এই কোড শুধুমাত্র {channel_bn} থেকে আপনার প্রথম অর্ডারে ব্যবহার করা যাবে',
            'message_en': f'This code can only be used on your first order placed through the {channel_en}',
        })

    return promo


def redeem_promo_code(code: str) -> None:
    """Call once, only when an order actually gets created off the back of
    this code (not merely at checkout-time validation, since an online
    payment can still be abandoned before an order ever exists)."""
    if not code:
        return
    PromoCode.objects.filter(code__iexact=code.strip()).update(times_used=F('times_used') + 1)
