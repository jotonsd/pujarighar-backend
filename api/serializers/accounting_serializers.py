from rest_framework import serializers
from api.models import Account, JournalEntry, JournalLine


class AccountSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Account
        fields = ['id', 'code', 'name_bn', 'name_en', 'account_type', 'parent', 'is_active']


class JournalLineSerializer(serializers.ModelSerializer):
    account_code    = serializers.CharField(source='account.code', read_only=True)
    account_name_bn = serializers.CharField(source='account.name_bn', read_only=True)
    account_name_en = serializers.CharField(source='account.name_en', read_only=True)

    class Meta:
        model  = JournalLine
        fields = ['id', 'account', 'account_code', 'account_name_bn', 'account_name_en',
                  'debit', 'credit', 'memo_bn', 'memo_en']


class JournalEntrySerializer(serializers.ModelSerializer):
    lines            = JournalLineSerializer(many=True, read_only=True)
    created_by_email = serializers.EmailField(source='created_by.email', read_only=True)
    total_debit      = serializers.SerializerMethodField()
    total_credit     = serializers.SerializerMethodField()

    class Meta:
        model  = JournalEntry
        fields = [
            'id', 'entry_number', 'reference_type', 'reference_id',
            'description_bn', 'description_en',
            'created_by', 'created_by_email', 'created_at', 'is_posted',
            'lines', 'total_debit', 'total_credit',
        ]

    def get_total_debit(self, obj):
        return str(sum(l.debit for l in obj.lines.all()))

    def get_total_credit(self, obj):
        return str(sum(l.credit for l in obj.lines.all()))
