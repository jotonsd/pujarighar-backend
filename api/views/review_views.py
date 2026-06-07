import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated

from api.models import Notification, Product, ProductPackageItem, Review, SalesOrder, SalesOrderItem, User
from api.serializers.review_serializers import ReviewSerializer, ReviewCreateSerializer
from api.utils.response import ApiResponse
from api.permissions import IsAdmin

logger = logging.getLogger(__name__)


def _product_in_order(product, order):
    """True if product appears directly or as a package component in the order."""
    if SalesOrderItem.objects.filter(order=order, product=product).exists():
        return True
    package_ids = ProductPackageItem.objects.filter(
        component=product
    ).values_list('package_id', flat=True)
    return SalesOrderItem.objects.filter(order=order, product_id__in=package_ids).exists()


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_review(request):
    serializer = ReviewCreateSerializer(data=request.data)
    if not serializer.is_valid():
        return ApiResponse(message='Validation failed', errors=serializer.errors, status_code=422)

    d = serializer.validated_data
    try:
        product = Product.objects.get(pk=d['product_id'])
    except Product.DoesNotExist:
        return ApiResponse(message='Product not found', errors='Not found', status_code=404)

    try:
        order = SalesOrder.objects.get(pk=d['order_id'], customer=request.user)
    except SalesOrder.DoesNotExist:
        return ApiResponse(message='Order not found', errors='Not found', status_code=404)

    if order.status != 'DELIVERED':
        return ApiResponse(message='Order not delivered yet', errors='Not delivered', status_code=400)

    if not _product_in_order(product, order):
        return ApiResponse(message='Product not in this order', errors='Invalid product', status_code=400)

    if Review.objects.filter(product=product, order=order, user=request.user).exists():
        return ApiResponse(message='Already reviewed', errors='Duplicate review', status_code=400)

    review = Review.objects.create(
        product=product,
        order=order,
        user=request.user,
        rating=d['rating'],
        comment=d['comment'],
    )

    product_name = product.name_bn or product.name_en
    reviewer_name = getattr(getattr(request.user, 'profile', None), 'full_name_bn', None) or request.user.email
    admins = User.objects.filter(role='ADMIN', is_active=True)
    Notification.objects.bulk_create([
        Notification(
            user=admin,
            title_bn=f'নতুন রিভিউ — {product_name}',
            title_en=f'New Review — {product.name_en or product.name_bn}',
            body_bn=f'{reviewer_name} "{product_name}" পণ্যে {d["rating"]}★ রিভিউ দিয়েছেন। অনুমোদনের জন্য দেখুন।',
            body_en=f'{reviewer_name} left a {d["rating"]}★ review on "{product.name_en or product.name_bn}". Please review for approval.',
            reference_type='REVIEW_PENDING',
            reference_id=review.id,
        )
        for admin in admins
    ])

    return ApiResponse(message='Review submitted', data=ReviewSerializer(review).data, status_code=201)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def eligible_order_for_product(request, pk):
    """
    Returns the most recent delivered order ID where the user can still review
    this product (directly or via a package component). Returns null if none.
    """
    try:
        product = Product.objects.get(pk=pk)
    except Product.DoesNotExist:
        return ApiResponse(message='Product not found', errors='Not found', status_code=404)

    already_reviewed_order_ids = Review.objects.filter(
        product=product, user=request.user
    ).values_list('order_id', flat=True)

    # Direct order items
    direct_qs = SalesOrderItem.objects.filter(
        product=product,
        order__customer=request.user,
        order__status='DELIVERED',
    ).exclude(order_id__in=already_reviewed_order_ids).values_list('order_id', flat=True)

    # Package component — find packages containing this product
    package_ids = ProductPackageItem.objects.filter(
        component=product
    ).values_list('package_id', flat=True)
    component_qs = SalesOrderItem.objects.filter(
        product_id__in=package_ids,
        order__customer=request.user,
        order__status='DELIVERED',
    ).exclude(order_id__in=already_reviewed_order_ids).values_list('order_id', flat=True)

    eligible_ids = list(set(direct_qs) | set(component_qs))
    if not eligible_ids:
        return ApiResponse(message='No eligible order', data={'order_id': None})

    order = SalesOrder.objects.filter(pk__in=eligible_ids).order_by('-created_at').first()
    return ApiResponse(message='Eligible order found', data={'order_id': str(order.id)})


@api_view(['GET'])
@permission_classes([AllowAny])
def list_product_reviews(request, pk):
    try:
        product = Product.objects.get(pk=pk)
    except Product.DoesNotExist:
        return ApiResponse(message='Product not found', errors='Not found', status_code=404)

    reviews = Review.objects.filter(product=product, is_approved=True).select_related('user__profile')
    return ApiResponse(message='Reviews retrieved', data=ReviewSerializer(reviews, many=True).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_order_reviews(request):
    """Returns review status for every item in a specific delivered order."""
    order_id = request.query_params.get('order_id')
    if not order_id:
        return ApiResponse(message='order_id required', errors='Missing param', status_code=400)
    try:
        order = SalesOrder.objects.get(pk=order_id, customer=request.user)
    except SalesOrder.DoesNotExist:
        return ApiResponse(message='Order not found', errors='Not found', status_code=404)

    reviews = Review.objects.filter(order=order, user=request.user).select_related('user__profile')
    return ApiResponse(message='Reviews retrieved', data=ReviewSerializer(reviews, many=True).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def list_pending_reviews(request):
    reviews = Review.objects.filter(is_approved=False).select_related('user__profile', 'product')
    return ApiResponse(message='Pending reviews', data=ReviewSerializer(reviews, many=True).data)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, IsAdmin])
def approve_review(_request, pk):
    try:
        review = Review.objects.get(pk=pk)
    except Review.DoesNotExist:
        return ApiResponse(message='Review not found', errors='Not found', status_code=404)
    review.is_approved = True
    review.save(update_fields=['is_approved'])
    return ApiResponse(message='Review approved', data=ReviewSerializer(review).data)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, IsAdmin])
def delete_review(_request, pk):
    try:
        Review.objects.get(pk=pk).delete()
        return ApiResponse(message='Review deleted')
    except Review.DoesNotExist:
        return ApiResponse(message='Review not found', errors='Not found', status_code=404)
