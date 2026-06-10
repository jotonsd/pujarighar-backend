import logging
from datetime import date
from decimal import Decimal
from django.db.models import Count, DecimalField, ExpressionWrapper, Sum
from django.db.models.functions import TruncDay, TruncWeek, TruncMonth
from django.utils import timezone
from django.db.models import F, Q
from api.models import (
    Account, JournalEntry, JournalLine, JournalLine as JL,
    SalesOrder, SalesOrderItem, User, Product, Partner, PartnerProfitPayment,
    StockMovement, SupplierPayment, LoanInvestor, LoanPayment,
)

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

        net_profit = revenue - expense
        partners   = Partner.objects.filter(is_active=True)
        equity_shares = [
            {
                'partner_id':   str(p.id),
                'name_bn':      p.name_bn,
                'name_en':      p.name_en,
                'percentage':   str(p.equity_percentage),
                'share_amount': str((net_profit * p.equity_percentage / 100).quantize(Decimal('0.01'))),
            }
            for p in partners
        ]
        return {
            'revenue':       str(revenue),
            'expense':       str(expense),
            'net_profit':    str(net_profit),
            'equity_shares': equity_shares,
        }

    def get_sales_summary(self, from_date: str, to_date: str, group_by: str) -> dict:
        qs = SalesOrder.objects.filter(payment_status='PAID')
        if from_date:
            qs = qs.filter(created_at__date__gte=from_date)
        if to_date:
            qs = qs.filter(created_at__date__lte=to_date)

        trunc = {'day': TruncDay, 'week': TruncWeek, 'month': TruncMonth}.get(group_by, TruncDay)
        rows = list(
            qs.annotate(period=trunc('created_at'))
              .values('period')
              .annotate(total_revenue=Sum('grand_total'), order_count=Count('id'))
              .order_by('period')
        )

        total_revenue = qs.aggregate(t=Sum('grand_total'))['t'] or Decimal('0')
        total_orders  = qs.count()
        avg_order     = (total_revenue / total_orders).quantize(Decimal('0.01')) if total_orders else Decimal('0')

        return {
            'rows': rows,
            'total_revenue': str(total_revenue),
            'total_orders':  total_orders,
            'avg_order':     str(avg_order),
        }

    def get_dashboard_summary(self) -> dict:
        today = timezone.now().date()

        # Build full 12-month spine for current year
        current_year = today.year
        all_months = [date(current_year, m, 1) for m in range(1, 13)]

        # Monthly revenue from account 4000 credits (current year) — includes all paid orders
        revenue_qs = (
            JournalLine.objects
            .filter(account__code='4000', journal_entry__created_at__year=current_year)
            .annotate(month=TruncMonth('journal_entry__created_at'))
            .values('month')
            .annotate(revenue=Sum('credit'))
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

        # ── This month vs last month ──────────────────────────────────────────
        from datetime import timedelta
        month_start   = today.replace(day=1)
        if today.month == 1:
            last_month_start = date(today.year - 1, 12, 1)
            last_month_end   = date(today.year - 1, 12, 31)
        else:
            last_month_start = date(today.year, today.month - 1, 1)
            last_month_end   = month_start - timedelta(days=1)

        def _rev(date_from, date_to):
            # Use account 4000 (Revenue) journal credits — captures both delivered
            # and COD-paid orders regardless of delivery status
            return JournalLine.objects.filter(
                account__code='4000',
                journal_entry__created_at__date__gte=date_from,
                journal_entry__created_at__date__lte=date_to,
            ).aggregate(t=Sum('credit'))['t'] or Decimal('0')

        def _exp(date_from, date_to):
            return JournalLine.objects.filter(
                account__account_type='EXPENSE',
                journal_entry__created_at__date__gte=date_from,
                journal_entry__created_at__date__lte=date_to,
            ).aggregate(t=Sum('debit'))['t'] or Decimal('0')

        this_month_rev  = _rev(month_start, today)
        last_month_rev  = _rev(last_month_start, last_month_end)
        this_month_exp  = _exp(month_start, today)
        this_month_profit = this_month_rev - this_month_exp

        rev_change_pct = (
            round(float((this_month_rev - last_month_rev) / last_month_rev * 100), 1)
            if last_month_rev > 0 else None
        )

        # ── Financial obligations ─────────────────────────────────────────────
        credit_moves = StockMovement.objects.filter(payment_method='CREDIT', movement_type='PURCHASE')
        total_credit = sum(m.unit_cost * m.quantity for m in credit_moves)
        total_paid_sup = SupplierPayment.objects.aggregate(t=Sum('amount'))['t'] or Decimal('0')
        supplier_outstanding = Decimal(str(total_credit)) - Decimal(str(total_paid_sup))

        loan_outstanding = Decimal('0')
        for loan in LoanInvestor.objects.filter(is_active=True):
            paid_principal = loan.payments.filter(payment_type='PRINCIPAL').aggregate(t=Sum('amount'))['t'] or Decimal('0')
            loan_outstanding += Decimal(str(loan.principal)) - paid_principal

        partner_agg = PartnerProfitPayment.objects.aggregate(ts=Sum('share_amount'), tp=Sum('paid_amount'))
        partner_outstanding = (Decimal(str(partner_agg['ts'] or 0)) - Decimal(str(partner_agg['tp'] or 0)))

        # ── Stock alerts ──────────────────────────────────────────────────────
        active_products = list(Product.objects.filter(is_active=True))
        low_stock_count  = sum(1 for p in active_products if 0 < p.stock_on_hand <= 5)
        out_of_stock     = sum(1 for p in active_products if p.stock_on_hand <= 0)

        # ── Recent orders ─────────────────────────────────────────────────────
        recent_qs = SalesOrder.objects.order_by('-created_at')[:8]
        recent_orders = [
            {
                'id':           str(o.id),
                'order_number': o.order_number,
                'name_bn':      o.shipping_name_bn or '',
                'name_en':      o.shipping_name_en or '',
                'grand_total':  str(o.grand_total),
                'status':       o.status,
                'created_at':   o.created_at.isoformat(),
            }
            for o in recent_qs
        ]

        # ── Top 5 products by revenue (all-time delivered) ────────────────────
        top_products_qs = (
            SalesOrderItem.objects
            .filter(order__status='DELIVERED')
            .values('product__id', 'product__name_bn', 'product__name_en')
            .annotate(revenue=Sum(ExpressionWrapper(F('unit_price') * F('quantity'), output_field=DecimalField(max_digits=14, decimal_places=2))))
            .order_by('-revenue')[:5]
        )
        top_products = [
            {
                'id':       str(r['product__id']),
                'name_bn':  r['product__name_bn'],
                'name_en':  r['product__name_en'] or r['product__name_bn'],
                'revenue':  str(r['revenue'] or 0),
            }
            for r in top_products_qs
        ]

        return {
            # existing
            'today_orders':          SalesOrder.objects.filter(created_at__date=today).count(),
            'today_revenue':         str(JournalLine.objects.filter(account__code='4000', journal_entry__created_at__date=today).aggregate(t=Sum('credit'))['t'] or Decimal('0')),
            'pending_orders':        SalesOrder.objects.filter(status='PENDING').count(),
            'low_stock_count':       low_stock_count,
            'total_customers':       User.objects.filter(role='CUSTOMER', is_active=True).count(),
            'total_products':        Product.objects.filter(is_active=True).count(),
            'monthly_revenue_chart': monthly_chart,
            'status_breakdown':      status_breakdown,
            # new
            'this_month_revenue':    str(this_month_rev),
            'last_month_revenue':    str(last_month_rev),
            'this_month_profit':     str(this_month_profit),
            'revenue_change_pct':    rev_change_pct,
            'supplier_outstanding':  str(supplier_outstanding),
            'loan_outstanding':      str(loan_outstanding),
            'partner_outstanding':   str(partner_outstanding),
            'out_of_stock_count':    out_of_stock,
            'recent_orders':         recent_orders,
            'top_products':          top_products,
        }
