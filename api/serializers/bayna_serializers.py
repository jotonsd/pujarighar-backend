from rest_framework import serializers
from api.models import BaynaBooking


class BaynaBookingSerializer(serializers.ModelSerializer):
    class Meta:
        model  = BaynaBooking
        fields = [
            'id', 'service_type', 'event_date', 'name', 'phone', 'email',
            'location', 'description', 'status', 'admin_notes',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
