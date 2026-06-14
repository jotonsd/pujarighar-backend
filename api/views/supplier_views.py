import logging
from decimal import Decimal
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from api.models import Account, JournalEntry, JournalLine, Supplier, SupplierPayment
from api.serializers.product_serializers import SupplierPaymentSerializer, SupplierSerializer
from api.utils.response import ApiResponse
from api.permissions import IsAdmin, IsAdminOrWarehouse

logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdminOrWarehouse])
def list_suppliers(request):
    include_inactive = request.query_params.get('include_inactive') == 'true'
    qs = Supplier.objects.all() if include_inactive else Supplier.objects.filter(is_active=True)
    return ApiResponse(message="Suppliers retrieved", data=SupplierSerializer(qs, many=True).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAdmin])
def create_supplier(request):
    serializer = SupplierSerializer(data=request.data)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    supplier = serializer.save()
    return ApiResponse(message="Supplier created", data=SupplierSerializer(supplier).data, status_code=201)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def get_supplier(_request, pk):
    try:
        supplier = Supplier.objects.get(pk=pk)
        return ApiResponse(message="Supplier retrieved", data=SupplierSerializer(supplier).data)
    except Supplier.DoesNotExist:
        return ApiResponse(message="Not found", errors="Not found", status_code=404)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, IsAdmin])
def update_supplier(request, pk):
    try:
        supplier = Supplier.objects.get(pk=pk)
    except Supplier.DoesNotExist:
        return ApiResponse(message="Not found", errors="Not found", status_code=404)
    serializer = SupplierSerializer(supplier, data=request.data, partial=True)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    serializer.save()
    return ApiResponse(message="Supplier updated", data=serializer.data)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, IsAdmin])
def delete_supplier(_request, pk):
    try:
        supplier = Supplier.objects.get(pk=pk)
        supplier.is_active = False
        supplier.save(update_fields=['is_active'])
        return ApiResponse(message="Supplier deleted")
    except Supplier.DoesNotExist:
        return ApiResponse(message="Not found", errors="Not found", status_code=404)


# ─── Supplier Payments ────────────────────────────────────────────────────────

def _next_je_number():
    today  = timezone.now().date()
    prefix = f'JE-{today:%Y%m%d}-'
    last   = JournalEntry.objects.filter(entry_number__startswith=prefix).count()
    return f'{prefix}{last + 1:04d}'


def _create_supplier_payment_journal(payment: SupplierPayment, user) -> None:
    """Dr Accounts Payable (2000) / Cr Cash (1000) — reduces what we owe the supplier."""
    total = payment.amount
    entry = JournalEntry.objects.create(
        entry_number=_next_je_number(),
        reference_type='SUPPLIER_PAYMENT',
        reference_id=payment.id,
        description_bn=f'সরবরাহকারী পেমেন্ট — {payment.supplier.name_bn}',
        description_en=f'Supplier Payment — {payment.supplier.name_bn}',
        created_by=user,
        is_posted=True,
    )
    for code, debit, credit in [
        ('2000', total,            Decimal('0')),  # Dr Accounts Payable
        ('1000', Decimal('0'),     total),         # Cr Cash
    ]:
        try:
            acct = Account.objects.get(code=code)
            JournalLine.objects.create(journal_entry=entry, account=acct, debit=debit, credit=credit)
        except Account.DoesNotExist:
            logger.warning(f'Account {code} not found — skipping journal line')


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def list_supplier_payments(request, pk):
    try:
        supplier = Supplier.objects.get(pk=pk)
    except Supplier.DoesNotExist:
        return ApiResponse(message="Not found", errors="Not found", status_code=404)
    payments = supplier.payments.select_related('created_by').all()
    return ApiResponse(
        message="Payments retrieved",
        data={
            'payments': SupplierPaymentSerializer(payments, many=True).data,
            'supplier': SupplierSerializer(supplier).data,
        },
    )


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAdmin])
def create_supplier_payment(request, pk):
    try:
        supplier = Supplier.objects.get(pk=pk)
    except Supplier.DoesNotExist:
        return ApiResponse(message="Not found", errors="Not found", status_code=404)

    serializer = SupplierPaymentSerializer(data={**request.data, 'supplier': str(supplier.id)})
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)

    payment = serializer.save(supplier=supplier, created_by=request.user)
    _create_supplier_payment_journal(payment, request.user)

    return ApiResponse(
        message="Payment recorded",
        data=SupplierPaymentSerializer(payment).data,
        status_code=201,
    )


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, IsAdmin])
def delete_supplier_payment(request, pk, payment_pk):
    try:
        payment = SupplierPayment.objects.select_related('supplier').get(pk=payment_pk, supplier_id=pk)
    except SupplierPayment.DoesNotExist:
        return ApiResponse(message="Not found", errors="Not found", status_code=404)

    # Reverse the journal entry
    JournalEntry.objects.filter(reference_type='SUPPLIER_PAYMENT', reference_id=payment.id).delete()
    payment.delete()
    return ApiResponse(message="Payment deleted")
