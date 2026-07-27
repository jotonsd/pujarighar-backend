from rest_framework import serializers
from api.models import CourierConsignment, CourierReturnRequest, CourierTrackingEvent


class CourierProviderSerializer(serializers.Serializer):
    """Manual (non-ModelSerializer) so the encrypted key/secret/webhook-secret
    fields never leak — only booleans indicating whether each is set, same
    masking convention used for SiteSetting's email_host_password."""
    id = serializers.IntegerField(read_only=True)
    code = serializers.CharField()
    name = serializers.CharField()
    base_url = serializers.CharField()
    is_active = serializers.BooleanField()
    has_api_key = serializers.SerializerMethodField()
    has_secret_key = serializers.SerializerMethodField()
    has_webhook_secret = serializers.SerializerMethodField()

    def get_has_api_key(self, obj):
        return bool(obj.api_key_encrypted)

    def get_has_secret_key(self, obj):
        return bool(obj.secret_key_encrypted)

    def get_has_webhook_secret(self, obj):
        return bool(obj.webhook_secret_encrypted)


class CourierTrackingEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = CourierTrackingEvent
        fields = ['id', 'status', 'message', 'source', 'created_at']


class CourierConsignmentSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(source='order.order_number', read_only=True)
    order_id = serializers.UUIDField(source='order.id', read_only=True)
    provider_code = serializers.CharField(source='provider.code', read_only=True)
    provider_name = serializers.CharField(source='provider.name', read_only=True)
    events = CourierTrackingEventSerializer(many=True, read_only=True)

    class Meta:
        model = CourierConsignment
        fields = [
            'id', 'order_id', 'order_number', 'provider', 'provider_code', 'provider_name',
            'consignment_id', 'tracking_code', 'status', 'cod_amount', 'delivery_charge',
            'tracking_message', 'created_at', 'updated_at', 'events',
        ]


class CourierReturnRequestSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(source='consignment.order.order_number', read_only=True)
    tracking_code = serializers.CharField(source='consignment.tracking_code', read_only=True)

    class Meta:
        model = CourierReturnRequest
        fields = [
            'id', 'consignment', 'order_number', 'tracking_code',
            'provider_request_id', 'reason', 'status', 'created_at', 'updated_at',
        ]
