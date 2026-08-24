from django.db.models import Count, Max, Q
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from api.models import SalesOrder, SiteSetting, SmsLog
from api.permissions import has_permission
from api.services.sms_service import send_bulk_sms as _send_bulk_sms
from api.utils.dates import local_day_start, local_day_end_exclusive
from api.utils.pagination import paginate_queryset
from api.utils.response import ApiResponse

MAX_BULK_RECIPIENTS = 2000


def _serialize_settings(s: SiteSetting) -> dict:
    return {
        'has_sms_api_key': bool(s.sms_api_key),
        'sms_sender_id':   s.sms_sender_id,
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated, has_permission('sms', 'view')])
def get_sms_settings(request):
    return ApiResponse(message='SMS settings retrieved', data=_serialize_settings(SiteSetting.get()))


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, has_permission('sms', 'edit')])
def update_sms_settings(request):
    s = SiteSetting.get()
    updated = []
    if request.data.get('sms_api_key'):
        s.sms_api_key = request.data['sms_api_key']
        updated.append('sms_api_key')
    if 'sms_sender_id' in request.data:
        s.sms_sender_id = request.data['sms_sender_id']
        updated.append('sms_sender_id')
    if updated:
        s.save(update_fields=updated)
    return ApiResponse(message='SMS settings updated', data=_serialize_settings(s))


@api_view(['GET'])
@permission_classes([IsAuthenticated, has_permission('sms', 'view')])
def list_sms_logs(request):
    qs = SmsLog.objects.select_related('order').all()
    status_filter = request.query_params.get('status', '')
    if status_filter:
        qs = qs.filter(status=status_filter)
    phone = request.query_params.get('phone', '')
    if phone:
        qs = qs.filter(phone__icontains=phone)
    from_date = request.query_params.get('from', '')
    if from_date:
        qs = qs.filter(created_at__gte=local_day_start(from_date))
    to_date = request.query_params.get('to', '')
    if to_date:
        qs = qs.filter(created_at__lt=local_day_end_exclusive(to_date))

    page_data, pagination = paginate_queryset(qs, request)
    rows = [{
        'id':            str(log.id),
        'order_number':  log.order.order_number if log.order else None,
        'phone':         log.phone,
        'message':       log.message,
        'status':        log.status,
        'response_code': log.response_code,
        'response_text': log.response_text,
        'created_at':    log.created_at.isoformat(),
    } for log in page_data]
    return ApiResponse(message='SMS logs retrieved', data=rows, pagination=pagination)


@api_view(['GET'])
@permission_classes([IsAuthenticated, has_permission('sms', 'view')])
def get_sms_stats(request):
    qs = SmsLog.objects.all()
    from_date = request.query_params.get('from', '')
    if from_date:
        qs = qs.filter(created_at__gte=local_day_start(from_date))
    to_date = request.query_params.get('to', '')
    if to_date:
        qs = qs.filter(created_at__lt=local_day_end_exclusive(to_date))

    total = qs.count()
    success = qs.filter(status='SUCCESS').count()
    failed = qs.filter(status='FAILED').count()
    return ApiResponse(message='SMS stats retrieved', data={
        'total': total, 'success': success, 'failed': failed,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated, has_permission('sms', 'view')])
def list_sms_recipients(request):
    """Unique shipment customers for bulk/promotional SMS targeting — pulled
    from order history (not the User table) since most checkouts are guests
    with no account, only a shipping name/phone snapshot on the order."""
    qs = SalesOrder.objects.exclude(shipping_phone='')
    search = request.query_params.get('search', '')
    if search:
        qs = qs.filter(
            Q(shipping_phone__icontains=search)
            | Q(shipping_name_bn__icontains=search)
            | Q(shipping_name_en__icontains=search)
        )
    qs = (
        qs.values('shipping_phone')
        .annotate(
            name_bn=Max('shipping_name_bn'),
            name_en=Max('shipping_name_en'),
            order_count=Count('id'),
            last_order_at=Max('created_at'),
        )
        .order_by('-last_order_at')
    )
    page_data, pagination = paginate_queryset(qs, request, default_page_size=50)
    rows = [{
        'phone':         r['shipping_phone'],
        'name_bn':       r['name_bn'],
        'name_en':       r['name_en'],
        'order_count':   r['order_count'],
        'last_order_at': r['last_order_at'].isoformat() if r['last_order_at'] else None,
    } for r in page_data]
    return ApiResponse(message='SMS recipients retrieved', data=rows, pagination=pagination)


@api_view(['POST'])
@permission_classes([IsAuthenticated, has_permission('sms', 'create')])
def send_bulk_sms_view(request):
    phones = request.data.get('phones') or []
    message = (request.data.get('message') or '').strip()
    if not isinstance(phones, list) or not phones or not message:
        return ApiResponse(message='Phones and message are required', errors='Invalid input', status_code=400)
    if len(phones) > MAX_BULK_RECIPIENTS:
        return ApiResponse(
            message=f'Too many recipients (max {MAX_BULK_RECIPIENTS} per send)',
            errors='Too many recipients', status_code=400,
        )
    phones = [str(p) for p in phones if p]
    _send_bulk_sms(phones, message)
    return ApiResponse(message='Bulk SMS queued', data={'recipient_count': len(phones)})
