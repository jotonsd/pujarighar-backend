import logging
from decimal import Decimal, InvalidOperation
from django.db import transaction
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from api.models import Account, JournalEntry, JournalLine
from api.serializers.accounting_serializers import AccountSerializer, JournalEntrySerializer
from api.services.accounting_service import AccountingService
from api.utils.response import ApiResponse
from api.utils.pagination import paginate_queryset
from api.permissions import IsAdmin

logger = logging.getLogger(__name__)
_svc = AccountingService()


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def list_accounts(_request):
    accounts = _svc.list_accounts()
    return ApiResponse(message="Accounts retrieved", data=AccountSerializer(accounts, many=True).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def get_account(_request, pk):
    try:
        return ApiResponse(message="Account retrieved", data=AccountSerializer(_svc.get_account(pk)).data)
    except Account.DoesNotExist:
        return ApiResponse(message="Account not found", errors="Not found", status_code=404)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def list_journal_entries(request):
    try:
        qs = _svc.list_journal_entries(request.query_params)
        page_data, pagination = paginate_queryset(qs, request)
        return ApiResponse(
            message="Journal entries retrieved",
            data=JournalEntrySerializer(page_data, many=True).data,
            pagination=pagination,
        )
    except Exception as e:
        logger.error(f"List journal entries error: {e}", exc_info=True)
        return ApiResponse(message=str(e), errors=str(e), status_code=500)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def get_journal_entry(_request, pk):
    try:
        return ApiResponse(message="Journal entry retrieved", data=JournalEntrySerializer(_svc.get_journal_entry(pk)).data)
    except JournalEntry.DoesNotExist:
        return ApiResponse(message="Journal entry not found", errors="Not found", status_code=404)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def get_ledger(request, account_id):
    try:
        locale = request.META.get('HTTP_ACCEPT_LANGUAGE', 'bn')[:2]
        data   = _svc.get_ledger(
            account_id,
            from_date=request.query_params.get('from', ''),
            to_date=request.query_params.get('to', ''),
            locale=locale,
        )
        return ApiResponse(message="Ledger retrieved", data={
            **data,
            'account': AccountSerializer(data['account']).data,
        })
    except Account.DoesNotExist:
        return ApiResponse(message="Account not found", errors="Not found", status_code=404)
    except Exception as e:
        logger.error(f"Ledger error: {e}", exc_info=True)
        return ApiResponse(message=str(e), errors=str(e), status_code=500)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def get_trial_balance(request):
    try:
        data = _svc.get_trial_balance(request.query_params.get('as_of', ''))
        rows = [{'account': AccountSerializer(r['account']).data, 'debit': str(r['debit']), 'credit': str(r['credit'])} for r in data['rows']]
        return ApiResponse(message="Trial balance retrieved", data={'rows': rows})
    except Exception as e:
        return ApiResponse(message=str(e), errors=str(e), status_code=500)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def get_profit_loss(request):
    try:
        data = _svc.get_profit_loss(request.query_params.get('from', ''), request.query_params.get('to', ''))
        return ApiResponse(message="Profit & loss retrieved", data=data)
    except Exception as e:
        return ApiResponse(message=str(e), errors=str(e), status_code=500)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def get_income_report(request):
    data = _svc.get_income_report(
        account_id=request.query_params.get('account_id', ''),
        from_date=request.query_params.get('from', ''),
        to_date=request.query_params.get('to', ''),
    )
    return ApiResponse(message="Income report retrieved", data=data)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def get_expense_report(request):
    data = _svc.get_expense_report(
        account_id=request.query_params.get('account_id', ''),
        from_date=request.query_params.get('from', ''),
        to_date=request.query_params.get('to', ''),
    )
    return ApiResponse(message="Expense report retrieved", data=data)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def get_sales_summary(request):
    try:
        data = _svc.get_sales_summary(
            request.query_params.get('from', ''),
            request.query_params.get('to', ''),
            request.query_params.get('group_by', 'day'),
        )
        return ApiResponse(message="Sales summary retrieved", data=data)
    except Exception as e:
        return ApiResponse(message=str(e), errors=str(e), status_code=500)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def get_dashboard_summary(_request):
    try:
        return ApiResponse(message="Dashboard summary retrieved", data=_svc.get_dashboard_summary())
    except Exception as e:
        logger.error(f"Dashboard error: {e}", exc_info=True)
        return ApiResponse(message=str(e), errors=str(e), status_code=500)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAdmin])
@transaction.atomic
def create_manual_journal(request):
    """
    Manual journal entry for expenses, equity contributions, bank transfers, etc.
    Body: { description_bn, description_en, reference_type, lines: [{account_code, debit, credit, memo_bn}] }
    """
    data = request.data
    lines_data = data.get('lines', [])
    if len(lines_data) < 2:
        return ApiResponse(message="At least 2 lines required", errors="Validation error", status_code=422)

    # Validate debits == credits
    try:
        total_debit  = sum(Decimal(str(l.get('debit',  0))) for l in lines_data)
        total_credit = sum(Decimal(str(l.get('credit', 0))) for l in lines_data)
    except InvalidOperation:
        return ApiResponse(message="Invalid amount", errors="Validation error", status_code=422)

    if abs(total_debit - total_credit) > Decimal('0.01'):
        return ApiResponse(
            message=f"Journal not balanced — debits {total_debit} ≠ credits {total_credit}",
            errors="Imbalance", status_code=422,
        )

    today  = timezone.now().date()
    prefix = f'JE-{today:%Y%m%d}-'
    last   = JournalEntry.objects.filter(entry_number__startswith=prefix).count()
    entry_number = f'{prefix}{last + 1:04d}'

    ref_type = data.get('reference_type', 'EXPENSE')
    entry = JournalEntry.objects.create(
        entry_number=entry_number,
        reference_type=ref_type,
        description_bn=data.get('description_bn', ''),
        description_en=data.get('description_en', ''),
        created_by=request.user,
        is_posted=True,
    )

    for line in lines_data:
        try:
            acct = Account.objects.get(code=line['account_code'])
        except Account.DoesNotExist:
            raise ValueError(f"Account {line['account_code']} not found")
        JournalLine.objects.create(
            journal_entry=entry,
            account=acct,
            debit=Decimal(str(line.get('debit', 0))),
            credit=Decimal(str(line.get('credit', 0))),
            memo_bn=line.get('memo_bn', ''),
            memo_en=line.get('memo_en', ''),
        )

    return ApiResponse(
        message="Journal entry created",
        data=JournalEntrySerializer(entry).data,
        status_code=201,
    )
