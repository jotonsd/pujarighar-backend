from decimal import Decimal
from django.conf import settings
from rest_framework import serializers
from api.models import SalesOrder, SalesOrderItem, OrderStatusLog, DeliveryAssignment, User, Product


class SalesOrderItemSerializer(serializers.ModelSerializer):
    is_package    = serializers.BooleanField(source='product.is_package', read_only=True)
    package_items = serializers.SerializerMethodField()
    product_image = serializers.SerializerMethodField()

    class Meta:
        model  = SalesOrderItem
        fields = ['id', 'product', 'product_name_bn', 'product_name_en', 'product_image',
                  'original_unit_price', 'unit_price', 'quantity', 'line_total',
                  'is_package', 'package_items']

    def get_product_image(self, obj):
        img = obj.product.images.first()
        if not img:
            return None
        request = self.context.get('request')
        url = img.image.url
        return request.build_absolute_uri(url) if request else url

    def get_package_items(self, obj):
        if not obj.product.is_package:
            return []
        # Plain .all() (not .select_related(...).all()) so this actually
        # reads from the prefetch_related('items__product__package_items__component')
        # cache instead of silently re-querying — calling select_related() or
        # any other filter on a prefetched related manager bypasses the
        # prefetch cache and always hits the DB fresh.
        return [
            {
                'component_name_bn': pi.component.name_bn,
                'component_name_en': pi.component.name_en,
                'component_sku':     pi.component.sku,
                'quantity':          str(pi.quantity),
            }
            for pi in obj.product.package_items.all()
        ]


def _courier_status_label(order, is_bn: bool) -> str | None:
    """An order sent to a courier is fulfilled by the provider, not an
    internal delivery person — order.status still just says 'ASSIGNED' (the
    courier flow reuses that same transition), so the generic "delivery
    person assigned" label would be misleading/wrong here. Swap in the
    courier's name whenever a consignment exists for this order."""
    consignment = getattr(order, 'courier_consignment', None)
    if not consignment:
        return None
    name = consignment.provider.name
    return f'{name}-এ পাঠানো হয়েছে' if is_bn else f'Sent to {name}'


class OrderStatusLogSerializer(serializers.ModelSerializer):
    changed_by_email  = serializers.EmailField(source='changed_by.email', read_only=True)
    to_status_label   = serializers.SerializerMethodField()
    to_status_label_en = serializers.SerializerMethodField()

    class Meta:
        model  = OrderStatusLog
        fields = ['id', 'from_status', 'to_status', 'to_status_label', 'to_status_label_en',
                  'changed_by', 'changed_by_email', 'changed_at', 'note_bn', 'note_en']

    def get_to_status_label(self, obj):
        labels = {
            'PENDING': 'পেন্ডিং', 'CONFIRMED': 'নিশ্চিত',
            'PACKED': 'প্যাক হয়েছে', 'ASSIGNED': 'ডেলিভারিম্যান নির্ধারিত',
            'ON_THE_WAY': 'পথে আছে', 'DELIVERED': 'ডেলিভারি হয়েছে', 'CANCELLED': 'বাতিল',
        }
        if obj.to_status == 'ASSIGNED':
            courier_label = _courier_status_label(obj.order, is_bn=True)
            if courier_label:
                return courier_label
        return labels.get(obj.to_status, obj.to_status)

    def get_to_status_label_en(self, obj):
        labels = {
            'PENDING': 'Pending', 'CONFIRMED': 'Confirmed',
            'PACKED': 'Packed', 'ASSIGNED': 'Assigned',
            'ON_THE_WAY': 'On the Way', 'DELIVERED': 'Delivered', 'CANCELLED': 'Cancelled',
        }
        if obj.to_status == 'ASSIGNED':
            courier_label = _courier_status_label(obj.order, is_bn=False)
            if courier_label:
                return courier_label
        return labels.get(obj.to_status, obj.to_status)


class DeliveryAssignmentSerializer(serializers.ModelSerializer):
    delivery_person_email   = serializers.SerializerMethodField()
    delivery_person_phone   = serializers.SerializerMethodField()
    delivery_person_name    = serializers.SerializerMethodField()
    delivery_person_name_bn = serializers.SerializerMethodField()
    delivery_person_name_en = serializers.SerializerMethodField()
    delivery_person_avatar  = serializers.SerializerMethodField()

    class Meta:
        model  = DeliveryAssignment
        fields = ['id', 'delivery_person', 'delivery_person_email', 'delivery_person_phone',
                  'delivery_person_name', 'delivery_person_name_bn', 'delivery_person_name_en',
                  'delivery_person_avatar', 'assigned_at', 'picked_up_at', 'delivered_at', 'tracking_note']

    def get_delivery_person_email(self, obj):
        return obj.delivery_person.email if obj.delivery_person else ''

    def get_delivery_person_phone(self, obj):
        return obj.delivery_person.phone if obj.delivery_person else ''

    def get_delivery_person_name(self, obj):
        p = getattr(obj.delivery_person, 'profile', None)
        return (p.full_name_bn or p.full_name_en) if p else ''

    def get_delivery_person_name_bn(self, obj):
        p = getattr(obj.delivery_person, 'profile', None)
        return p.full_name_bn if p else ''

    def get_delivery_person_name_en(self, obj):
        p = getattr(obj.delivery_person, 'profile', None)
        return p.full_name_en if p else ''

    def get_delivery_person_avatar(self, obj):
        p = getattr(obj.delivery_person, 'profile', None)
        if not p or not p.avatar:
            return None
        avatar = p.avatar
        if avatar.startswith('http'):
            return avatar
        return f"{settings.BACKEND_URL}{settings.MEDIA_URL}{avatar}"


class SalesOrderSerializer(serializers.ModelSerializer):
    items               = SalesOrderItemSerializer(many=True, read_only=True)
    delivery            = DeliveryAssignmentSerializer(read_only=True)
    customer_email      = serializers.EmailField(source='customer.email', read_only=True)
    status_label        = serializers.SerializerMethodField()
    courier_consignment = serializers.SerializerMethodField()

    class Meta:
        model  = SalesOrder
        fields = [
            'id', 'order_number', 'customer', 'customer_email', 'status', 'status_label',
            'source',
            'payment_method', 'payment_status',
            'shipping_name_bn', 'shipping_name_en', 'shipping_phone',
            'shipping_address_bn', 'shipping_address_en',
            'shipping_district', 'shipping_thana', 'shipping_post_code',
            'subtotal', 'discount_amount', 'tax_amount', 'delivery_charge', 'grand_total', 'cashback_amount', 'cashback_used',
            'notes_bn', 'notes_en',
            'items', 'delivery', 'courier_consignment',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'order_number', 'created_at', 'updated_at']

    def get_status_label(self, obj):
        return dict(SalesOrder._meta.get_field('status').choices).get(obj.status, obj.status)

    def get_courier_consignment(self, obj):
        from api.serializers.courier_serializers import CourierConsignmentSerializer
        consignment = getattr(obj, 'courier_consignment', None)
        return CourierConsignmentSerializer(consignment).data if consignment else None


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
    courier_tracking_url    = serializers.SerializerMethodField()

    class Meta:
        model  = SalesOrder
        fields = [
            'order_number', 'status', 'status_label_bn', 'status_label_en',
            'payment_method', 'payment_status',
            'payment_method_label_bn', 'payment_method_label_en',
            'shipping_name_bn', 'shipping_name_en', 'shipping_phone',
            'shipping_address_bn', 'shipping_district', 'shipping_thana',
            'grand_total', 'created_at',
            'delivery_info', 'courier_tracking_url',
            'timeline',
        ]

    def get_status_label_bn(self, obj):
        if obj.status == 'ASSIGNED':
            courier_label = _courier_status_label(obj, is_bn=True)
            if courier_label:
                return courier_label
        return STATUS_LABELS_BN.get(obj.status, obj.status)

    def get_status_label_en(self, obj):
        if obj.status == 'ASSIGNED':
            courier_label = _courier_status_label(obj, is_bn=False)
            if courier_label:
                return courier_label
        return STATUS_LABELS_EN.get(obj.status, obj.status)

    def get_courier_tracking_url(self, obj):
        from api.serializers.courier_serializers import build_courier_tracking_url
        consignment = getattr(obj, 'courier_consignment', None)
        return build_courier_tracking_url(consignment) if consignment else None

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
            'phone':        d.delivery_person.phone if d.delivery_person else '',
            'assigned_at':  d.assigned_at.isoformat() if d.assigned_at else None,
            'picked_up_at': d.picked_up_at.isoformat() if d.picked_up_at else None,
            'delivered_at': d.delivered_at.isoformat() if d.delivered_at else None,
        }

    def get_timeline(self, obj):
        logs = obj.status_logs.select_related('order__courier_consignment__provider').all()
        return OrderStatusLogSerializer(logs, many=True).data


class AssignDeliverySerializer(serializers.Serializer):
    delivery_person_id = serializers.UUIDField(required=False, allow_null=True)
    weight = serializers.DecimalField(max_digits=6, decimal_places=2, required=False, allow_null=True, default=None)

    def validate_delivery_person_id(self, value):
        if value is None:
            return value
        if not User.objects.filter(id=value, role__code='DELIVERY', is_active=True).exists():
            raise serializers.ValidationError({
                'message_bn': 'ডেলিভারিম্যান পাওয়া যায়নি',
                'message_en': 'Delivery person not found',
            })
        return value


class OrderCancelSerializer(serializers.Serializer):
    note_bn = serializers.CharField(required=False, allow_blank=True, default='')
    note_en = serializers.CharField(required=False, allow_blank=True, default='')


class AddOrderItemSerializer(serializers.Serializer):
    product_id = serializers.UUIDField()
    quantity   = serializers.DecimalField(max_digits=10, decimal_places=3, min_value=Decimal('0.001'))

    def validate_product_id(self, value):
        if not Product.objects.filter(id=value, is_active=True).exists():
            raise serializers.ValidationError({
                'message_bn': 'পণ্য পাওয়া যায়নি',
                'message_en': 'Product not found',
            })
        return value
