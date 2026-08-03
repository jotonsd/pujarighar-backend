import logging

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated

from api.models import BaynaBooking
from api.permissions import IsCustomer, has_permission
from api.serializers.bayna_serializers import BaynaBookingSerializer
from api.services.bayna_service import BaynaService
from api.utils.pagination import paginate_queryset
from api.utils.response import ApiResponse, api_error
from api.utils.visitor import get_visitor

logger = logging.getLogger(__name__)
_svc = BaynaService()


@api_view(['POST'])
@permission_classes([AllowAny])
def create_booking(request):
    user, guest_id = get_visitor(request)
    serializer = BaynaBookingSerializer(data=request.data)
    if not serializer.is_valid():
        return ApiResponse(message='Validation failed', errors=serializer.errors, status_code=422)
    try:
        booking = _svc.create_booking(user, guest_id, serializer.validated_data)
        return ApiResponse(message='Booking request submitted', data=BaynaBookingSerializer(booking).data, status_code=201)
    except Exception as e:
        logger.error(f'Bayna create_booking error: {e}', exc_info=True)
        return api_error(e)


@api_view(['GET'])
@permission_classes([IsAuthenticated, has_permission('bayna', 'view')])
def list_bookings(request):
    qs = _svc.list_bookings(
        status=request.query_params.get('status'),
        service_type=request.query_params.get('service_type'),
    )
    page_data, pagination = paginate_queryset(qs, request)
    return ApiResponse(
        message='Bookings retrieved',
        data=BaynaBookingSerializer(page_data, many=True).data,
        pagination=pagination,
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated, has_permission('bayna', 'view')])
def get_booking(request, pk):
    try:
        booking = _svc.get_booking(pk)
    except BaynaBooking.DoesNotExist:
        return ApiResponse(message='Booking not found', errors='Not found', status_code=404)
    return ApiResponse(message='Booking retrieved', data=BaynaBookingSerializer(booking).data)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, has_permission('bayna', 'edit')])
def update_booking(request, pk):
    try:
        booking = _svc.get_booking(pk)
    except BaynaBooking.DoesNotExist:
        return ApiResponse(message='Booking not found', errors='Not found', status_code=404)
    serializer = BaynaBookingSerializer(booking, data=request.data, partial=True)
    if not serializer.is_valid():
        return ApiResponse(message='Validation failed', errors=serializer.errors, status_code=422)
    updated = _svc.update_booking(booking, serializer.validated_data)
    return ApiResponse(message='Booking updated', data=BaynaBookingSerializer(updated).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsCustomer])
def list_my_bookings(request):
    qs = _svc.list_my_bookings(request.user)
    return ApiResponse(message='Bookings retrieved', data=BaynaBookingSerializer(qs, many=True).data)
