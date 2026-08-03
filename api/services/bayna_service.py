import logging
from api.models import BaynaBooking, Notification, User
from api.services.notification_ws import broadcast_notifications

logger = logging.getLogger(__name__)


class BaynaService:

    def list_bookings(self, status=None, service_type=None):
        qs = BaynaBooking.objects.all()
        if status:
            qs = qs.filter(status=status)
        if service_type:
            qs = qs.filter(service_type=service_type)
        return qs

    def list_my_bookings(self, user):
        return BaynaBooking.objects.filter(user=user)

    def get_booking(self, pk) -> BaynaBooking:
        return BaynaBooking.objects.get(pk=pk)

    def create_booking(self, user, guest_id: str, data: dict) -> BaynaBooking:
        booking = BaynaBooking.objects.create(
            user=user,
            guest_id='' if user else guest_id,
            service_type=data['service_type'],
            event_date=data['event_date'],
            name=data['name'],
            phone=data['phone'],
            email=data.get('email', ''),
            location=data.get('location', ''),
            description=data['description'],
        )
        self._notify_admins(booking)
        logger.info(f'Bayna booking created: {booking.id} ({booking.service_type}) by {user.email if user else guest_id}')
        return booking

    def update_booking(self, booking: BaynaBooking, data: dict) -> BaynaBooking:
        for field in ('status', 'admin_notes', 'event_date'):
            if field in data:
                setattr(booking, field, data[field])
        booking.save()
        return booking

    SERVICE_LABELS_EN = {'PUJARI': 'Pujari', 'DHAKI': 'Dhaki', 'MURTI': 'Murti'}

    def _notify_admins(self, booking: BaynaBooking) -> None:
        admins = User.objects.filter(role__code='ADMIN', is_active=True)
        label_bn = dict(BaynaBooking.SERVICE_TYPES).get(booking.service_type, booking.service_type)
        label_en = self.SERVICE_LABELS_EN.get(booking.service_type, booking.service_type)
        notifications = [
            Notification(
                user=admin,
                title_bn=f'নতুন বায়না অনুরোধ — {label_bn}',
                title_en=f'New Bayna Request — {label_en}',
                body_bn=f'{booking.name} ({booking.phone}) {booking.event_date}-এ {label_bn} চেয়েছেন।',
                body_en=f'{booking.name} ({booking.phone}) requested {label_en} for {booking.event_date}.',
                reference_type='BAYNA_BOOKING',
                reference_id=booking.id,
            )
            for admin in admins
        ]
        Notification.objects.bulk_create(notifications)
        broadcast_notifications(notifications)
