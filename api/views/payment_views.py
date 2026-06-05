import logging
from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from api.models import PaymentTransaction
from api.services.sslcommerz_service import SSLCommerzService

logger = logging.getLogger(__name__)
_svc   = SSLCommerzService()


@csrf_exempt
@require_POST
def payment_ipn(request):
    """
    SSLCommerz IPN (Instant Payment Notification).
    Called server-to-server by SSLCommerz after payment — used as a
    backup confirmation in case the browser redirect is interrupted.
    """
    post  = request.POST
    tran_id = post.get('tran_id', '')
    val_id  = post.get('val_id', '')
    status  = post.get('status', '')

    if status not in ('VALID', 'VALIDATED'):
        logger.info(f'IPN ignored — status={status} tran_id={tran_id}')
        return HttpResponse('IPN received')

    _svc.confirm_payment(tran_id, val_id, post)
    return HttpResponse('IPN received')


@csrf_exempt
@require_POST
def payment_success(request):
    post    = request.POST
    tran_id = post.get('tran_id', '')
    val_id  = post.get('val_id', '')
    status  = post.get('status', '')

    frontend = settings.FRONTEND_URL

    if status not in ('VALID', 'VALIDATED'):
        logger.warning(f'payment_success called with status={status} tran_id={tran_id}')
        return HttpResponseRedirect(f'{frontend}/bn/payment/fail?reason=invalid')

    order = _svc.confirm_payment(tran_id, val_id, post)
    if order is None:
        return HttpResponseRedirect(f'{frontend}/bn/payment/fail?reason=verification_failed')

    return HttpResponseRedirect(f'{frontend}/bn/payment/success?order_id={order.id}')


@csrf_exempt
@require_POST
def payment_fail(request):
    post    = request.POST
    tran_id = post.get('tran_id', '')
    logger.warning(f'Payment failed for tran_id={tran_id}')

    try:
        txn = PaymentTransaction.objects.get(tran_id=tran_id)
        if txn.status == 'INITIATED':
            txn.status = 'FAILED'
            txn.save(update_fields=['status', 'updated_at'])
        order_id = str(txn.order.id)
    except PaymentTransaction.DoesNotExist:
        order_id = ''

    frontend = settings.FRONTEND_URL
    return HttpResponseRedirect(
        f'{frontend}/bn/payment/fail?order_id={order_id}' if order_id
        else f'{frontend}/bn/payment/fail'
    )


@csrf_exempt
@require_POST
def payment_cancel(request):
    post    = request.POST
    tran_id = post.get('tran_id', '')
    logger.info(f'Payment cancelled for tran_id={tran_id}')

    try:
        txn = PaymentTransaction.objects.get(tran_id=tran_id)
        if txn.status == 'INITIATED':
            txn.status = 'CANCELLED'
            txn.save(update_fields=['status', 'updated_at'])
        order_id = str(txn.order.id)
    except PaymentTransaction.DoesNotExist:
        order_id = ''

    frontend = settings.FRONTEND_URL
    return HttpResponseRedirect(
        f'{frontend}/bn/payment/cancel?order_id={order_id}' if order_id
        else f'{frontend}/bn/payment/cancel'
    )
