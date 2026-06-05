import logging
from datetime import date
from decimal import Decimal
from django.db.models import Sum
from django.db.models.functions import TruncDay, TruncWeek, TruncMonth
from django.utils import timezone
from api.models import Account, JournalEntry, JournalLine, JournalLine as JL, SalesOrder, User, Product

logger = logging.getLogger(__name__)


class AccountingService:

    def list_accounts(self):
        return Account.objects.filter(is_active=True)

    def get_account(self, pk: str) -> Account:
        return Account.objects.get(pk=pk)

    def list_journal_entries(self, params: dict):
        qs = JournalEntry.objects.prefetch_related('lines__account')
        if params.get('from'):
            qs = qs.filter(created_at__date__gte=params['from'])
        if params.get('to'):
            qs = qs.filter(created_at__date__lte=params['to'])
        if params.get('reference_type'):
            qs = qs.filter(reference_type=params['reference_type'])
        if params.get('reference_id'):
            qs = qs.filter(reference_id=params['reference_id'])
        return qs

    def get_journal_entry(self, pk: str) -> JournalEntry:
        return JournalEntry.objects.prefetch_related('lines__account').get(pk=pk)

    def get_ledger(self, account_id: str, from_date: str, to_date: str, locale: str) -> dict:
        account  = Account.objects.get(pk=account_id)
        lines_qs = JournalLine.objects.filter(account=account).select_related('journal_entry').order_by('journal_entry__created_at')

        if from_date:
            opening_lines   = lines_qs.filter(journal_entry__created_at__date__lt=from_date)
            opening_balance = (
                (opening_lines.aggregate(d=Sum('debit'))['d']  or Decimal('0')) -
                (opening_lines.aggregate(c=Sum('credit'))['c'] or Decimal('0'))
            )
            lines_qs = lines_qs.filter(journal_entry__created_at__date__gte=from_date)
        else:
            opening_balance = Decimal('0')

        if to_date:
            lines_qs = lines_qs.filter(journal_entry__created_at__date__lte=to_date)

        running   = opening_balance
        line_data = []
        for line in lines_qs:
            running += line.debit - line.credit
            desc = line.journal_entry.description_bn if locale == 'bn' else line.journal_entry.description_en
            line_data.append({
                'date':         line.journal_entry.created_at,
                'entry_number': line.journal_entry.entry_number,
                'description':  desc,
                'debit':        str(line.debit),
                'credit':       str(line.credit),
                'balance':      str(running),
            })

        return {
            'account':         account,
            'opening_balance': str(opening_balance),
            'closing_balance': str(running),
            'lines':           line_data,
        }

    def get_trial_balance(self, as_of: str) -> dict:
        lines_qs = JournalLine.objects.select_related('account')
        if as_of:
            lines_qs = lines_qs.filter(journal_entry__created_at__date__lte=as_of)

        rows: dict = {}
        for line in lines_qs:
            acct = line.account
            if acct.id not in rows:
                rows[acct.id] = {'account': acct, 'debit': Decimal('0'), 'credit': Decimal('0')}
            rows[acct.id]['debit']  += line.debit
            rows[acct.id]['credit'] += line.credit
        return {'rows': list(rows.values())}

    def get_profit_loss(self, from_date: str, to_date: str) -> dict:
        lines_qs = JournalLine.objects.select_related('account').filter(
            account__account_type__in=['REVENUE', 'EXPENSE']
        )
        if from_date:
            lines_qs = lines_qs.filter(journal_entry__created_at__date__gte=from_date)
        if to_date:
            lines_qs = lines_qs.filter(journal_entry__created_at__date__lte=to_date)

        revenue = Decimal('0')
        expense = Decimal('0')
        for line in lines_qs:
            if line.account.account_type == 'REVENUE':
                revenue += line.credit - line.debit
            else:
                expense += line.debit - line.credit
        return {'revenue': str(revenue), 'expense': str(expense), 'net_profit': str(revenue - expense)}

    def get_sales_summary(self, from_date: str, to_date: str, group_by: str) -> list:
        qs = SalesOrder.objects.filter(status='DELIVERED')
        if from_date:
            qs = qs.filter(created_at__date__gte=from_date)
        if to_date:
            qs = qs.filter(created_at__date__lte=to_date)

        trunc = {'day': TruncDay, 'week': TruncWeek, 'month': TruncMonth}.get(group_by, TruncDay)
        return list(
            qs.annotate(period=trunc('created_at'))
              .values('period')
              .annotate(total_revenue=Sum('grand_total'))
              .order_by('period')
        )

    def get_dashboard_summary(self) -> dict:
        today = timezone.now().date()

        # Build full 12-month spine for current year
        current_year = today.year
        all_months = [date(current_year, m, 1) for m in range(1, 13)]

        # Monthly revenue from delivered orders (current year)
        revenue_qs = (
            SalesOrder.objects
            .filter(status='DELIVERED', created_at__year=current_year)
            .annotate(month=TruncMonth('created_at'))
            .values('month')
            .annotate(revenue=Sum('grand_total'))
        )
        revenue_map = {row['month'].date().replace(day=1): row['revenue'] or Decimal('0') for row in revenue_qs}

        # Monthly expense from journal lines (current year)
        expense_qs = (
            JournalLine.objects
            .filter(account__account_type='EXPENSE', journal_entry__created_at__year=current_year)
            .annotate(month=TruncMonth('journal_entry__created_at'))
            .values('month')
            .annotate(expense=Sum('debit'))
        )
        expense_map = {row['month'].date().replace(day=1): row['expense'] or Decimal('0') for row in expense_qs}

        monthly_chart = [
            {
                'month':   str(m),
                'revenue': str(revenue_map.get(m, Decimal('0'))),
                'expense': str(expense_map.get(m, Decimal('0'))),
            }
            for m in all_months
        ]

        # Order status breakdown
        statuses = ['PENDING', 'CONFIRMED', 'PACKED', 'ASSIGNED', 'ON_THE_WAY', 'DELIVERED', 'RETURNED', 'CANCELLED']
        status_breakdown = [
            {'status': s, 'count': SalesOrder.objects.filter(status=s).count()}
            for s in statuses
        ]

        return {
            'today_orders':          SalesOrder.objects.filter(created_at__date=today).count(),
            'today_revenue':         str(SalesOrder.objects.filter(created_at__date=today, status='DELIVERED').aggregate(t=Sum('grand_total'))['t'] or Decimal('0')),
            'pending_orders':        SalesOrder.objects.filter(status='PENDING').count(),
            'low_stock_count':       sum(1 for p in Product.objects.filter(is_active=True) if p.stock_on_hand <= 5),
            'total_customers':       User.objects.filter(role='CUSTOMER', is_active=True).count(),
            'total_products':        Product.objects.filter(is_active=True).count(),
            'monthly_revenue_chart': monthly_chart,
            'status_breakdown':      status_breakdown,
        }
