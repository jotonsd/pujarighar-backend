from decimal import Decimal
from django.db.models import Sum
from rest_framework import serializers
from api.models import Partner, PartnerProfitPayment


class PartnerSerializer(serializers.ModelSerializer):
    total_share   = serializers.SerializerMethodField()
    total_paid    = serializers.SerializerMethodField()
    total_balance = serializers.SerializerMethodField()

    class Meta:
        model  = Partner
        fields = [
            'id', 'name_bn', 'name_en', 'equity_percentage', 'invested_amount',
            'is_active', 'created_at',
            'total_share', 'total_paid', 'total_balance',
        ]
        read_only_fields = ['id', 'created_at', 'total_share', 'total_paid', 'total_balance']

    def _payment_totals(self, obj):
        if not hasattr(obj, '_ppt_cache'):
            agg = obj.profit_payments.aggregate(ts=Sum('share_amount'), tp=Sum('paid_amount'))
            obj._ppt_cache = (
                Decimal(str(agg['ts'] or 0)),
                Decimal(str(agg['tp'] or 0)),
            )
        return obj._ppt_cache

    def get_total_share(self, obj):
        s, _ = self._payment_totals(obj)
        return str(s.quantize(Decimal('0.01')))

    def get_total_paid(self, obj):
        _, p = self._payment_totals(obj)
        return str(p.quantize(Decimal('0.01')))

    def get_total_balance(self, obj):
        s, p = self._payment_totals(obj)
        return str((s - p).quantize(Decimal('0.01')))


class PartnerProfitPaymentSerializer(serializers.ModelSerializer):
    partner_name = serializers.SerializerMethodField()
    balance      = serializers.SerializerMethodField()

    class Meta:
        model  = PartnerProfitPayment
        fields = [
            'id', 'partner', 'partner_name',
            'year', 'month',
            'total_profit', 'share_amount', 'paid_amount', 'balance',
            'paid_date', 'note', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']

    def get_partner_name(self, obj):
        return obj.partner.name_bn

    def get_balance(self, obj):
        return str((Decimal(str(obj.share_amount)) - Decimal(str(obj.paid_amount))).quantize(Decimal('0.01')))
