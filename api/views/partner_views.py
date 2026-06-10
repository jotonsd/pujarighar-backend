import logging
from decimal import Decimal
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from api.models import Account, JournalEntry, JournalLine, Partner, PartnerProfitPayment
from api.serializers.partner_serializers import PartnerSerializer, PartnerProfitPaymentSerializer
from api.utils.response import ApiResponse
from api.permissions import IsAdmin

logger = logging.getLogger(__name__)


def _acct(code):
    try:
        return Account.objects.get(code=code)
    except Account.DoesNotExist:
        return None


def _next_entry_number():
    today  = timezone.now().date()
    prefix = f'JE-{today:%Y%m%d}-'
    last   = JournalEntry.objects.filter(entry_number__startswith=prefix).count()
    return f'{prefix}{last + 1:04d}'


def _create_profit_accrual_journal(payment: PartnerProfitPayment, user) -> None:
    """
    Dr Retained Earnings (3100) = share_amount   — reduces accumulated profit
    Cr Partner Profit Payable (2300) = share_amount — records obligation to partner
    """
    share = Decimal(str(payment.share_amount))
    if share <= 0:
        return
    entry = JournalEntry.objects.create(
        entry_number=_next_entry_number(), reference_type='EQUITY',
        reference_id=payment.id,
        description_bn=f'অংশীদার লাভ বরাদ্দ — {payment.partner.name_bn} ({payment.year}/{payment.month:02d})',
        description_en=f'Partner Profit Allocation — {payment.partner.name_en or payment.partner.name_bn} ({payment.year}/{payment.month:02d})',
        created_by=user, is_posted=True,
    )
    for code, debit, credit in [
        ('3100', share,          Decimal('0')),  # Dr Retained Earnings
        ('2300', Decimal('0'),   share),         # Cr Partner Profit Payable
    ]:
        acct = _acct(code)
        if acct:
            JournalLine.objects.create(journal_entry=entry, account=acct, debit=debit, credit=credit)


def _create_profit_payment_journal(payment: PartnerProfitPayment, paid_delta: Decimal, user) -> None:
    """
    Dr Partner Profit Payable (2300) = paid_delta  — settles the payable
    Cr Cash (1000) = paid_delta                    — cash goes out
    """
    if paid_delta <= 0:
        return
    entry = JournalEntry.objects.create(
        entry_number=_next_entry_number(), reference_type='EQUITY',
        reference_id=payment.id,
        description_bn=f'অংশীদার লাভ পরিশোধ — {payment.partner.name_bn} ({payment.year}/{payment.month:02d})',
        description_en=f'Partner Profit Payment — {payment.partner.name_en or payment.partner.name_bn} ({payment.year}/{payment.month:02d})',
        created_by=user, is_posted=True,
    )
    for code, debit, credit in [
        ('2300', paid_delta,     Decimal('0')),  # Dr Partner Profit Payable
        ('1000', Decimal('0'),   paid_delta),    # Cr Cash
    ]:
        acct = _acct(code)
        if acct:
            JournalLine.objects.create(journal_entry=entry, account=acct, debit=debit, credit=credit)


def _create_capital_journal(partner: Partner, user) -> None:
    """
    Dr Cash (1000) = invested_amount   — cash received from partner
    Cr Owner's Capital (3000) = invested_amount — equity recorded
    """
    amount = Decimal(str(partner.invested_amount))
    if amount <= 0:
        return
    entry = JournalEntry.objects.create(
        entry_number=_next_entry_number(), reference_type='CAPITAL',
        reference_id=partner.id,
        description_bn=f'মূলধন বিনিয়োগ — {partner.name_bn}',
        description_en=f'Capital Contribution — {partner.name_en or partner.name_bn}',
        created_by=user, is_posted=True,
    )
    for code, debit, credit in [
        ('1000', amount,         Decimal('0')),  # Dr Cash
        ('3000', Decimal('0'),   amount),        # Cr Owner's Capital
    ]:
        acct = _acct(code)
        if acct:
            JournalLine.objects.create(journal_entry=entry, account=acct, debit=debit, credit=credit)


# ─── Partners CRUD ────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def list_partners(_request):
    partners = Partner.objects.filter(is_active=True)
    return ApiResponse(message="Partners retrieved", data=PartnerSerializer(partners, many=True).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAdmin])
def create_partner(request):
    serializer = PartnerSerializer(data=request.data)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    partner = serializer.save()
    _create_capital_journal(partner, request.user)
    return ApiResponse(message="Partner created", data=PartnerSerializer(partner).data, status_code=201)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, IsAdmin])
def update_partner(request, pk):
    try:
        partner = Partner.objects.get(pk=pk)
    except Partner.DoesNotExist:
        return ApiResponse(message="Not found", errors="Not found", status_code=404)
    serializer = PartnerSerializer(partner, data=request.data, partial=True)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    serializer.save()
    return ApiResponse(message="Partner updated", data=serializer.data)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, IsAdmin])
def delete_partner(_request, pk):
    try:
        partner = Partner.objects.get(pk=pk)
        partner.is_active = False
        partner.save(update_fields=['is_active'])
        return ApiResponse(message="Partner deleted")
    except Partner.DoesNotExist:
        return ApiResponse(message="Not found", errors="Not found", status_code=404)


# ─── Profit Payments ─────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def list_partner_payments(_request, pk):
    try:
        partner = Partner.objects.get(pk=pk)
    except Partner.DoesNotExist:
        return ApiResponse(message="Not found", errors="Not found", status_code=404)
    payments = PartnerProfitPayment.objects.filter(partner=partner).order_by('-year', '-month')
    totals = payments.aggregate(total_share=Sum('share_amount'), total_paid=Sum('paid_amount'))
    data = {
        'payments': PartnerProfitPaymentSerializer(payments, many=True).data,
        'total_share': str(Decimal(str(totals['total_share'] or 0)).quantize(Decimal('0.01'))),
        'total_paid':  str(Decimal(str(totals['total_paid'] or 0)).quantize(Decimal('0.01'))),
    }
    return ApiResponse(message="Payments retrieved", data=data)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAdmin])
@transaction.atomic
def create_partner_payment(request, pk):
    try:
        partner = Partner.objects.get(pk=pk)
    except Partner.DoesNotExist:
        return ApiResponse(message="Not found", errors="Not found", status_code=404)

    data = request.data.copy()
    data['partner'] = str(partner.id)

    if 'share_amount' not in data and 'total_profit' in data:
        total_profit = Decimal(str(data['total_profit']))
        data['share_amount'] = str((total_profit * partner.equity_percentage / 100).quantize(Decimal('0.01')))

    serializer = PartnerProfitPaymentSerializer(data=data)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    payment = serializer.save()

    # Auto-generate accounting journals
    _create_profit_accrual_journal(payment, request.user)
    paid = Decimal(str(payment.paid_amount))
    if paid > 0:
        _create_profit_payment_journal(payment, paid, request.user)

    return ApiResponse(message="Payment recorded", data=PartnerProfitPaymentSerializer(payment).data, status_code=201)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, IsAdmin])
@transaction.atomic
def update_partner_payment(request, pk, payment_pk):
    try:
        payment = PartnerProfitPayment.objects.get(pk=payment_pk, partner__id=pk)
    except PartnerProfitPayment.DoesNotExist:
        return ApiResponse(message="Not found", errors="Not found", status_code=404)

    old_paid = Decimal(str(payment.paid_amount))
    serializer = PartnerProfitPaymentSerializer(payment, data=request.data, partial=True)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    payment = serializer.save()

    # Only journal the delta if paid_amount increased
    new_paid = Decimal(str(payment.paid_amount))
    delta = new_paid - old_paid
    if delta > 0:
        _create_profit_payment_journal(payment, delta, request.user)

    return ApiResponse(message="Payment updated", data=PartnerProfitPaymentSerializer(payment).data)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, IsAdmin])
def delete_partner_payment(_request, pk, payment_pk):
    try:
        payment = PartnerProfitPayment.objects.get(pk=payment_pk, partner__id=pk)
        payment.delete()
        return ApiResponse(message="Payment deleted")
    except PartnerProfitPayment.DoesNotExist:
        return ApiResponse(message="Not found", errors="Not found", status_code=404)
