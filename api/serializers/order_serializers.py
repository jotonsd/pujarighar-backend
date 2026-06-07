from rest_framework import serializers
from api.models import SalesOrder, SalesOrderItem, OrderStatusLog, DeliveryAssignment, User


class SalesOrderItemSerializer(serializers.ModelSerializer):
    is_package    = serializers.BooleanField(source='product.is_package', read_only=True)
    package_items = serializers.SerializerMethodField()

    class Meta:
        model  = SalesOrderItem
        fields = ['id', 'product', 'product_name_bn', 'product_name_en',
                  'unit_price', 'quantity', 'line_total', 'is_package', 'package_items']

    def get_package_items(self, obj):
        if not obj.product.is_package:
            return []
        return [
            {
                'component_name_bn': pi.component.name_bn,
                'component_name_en': pi.component.name_en,
                'component_sku':     pi.component.sku,
                'quantity':          str(pi.quantity),
            }
            for pi in obj.product.package_items.select_related('component').all()
        ]


class OrderStatusLogSerializer(serializers.ModelSerializer):
    changed_by_email = serializers.EmailField(source='changed_by.email', read_only=True)
    to_status_label  = serializers.SerializerMethodField()

    class Meta:
        model  = OrderStatusLog
        fields = ['id', 'from_status', 'to_status', 'to_status_label',
                  'changed_by', 'changed_by_email', 'changed_at', 'note_bn', 'note_en']

    def get_to_status_label(self, obj):
        labels = {
            'PENDING': 'পেন্ডিং', 'CONFIRMED': 'নিশ্চিত',
            'PACKED': 'প্যাক হয়েছে', 'ASSIGNED': 'ডেলিভারিম্যান নির্ধারিত',
            'ON_THE_WAY': 'পথে আছে', 'DELIVERED': 'ডেলিভারি হয়েছে', 'CANCELLED': 'বাতিল',
        }
        return labels.get(obj.to_status, obj.to_status)


class DeliveryAssignmentSerializer(serializers.ModelSerializer):
    delivery_person_email   = serializers.EmailField(source='delivery_person.email', read_only=True)
    delivery_person_phone   = serializers.CharField(source='delivery_person.phone', read_only=True)
    delivery_person_name    = serializers.SerializerMethodField()
    delivery_person_name_bn = serializers.SerializerMethodField()
    delivery_person_name_en = serializers.SerializerMethodField()

    class Meta:
        model  = DeliveryAssignment
        fields = ['id', 'delivery_person', 'delivery_person_email', 'delivery_person_phone',
                  'delivery_person_name', 'delivery_person_name_bn', 'delivery_person_name_en',
                  'assigned_at', 'picked_up_at', 'delivered_at', 'tracking_note']

    def get_delivery_person_name(self, obj):
        p = getattr(obj.delivery_person, 'profile', None)
        return (p.full_name_bn or p.full_name_en) if p else ''

    def get_delivery_person_name_bn(self, obj):
        p = getattr(obj.delivery_person, 'profile', None)
        return p.full_name_bn if p else ''

    def get_delivery_person_name_en(self, obj):
        p = getattr(obj.delivery_person, 'profile', None)
        return p.full_name_en if p else ''


class SalesOrderSerializer(serializers.ModelSerializer):
    items          = SalesOrderItemSerializer(many=True, read_only=True)
    delivery       = DeliveryAssignmentSerializer(read_only=True)
    customer_email = serializers.EmailField(source='customer.email', read_only=True)
    status_label   = serializers.SerializerMethodField()

    class Meta:
        model  = SalesOrder
        fields = [
            'id', 'order_number', 'customer', 'customer_email', 'status', 'status_label',
            'payment_method', 'payment_status',
            'shipping_name_bn', 'shipping_name_en', 'shipping_phone',
            'shipping_address_bn', 'shipping_address_en',
            'shipping_district', 'shipping_thana', 'shipping_post_code',
            'subtotal', 'discount_amount', 'tax_amount', 'delivery_charge', 'grand_total',
            'notes_bn', 'notes_en',
            'items', 'delivery',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'order_number', 'created_at', 'updated_at']

    def get_status_label(self, obj):
        return dict(SalesOrder._meta.get_field('status').choices).get(obj.status, obj.status)


STATUS_LABELS_BN = {
    'PENDING':'পেন্ডিং', 'CONFIRMED':'নিশ্চিত', 'PACKED':'প্যাক হয়েছে',
    'ASSIGNED':'ডেলিভারিম্যান নির্ধারিত', 'ON_THE_WAY':'পথে আছে',
    'DELIVERED':'ডেলিভারি হয়েছে', 'RETURNED':'ফেরত', 'CANCELLED':'বাতিল',
}
STATUS_LABELS_EN = {
    'PENDING':'Pending', 'CONFIRMED':'Confirmed', 'PACKED':'Packed',
    'ASSIGNED':'Assigned', 'ON_THE_WAY':'On the Way',
    'DELIVERED':'Delivered', 'RETURNED':'Returned', 'CANCELLED':'Cancelled',
}


class OrderTrackingSerializer(serializers.ModelSerializer):
    status_label_bn         = serializers.SerializerMethodField()
    status_label_en         = serializers.SerializerMethodField()
    timeline                = serializers.SerializerMethodField()
    payment_method_label_bn = serializers.SerializerMethodField()
    payment_method_label_en = serializers.SerializerMethodField()
    delivery_info           = serializers.SerializerMethodField()

    class Meta:
        model  = SalesOrder
        fields = [
            'order_number', 'status', 'status_label_bn', 'status_label_en',
            'payment_method', 'payment_status',
            'payment_method_label_bn', 'payment_method_label_en',
            'shipping_name_bn', 'shipping_name_en', 'shipping_phone',
            'shipping_address_bn', 'shipping_district', 'shipping_thana',
            'grand_total', 'created_at',
            'delivery_info',
            'timeline',
        ]

    def get_status_label_bn(self, obj):
        return STATUS_LABELS_BN.get(obj.status, obj.status)

    def get_status_label_en(self, obj):
        return STATUS_LABELS_EN.get(obj.status, obj.status)

    def get_payment_method_label_bn(self, obj):
        return 'ক্যাশ অন ডেলিভারি' if obj.payment_method == 'COD' else 'অনলাইন পেমেন্ট'

    def get_payment_method_label_en(self, obj):
        return 'Cash on Delivery' if obj.payment_method == 'COD' else 'Online Payment'

    def get_delivery_info(self, obj):
        d = getattr(obj, 'delivery', None)
        if not d:
            return None
        p = getattr(d.delivery_person, 'profile', None)
        return {
            'name_bn':      p.full_name_bn if p else '',
            'name_en':      p.full_name_en if p else '',
            'phone':        d.delivery_person.phone,
            'assigned_at':  d.assigned_at.isoformat() if d.assigned_at else None,
            'picked_up_at': d.picked_up_at.isoformat() if d.picked_up_at else None,
            'delivered_at': d.delivered_at.isoformat() if d.delivered_at else None,
        }

    def get_timeline(self, obj):
        return OrderStatusLogSerializer(obj.status_logs.all(), many=True).data


class AssignDeliverySerializer(serializers.Serializer):
    delivery_person_id = serializers.UUIDField()

    def validate_delivery_person_id(self, value):
        if not User.objects.filter(id=value, role='DELIVERY', is_active=True).exists():
            raise serializers.ValidationError({
                'message_bn': 'ডেলিভারিম্যান পাওয়া যায়নি',
                'message_en': 'Delivery person not found',
            })
        return value


class OrderCancelSerializer(serializers.Serializer):
    note_bn = serializers.CharField(required=False, allow_blank=True, default='')
    note_en = serializers.CharField(required=False, allow_blank=True, default='')
