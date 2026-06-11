from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny

from api.models import SiteSetting
from api.permissions import IsAdmin
from api.utils.response import ApiResponse

PAGE_SIZE_CHOICES  = ['A4', 'A5', 'LETTER', 'THERMAL']
TEXT_FIELDS        = ['invoice_page_size', 'company_name_bn', 'company_name_en',
                      'contact_phone', 'contact_email', 'address_bn', 'address_en']
FILE_FIELDS        = ['logo', 'favicon']


def _serialize(s: SiteSetting, request=None) -> dict:
    def img_url(field):
        f = getattr(s, field)
        if not f:
            return None
        return request.build_absolute_uri(f.url) if request else f.url

    return {
        'invoice_page_size': s.invoice_page_size,
        'company_name_bn':   s.company_name_bn,
        'company_name_en':   s.company_name_en,
        'contact_phone':     s.contact_phone,
        'contact_email':     s.contact_email,
        'address_bn':        s.address_bn,
        'address_en':        s.address_en,
        'logo':              img_url('logo'),
        'favicon':           img_url('favicon'),
    }


@api_view(['GET'])
@permission_classes([AllowAny])
def get_site_settings(request):
    return ApiResponse(message='Settings retrieved', data=_serialize(SiteSetting.get(), request))


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, IsAdmin])
def update_site_settings(request):
    s = SiteSetting.get()
    updated = []
    for field in TEXT_FIELDS:
        if field in request.data:
            if field == 'invoice_page_size' and request.data[field] not in PAGE_SIZE_CHOICES:
                continue
            setattr(s, field, request.data[field])
            updated.append(field)
    for field in FILE_FIELDS:
        if field in request.FILES:
            setattr(s, field, request.FILES[field])
            updated.append(field)
    if updated:
        s.save(update_fields=updated)
    return ApiResponse(message='Settings updated', data=_serialize(s, request))
