import logging
import secrets

from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated

from api.models import CourierConsignment, CourierProvider, CourierReturnRequest, SalesOrder
from api.permissions import has_permission, has_any_permission
from api.serializers.courier_serializers import (
    CourierConsignmentSerializer, CourierProviderSerializer, CourierReturnRequestSerializer,
)
from api.services.courier_service import CourierService
from api.utils.crypto import decrypt_token, encrypt_token
from api.utils.pagination import paginate_queryset
from api.utils.response import ApiResponse, api_error

logger = logging.getLogger(__name__)
_svc = CourierService()


# ─── Providers ─────────────────────────────────────────────────────────────────

@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def providers(request):
    if request.method == 'GET':
        # Also readable by anyone who can view orders — the order-details
        # "assign to courier" flow needs this list to populate its provider
        # dropdown, without requiring the full 'courier' module permission.
        if not has_any_permission(('courier', 'view'), ('orders', 'view'))().has_permission(request, None):
            return ApiResponse(message='Permission denied', errors='Forbidden', status_code=403)
        qs = _svc.list_providers()
        return ApiResponse(message='Providers retrieved', data=CourierProviderSerializer(qs, many=True).data)

    if not has_permission('courier', 'view')().has_permission(request, None):
        return ApiResponse(message='Permission denied', errors='Forbidden', status_code=403)
    data = request.data
    if not data.get('code') or not data.get('name'):
        return ApiResponse(message='Code and name are required', errors='Validation error', status_code=422)
    provider = CourierProvider.objects.create(
        code=data['code'],
        name=data['name'],
        base_url=data.get('base_url', ''),
        is_active=bool(data.get('is_active', False)),
        store_id=data.get('store_id', ''),
    )
    if data.get('api_key'):
        provider.api_key_encrypted = encrypt_token(data['api_key'])
    if data.get('secret_key'):
        provider.secret_key_encrypted = encrypt_token(data['secret_key'])
    if data.get('username'):
        provider.username_encrypted = encrypt_token(data['username'])
    if data.get('password'):
        provider.password_encrypted = encrypt_token(data['password'])
    provider.webhook_secret_encrypted = encrypt_token(secrets.token_urlsafe(32))
    provider.save()
    payload = CourierProviderSerializer(provider).data
    # Webhook secret shown once, in plaintext, right after creation only —
    # never returned again from the GET/list endpoint.
    payload['webhook_secret'] = decrypt_token(provider.webhook_secret_encrypted)
    return ApiResponse(message='Provider created', data=payload, status_code=201)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, has_permission('courier', 'edit')])
def update_provider(request, pk):
    try:
        provider = CourierProvider.objects.get(pk=pk)
    except CourierProvider.DoesNotExist:
        return ApiResponse(message='Provider not found', errors='Not found', status_code=404)

    data = request.data
    for field in ('name', 'base_url', 'store_id'):
        if field in data:
            setattr(provider, field, data[field])
    if 'is_active' in data:
        provider.is_active = bool(data['is_active'])
    if data.get('api_key'):
        provider.api_key_encrypted = encrypt_token(data['api_key'])
    if data.get('secret_key'):
        provider.secret_key_encrypted = encrypt_token(data['secret_key'])
    if data.get('username'):
        provider.username_encrypted = encrypt_token(data['username'])
    if data.get('password'):
        provider.password_encrypted = encrypt_token(data['password'])
    provider.save()
    return ApiResponse(message='Provider updated', data=CourierProviderSerializer(provider).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated, has_permission('courier', 'view')])
def provider_balance(request, pk):
    try:
        provider = CourierProvider.objects.get(pk=pk)
    except CourierProvider.DoesNotExist:
        return ApiResponse(message='Provider not found', errors='Not found', status_code=404)
    try:
        return ApiResponse(message='Balance retrieved', data=_svc.get_balance(provider))
    except Exception as e:
        logger.error(f'Courier balance error: {e}', exc_info=True)
        return api_error(e)


@api_view(['GET'])
@permission_classes([IsAuthenticated, has_permission('courier', 'view')])
def provider_police_stations(request, pk):
    try:
        provider = CourierProvider.objects.get(pk=pk)
    except CourierProvider.DoesNotExist:
        return ApiResponse(message='Provider not found', errors='Not found', status_code=404)
    try:
        return ApiResponse(message='Police stations retrieved', data=_svc.list_police_stations(provider))
    except Exception as e:
        logger.error(f'Courier police stations error: {e}', exc_info=True)
        return api_error(e)


# ─── Send order / consignments ─────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated, has_any_permission(('courier', 'edit'), ('orders', 'edit'))])
def send_to_courier(request, pk):
    try:
        order = SalesOrder.objects.get(pk=pk)
    except SalesOrder.DoesNotExist:
        return ApiResponse(message='Order not found', errors='Not found', status_code=404)
    try:
        consignment = _svc.send_order(order, request.data.get('provider_id'), request.user, request.data.get('weight'), request.data.get('note'))
        return ApiResponse(message='Sent to courier', data=CourierConsignmentSerializer(consignment).data, status_code=201)
    except Exception as e:
        logger.error(f'Send to courier error: {e}', exc_info=True)
        return api_error(e)


@api_view(['GET'])
@permission_classes([IsAuthenticated, has_permission('courier', 'view')])
def courier_status(request, pk):
    try:
        order = SalesOrder.objects.get(pk=pk)
        consignment = order.courier_consignment
    except (SalesOrder.DoesNotExist, CourierConsignment.DoesNotExist):
        return ApiResponse(message='Consignment not found', errors='Not found', status_code=404)
    try:
        updated = _svc.refresh_status(consignment)
        return ApiResponse(message='Status refreshed', data=CourierConsignmentSerializer(updated).data)
    except Exception as e:
        logger.error(f'Courier status refresh error: {e}', exc_info=True)
        return api_error(e)


@api_view(['GET'])
@permission_classes([IsAuthenticated, has_permission('courier', 'view')])
def list_consignments(request):
    qs = CourierConsignment.objects.select_related('order', 'provider').prefetch_related('events').order_by('-created_at')
    status_filter = request.query_params.get('status')
    if status_filter:
        qs = qs.filter(status=status_filter)
    provider_code = request.query_params.get('provider_code')
    if provider_code:
        qs = qs.filter(provider__code=provider_code)
    page_data, pagination = paginate_queryset(qs, request)
    return ApiResponse(
        message='Consignments retrieved',
        data=CourierConsignmentSerializer(page_data, many=True).data,
        pagination=pagination,
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated, has_permission('courier', 'view')])
def get_consignment(request, pk):
    try:
        consignment = CourierConsignment.objects.select_related('order', 'provider').prefetch_related('events').get(pk=pk)
    except CourierConsignment.DoesNotExist:
        return ApiResponse(message='Consignment not found', errors='Not found', status_code=404)
    return ApiResponse(message='Consignment retrieved', data=CourierConsignmentSerializer(consignment).data)


# ─── Return requests ────────────────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated, has_permission('courier', 'create')])
def create_return_request(request):
    try:
        consignment = CourierConsignment.objects.get(pk=request.data.get('consignment_id'))
    except (CourierConsignment.DoesNotExist, ValueError, TypeError):
        return ApiResponse(message='Consignment not found', errors='Not found', status_code=404)
    try:
        rr = _svc.create_return_request(consignment, request.data.get('reason', ''), request.user)
        return ApiResponse(message='Return request created', data=CourierReturnRequestSerializer(rr).data, status_code=201)
    except Exception as e:
        logger.error(f'Create return request error: {e}', exc_info=True)
        return api_error(e)


@api_view(['GET'])
@permission_classes([IsAuthenticated, has_permission('courier', 'view')])
def get_return_request(request, pk):
    try:
        rr = CourierReturnRequest.objects.select_related('consignment__order').get(pk=pk)
    except CourierReturnRequest.DoesNotExist:
        return ApiResponse(message='Return request not found', errors='Not found', status_code=404)
    try:
        rr = _svc.refresh_return_request(rr)
        return ApiResponse(message='Return request retrieved', data=CourierReturnRequestSerializer(rr).data)
    except Exception as e:
        logger.error(f'Get return request error: {e}', exc_info=True)
        return api_error(e)


@api_view(['GET'])
@permission_classes([IsAuthenticated, has_permission('courier', 'view')])
def list_return_requests(request):
    qs = CourierReturnRequest.objects.select_related('consignment__order').order_by('-created_at')
    page_data, pagination = paginate_queryset(qs, request)
    return ApiResponse(
        message='Return requests retrieved',
        data=CourierReturnRequestSerializer(page_data, many=True).data,
        pagination=pagination,
    )


# ─── Payments (read-through to provider) ───────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated, has_permission('courier', 'view')])
def list_payments(request):
    try:
        provider = CourierProvider.objects.get(pk=request.query_params.get('provider_id'))
    except (CourierProvider.DoesNotExist, ValueError, TypeError):
        return ApiResponse(message='Provider not found', errors='Not found', status_code=404)
    try:
        return ApiResponse(message='Payments retrieved', data=_svc.list_payments(provider))
    except Exception as e:
        logger.error(f'List payments error: {e}', exc_info=True)
        return api_error(e)


@api_view(['GET'])
@permission_classes([IsAuthenticated, has_permission('courier', 'view')])
def get_payment(request, payment_id):
    try:
        provider = CourierProvider.objects.get(pk=request.query_params.get('provider_id'))
    except (CourierProvider.DoesNotExist, ValueError, TypeError):
        return ApiResponse(message='Provider not found', errors='Not found', status_code=404)
    try:
        return ApiResponse(message='Payment retrieved', data=_svc.get_payment(provider, payment_id))
    except Exception as e:
        logger.error(f'Get payment error: {e}', exc_info=True)
        return api_error(e)


# ─── Webhook (public, Bearer-token authenticated) ──────────────────────────────

@api_view(['POST'])
@authentication_classes([])  # bypass DRF's global JWTAuthentication — this endpoint's
                             # Authorization: Bearer header is Steadfast's own webhook
                             # token, not one of our JWTs, and would otherwise 401 first
@permission_classes([AllowAny])
def steadfast_webhook(request):
    logger.info(f'Courier webhook received: {request.data}')
    auth_header = request.META.get('HTTP_AUTHORIZATION', '')
    token = auth_header.replace('Bearer ', '').strip()

    provider = CourierProvider.objects.filter(code='STEADFAST').first()
    if not provider:
        logger.warning('Courier webhook rejected: no STEADFAST provider configured')
        return ApiResponse(message='Invalid or missing token', errors='Unauthorized', status_code=401)
    if not token or decrypt_token(provider.webhook_secret_encrypted) != token:
        logger.warning(f'Courier webhook rejected: token mismatch (got {token[:8] + "..." if token else "(empty)"})')
        return ApiResponse(message='Invalid or missing token', errors='Unauthorized', status_code=401)

    try:
        _svc.handle_webhook(request.data)
        return ApiResponse(message='Webhook received successfully.')
    except Exception as e:
        logger.error(f'Courier webhook error: {e}', exc_info=True)
        return ApiResponse(message='Failed to process webhook', errors=str(e), status_code=400)
