import logging
from decimal import Decimal
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from api.models import Account, JournalEntry, JournalLine, LoanInvestor, LoanPayment
from api.serializers.loan_serializers import LoanInvestorSerializer, LoanPaymentSerializer
from api.utils.response import ApiResponse
from api.permissions import IsAdmin

logger = logging.getLogger(__name__)


def _acct(code):
    try:
        return Account.objects.get(code=code)
    except Account.DoesNotExist:
        return None


def _next_je_number():
    today  = timezone.now().date()
    prefix = f'JE-{today:%Y%m%d}-'
    last   = JournalEntry.objects.filter(entry_number__startswith=prefix).count()
    return f'{prefix}{last + 1:04d}'


def _create_loan_received_journal(loan: LoanInvestor, user) -> None:
    """
    Dr Cash (1000) = principal      — cash received from lender
    Cr Loan Payable (2400) = principal — liability recorded
    """
    amount = Decimal(str(loan.principal))
    entry = JournalEntry.objects.create(
        entry_number=_next_je_number(), reference_type='LOAN_RECEIVED',
        reference_id=loan.id,
        description_bn=f'ঋণ গ্রহণ — {loan.name_bn}',
        description_en=f'Loan Received — {loan.name_en or loan.name_bn}',
        created_by=user, is_posted=True,
    )
    for code, debit, credit in [
        ('1000', amount,         Decimal('0')),  # Dr Cash
        ('2400', Decimal('0'),   amount),        # Cr Loan Payable
    ]:
        acct = _acct(code)
        if acct:
            JournalLine.objects.create(journal_entry=entry, account=acct, debit=debit, credit=credit)
        else:
            logger.warning(f'Account {code} not found — skipping line for loan journal')


def _create_loan_payment_journal(payment: LoanPayment, user) -> None:
    """
    INTEREST:  Dr Interest Expense (6900) / Cr Cash (1000)
    PRINCIPAL: Dr Loan Payable (2400)      / Cr Cash (1000)
    """
    amount = Decimal(str(payment.amount))
    ref_type = 'LOAN_INTEREST' if payment.payment_type == 'INTEREST' else 'LOAN_PRINCIPAL'

    if payment.payment_type == 'INTEREST':
        lines = [
            ('6900', amount,       Decimal('0')),  # Dr Interest Expense
            ('1000', Decimal('0'), amount),        # Cr Cash
        ]
        desc_bn = f'সুদ পরিশোধ — {payment.loan.name_bn}'
        desc_en = f'Interest Payment — {payment.loan.name_en or payment.loan.name_bn}'
    else:
        lines = [
            ('2400', amount,       Decimal('0')),  # Dr Loan Payable
            ('1000', Decimal('0'), amount),        # Cr Cash
        ]
        desc_bn = f'ঋণ আসল পরিশোধ — {payment.loan.name_bn}'
        desc_en = f'Loan Principal Repayment — {payment.loan.name_en or payment.loan.name_bn}'

    entry = JournalEntry.objects.create(
        entry_number=_next_je_number(), reference_type=ref_type,
        reference_id=payment.id,
        description_bn=desc_bn, description_en=desc_en,
        created_by=user, is_posted=True,
    )
    for code, debit, credit in lines:
        acct = _acct(code)
        if acct:
            JournalLine.objects.create(journal_entry=entry, account=acct, debit=debit, credit=credit)
        else:
            logger.warning(f'Account {code} not found — skipping line for loan payment journal')


# ─── Loan Investors CRUD ──────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def list_loan_investors(_request):
    loans = LoanInvestor.objects.filter(is_active=True)
    return ApiResponse(message="Loan investors retrieved", data=LoanInvestorSerializer(loans, many=True).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAdmin])
@transaction.atomic
def create_loan_investor(request):
    serializer = LoanInvestorSerializer(data=request.data)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    loan = serializer.save()
    _create_loan_received_journal(loan, request.user)
    return ApiResponse(message="Loan investor created", data=LoanInvestorSerializer(loan).data, status_code=201)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, IsAdmin])
def update_loan_investor(request, pk):
    try:
        loan = LoanInvestor.objects.get(pk=pk)
    except LoanInvestor.DoesNotExist:
        return ApiResponse(message="Not found", errors="Not found", status_code=404)
    serializer = LoanInvestorSerializer(loan, data=request.data, partial=True)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    serializer.save()
    return ApiResponse(message="Loan investor updated", data=serializer.data)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, IsAdmin])
def delete_loan_investor(_request, pk):
    try:
        loan = LoanInvestor.objects.get(pk=pk)
        loan.is_active = False
        loan.save(update_fields=['is_active'])
        return ApiResponse(message="Loan investor deactivated")
    except LoanInvestor.DoesNotExist:
        return ApiResponse(message="Not found", errors="Not found", status_code=404)


# ─── Loan Payments ────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def list_loan_payments(_request, pk):
    try:
        loan = LoanInvestor.objects.get(pk=pk)
    except LoanInvestor.DoesNotExist:
        return ApiResponse(message="Not found", errors="Not found", status_code=404)

    payments = loan.payments.select_related('created_by').all()
    totals   = payments.aggregate(
        total_interest  = Sum('amount', filter=Q(payment_type='INTEREST')),
        total_principal = Sum('amount', filter=Q(payment_type='PRINCIPAL')),
    )
    principal_paid = Decimal(str(totals['total_principal'] or 0))
    return ApiResponse(message="Payments retrieved", data={
        'loan':             LoanInvestorSerializer(loan).data,
        'payments':         LoanPaymentSerializer(payments, many=True).data,
        'total_interest':   str(Decimal(str(totals['total_interest'] or 0)).quantize(Decimal('0.01'))),
        'total_principal':  str(principal_paid.quantize(Decimal('0.01'))),
        'remaining_principal': str((Decimal(str(loan.principal)) - principal_paid).quantize(Decimal('0.01'))),
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAdmin])
@transaction.atomic
def create_loan_payment(request, pk):
    try:
        loan = LoanInvestor.objects.get(pk=pk)
    except LoanInvestor.DoesNotExist:
        return ApiResponse(message="Not found", errors="Not found", status_code=404)

    serializer = LoanPaymentSerializer(data={**request.data, 'loan': str(loan.id)})
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)

    payment = serializer.save(loan=loan, created_by=request.user)
    _create_loan_payment_journal(payment, request.user)
    return ApiResponse(message="Payment recorded", data=LoanPaymentSerializer(payment).data, status_code=201)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, IsAdmin])
def delete_loan_payment(_request, pk, payment_pk):
    try:
        payment = LoanPayment.objects.select_related('loan').get(pk=payment_pk, loan_id=pk)
    except LoanPayment.DoesNotExist:
        return ApiResponse(message="Not found", errors="Not found", status_code=404)

    ref_type = 'LOAN_INTEREST' if payment.payment_type == 'INTEREST' else 'LOAN_PRINCIPAL'
    JournalEntry.objects.filter(reference_type=ref_type, reference_id=payment.id).delete()
    payment.delete()
    return ApiResponse(message="Payment deleted")
