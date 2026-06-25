from rest_framework import serializers
from api.models import PromoEmail


class PromoEmailSerializer(serializers.ModelSerializer):
    sent_by_name = serializers.SerializerMethodField()

    class Meta:
        model  = PromoEmail
        fields = ['id', 'email_type', 'subject_bn', 'subject_en', 'message_bn', 'message_en',
                  'status', 'recipient_count', 'sent_by_name', 'sent_at', 'created_at']
        read_only_fields = ['id', 'status', 'recipient_count', 'sent_by_name', 'sent_at', 'created_at']

    def get_sent_by_name(self, obj):
        if not obj.sent_by:
            return None
        return obj.sent_by.profile.full_name_bn or obj.sent_by.profile.full_name_en or obj.sent_by.email


class PromoEmailCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model  = PromoEmail
        fields = ['email_type', 'subject_bn', 'subject_en', 'message_bn', 'message_en']
