from decimal import Decimal, InvalidOperation
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny

from api.models import SiteSetting
from api.permissions import has_permission
from api.utils.response import ApiResponse

PAGE_SIZE_CHOICES  = ['A4', 'A5', 'LETTER', 'THERMAL']
TEXT_FIELDS        = ['invoice_page_size', 'company_name_bn', 'company_name_en',
                      'contact_phone', 'contact_email', 'address_bn', 'address_en',
                      'email_host', 'email_host_user', 'email_host_password', 'email_default_from',
                      'telegram_bot_token', 'telegram_chat_id',
                      'gemini_api_key', 'gemini_model']
INT_FIELDS         = ['email_port']
BOOL_FIELDS        = ['email_use_tls', 'ai_ordering_enabled']
FILE_FIELDS        = ['logo', 'favicon']
DECIMAL_FIELDS     = ['referral_bonus_amount', 'first_order_discount_percent']


def _serialize(s: SiteSetting, request=None) -> dict:
    def img_url(field):
        f = getattr(s, field)
        if not f:
            return None
        return request.build_absolute_uri(f.url) if request else f.url

    data = {
        'invoice_page_size':   s.invoice_page_size,
        'company_name_bn':     s.company_name_bn,
        'company_name_en':     s.company_name_en,
        'contact_phone':       s.contact_phone,
        'contact_email':       s.contact_email,
        'address_bn':          s.address_bn,
        'address_en':          s.address_en,
        'logo':                img_url('logo'),
        'favicon':             img_url('favicon'),
    }

    user = getattr(request, 'user', None) if request else None
    if user and user.is_authenticated and getattr(user.role, 'code', None) == 'ADMIN':
        data.update({
            'email_host':               s.email_host,
            'email_port':               s.email_port,
            'email_host_user':          s.email_host_user,
            'has_email_host_password':  bool(s.email_host_password),
            'email_use_tls':            s.email_use_tls,
            'email_default_from':       s.email_default_from,
            'referral_bonus_amount':    str(s.referral_bonus_amount),
            'first_order_discount_percent': str(s.first_order_discount_percent),
            'has_telegram_bot_token':   bool(s.telegram_bot_token),
            'telegram_chat_id':         s.telegram_chat_id,
            'has_gemini_api_key':       bool(s.gemini_api_key),
            'gemini_model':             s.gemini_model,
            'ai_ordering_enabled':      s.ai_ordering_enabled,
        })

    return data


@api_view(['GET'])
@permission_classes([AllowAny])
def get_site_settings(request):
    return ApiResponse(message='Settings retrieved', data=_serialize(SiteSetting.get(), request))


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, has_permission('site_settings', 'edit')])
def update_site_settings(request):
    s = SiteSetting.get()
    updated = []
    for field in TEXT_FIELDS:
        if field in request.data:
            if field == 'invoice_page_size' and request.data[field] not in PAGE_SIZE_CHOICES:
                continue
            if field in ('email_host_password', 'telegram_bot_token', 'gemini_api_key') and not request.data[field]:
                continue  # blank means "keep the currently stored secret"
            setattr(s, field, request.data[field])
            updated.append(field)
    for field in INT_FIELDS:
        if field in request.data:
            try:
                setattr(s, field, int(request.data[field]))
                updated.append(field)
            except (ValueError, TypeError):
                pass
    for field in BOOL_FIELDS:
        if field in request.data:
            setattr(s, field, str(request.data[field]).lower() in ('true', '1', 'yes'))
            updated.append(field)
    for field in FILE_FIELDS:
        if field in request.FILES:
            setattr(s, field, request.FILES[field])
            updated.append(field)
    for field in DECIMAL_FIELDS:
        if field in request.data:
            try:
                value = Decimal(str(request.data[field]))
                if field == 'first_order_discount_percent' and value > 100:
                    continue
                if value >= 0:
                    setattr(s, field, value)
                    updated.append(field)
            except (InvalidOperation, TypeError, ValueError):
                pass
    if updated:
        s.save(update_fields=updated)
    return ApiResponse(message='Settings updated', data=_serialize(s, request))
