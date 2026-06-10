from decimal import Decimal
from django.db.models import Q, Sum
from rest_framework import serializers
from api.models import LoanInvestor, LoanPayment


class LoanInvestorSerializer(serializers.ModelSerializer):
    total_interest_paid  = serializers.SerializerMethodField()
    total_principal_paid = serializers.SerializerMethodField()
    remaining_principal  = serializers.SerializerMethodField()

    class Meta:
        model  = LoanInvestor
        fields = [
            'id', 'name_bn', 'name_en', 'phone',
            'principal', 'interest_rate', 'loan_date', 'due_date',
            'is_active', 'note', 'created_at',
            'total_interest_paid', 'total_principal_paid', 'remaining_principal',
        ]
        read_only_fields = ['id', 'created_at', 'total_interest_paid', 'total_principal_paid', 'remaining_principal']

    def _totals(self, obj):
        if not hasattr(obj, '_loan_cache'):
            agg = obj.payments.aggregate(
                ti=Sum('amount', filter=Q(payment_type='INTEREST')),
                tp=Sum('amount', filter=Q(payment_type='PRINCIPAL')),
            )
            obj._loan_cache = (
                Decimal(str(agg['ti'] or 0)),
                Decimal(str(agg['tp'] or 0)),
            )
        return obj._loan_cache

    def get_total_interest_paid(self, obj):
        ti, _ = self._totals(obj)
        return str(ti.quantize(Decimal('0.01')))

    def get_total_principal_paid(self, obj):
        _, tp = self._totals(obj)
        return str(tp.quantize(Decimal('0.01')))

    def get_remaining_principal(self, obj):
        _, tp = self._totals(obj)
        return str((Decimal(str(obj.principal)) - tp).quantize(Decimal('0.01')))


class LoanPaymentSerializer(serializers.ModelSerializer):
    loan_name = serializers.SerializerMethodField()

    class Meta:
        model  = LoanPayment
        fields = [
            'id', 'loan', 'loan_name',
            'payment_type', 'amount', 'paid_date', 'note', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']

    def get_loan_name(self, obj):
        return obj.loan.name_bn
