import logging
import re
from django.conf import settings
from django.core.files.storage import default_storage
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from api.models import User
from api.serializers.auth_serializers import ChangePasswordSerializer
from api.serializers.user_serializers import (
    UserSerializer, ProfileSerializer,
    AdminCreateUserSerializer, AdminUpdateUserSerializer, ChangeRoleSerializer,
)
from api.services.user_service import UserService
from api.utils.response import ApiResponse
from api.utils.pagination import paginate_queryset
from api.permissions import IsAdmin, IsAdminOrWarehouse

logger = logging.getLogger(__name__)
_svc = UserService()


def _build_nav_menu(role: str) -> list:
    """Return structured nav menu for the given role. Consumed by the frontend Navbar."""

    def link(href, icon, bn, en):
        return {'type': 'link', 'href': href, 'icon': icon, 'label_bn': bn, 'label_en': en}

    def group(icon, bn, en, items):
        return {'type': 'group', 'icon': icon, 'label_bn': bn, 'label_en': en, 'items': items}

    def item(href, icon, bn, en):
        return {'href': href, 'icon': icon, 'label_bn': bn, 'label_en': en}

    if role == 'ADMIN':
        return [
            link('/admin/orders/new',  'receipt',      'POS',            'POS'),
            link('/admin/orders',      'shopping-bag', 'অর্ডার',          'Orders'),
            group('layout-dashboard', 'ড্যাশবোর্ড', 'Dashboard', [
                item('/admin/dashboard',  'bar-chart',   'ওভারভিউ',              'Overview'),
                item('/admin/analytics',  'trending-up', 'অ্যানালিটিক্স ও এসইও', 'Analytics & SEO'),
            ]),
            group('boxes', 'পণ্য ব্যবস্থাপনা', 'Product Management', [
                group('package', 'পণ্য', 'Catalog', [
                    item('/admin/products',         'package',     'পণ্য',          'Products'),
                    item('/admin/packages',         'gift',        'প্যাকেজ',       'Packages'),
                    item('/admin/categories',       'tag',         'কেটাগরি',       'Categories'),
                    item('/admin/settings/brands',  'badge-check', 'ব্র্যান্ড',     'Brands'),
                    item('/admin/discounts',        'percent',     'ডিসকাউন্ট',     'Discounts'),
                ]),
                group('warehouse', 'গুদাম', 'Inventory', [
                    item('/admin/inventory',           'clipboard-list', 'স্টক',         'Stock'),
                    item('/admin/settings/suppliers',  'truck',          'সরবরাহকারী',   'Suppliers'),
                ]),
            ]),
            group('users', 'ব্যবহারকারী', 'Users', [
                item('/admin/users',             'users',      'ব্যবহারকারী',     'Users'),
                item('/admin/settings/partners', 'handshake',  'অংশীদার',          'Partners'),
                item('/admin/settings/loans',    'piggy-bank', 'ঋণ বিনিয়োগকারী', 'Loan Investors'),
            ]),
            group('landmark', 'ফিন্যান্স', 'Finance', [
                group('book-open', 'হিসাব', 'Accounting', [
                    item('/admin/accounting/journal',       'notebook-pen',  'জার্নাল',          'Journal'),
                    item('/admin/accounting/ledger',        'book-open',     'খাতা',              'Ledger'),
                    item('/admin/accounting/profit-loss',   'trending-up',   'লাভ-ক্ষতি',        'Profit & Loss'),
                    item('/admin/accounting/trial-balance', 'scale',         'ট্রায়াল ব্যালেন্স', 'Trial Balance'),
                    item('/admin/accounting/sales-summary', 'shopping-cart', 'বিক্রয় সারসংক্ষেপ', 'Sales Summary'),
                ]),
                item('/admin/expenses',                  'plus-circle', 'খরচ যোগ করুন',   'Add Expense'),
                item('/admin/settings/delivery-charges', 'truck', 'ডেলিভারি চার্জ', 'Delivery Charges'),
                item('/admin/settings/cashback',         'gift',  'ক্যাশব্যাক',      'Cashback'),
            ]),
            group('file-bar-chart', 'রিপোর্ট', 'Reports', [
                item('/admin/reports/purchases',            'receipt',       'ক্রয় রিপোর্ট',              'Purchase Report'),
                item('/admin/reports/supplier-returns',      'undo',         'সরবরাহকারীকে ফেরত রিপোর্ট', 'Supplier Return Report'),
                item('/admin/reports/supplier-outstanding',  'credit-card',  'সরবরাহকারী বকেয়া রিপোর্ট', 'Supplier Outstanding Report'),
                item('/admin/reports/product-stock',         'package',      'পণ্য স্টক রিপোর্ট',         'Product Stock Report'),
                item('/admin/reports/income',                'trending-up',  'আয় রিপোর্ট',                'Income Report'),
                item('/admin/reports/expenses',              'trending-down','ব্যয় রিপোর্ট',              'Expense Report'),
            ]),
            group('megaphone', 'মার্কেটিং', 'Marketing', [
                item('/admin/slides',                 'gallery-horizontal', 'হিরো স্লাইডার', 'Hero Slider'),
                item('/admin/banners',                'target',             'ব্যানার',         'Banners'),
                item('/admin/marketing/promo-emails', 'mail',               'প্রোমো ইমেইল',   'Promo Emails'),
                item('/admin/settings/reviews',       'star',               'রিভিউ',           'Reviews'),
            ]),
        ]

    if role == 'WAREHOUSE':
        return [
            link('/admin/orders/new', 'receipt',      'POS',         'POS'),
            link('/admin/orders',     'shopping-bag', 'অর্ডার',       'Orders'),
            group('package', 'পণ্য', 'Catalog', [
                item('/admin/products',        'package',     'পণ্য',      'Products'),
                item('/admin/packages',        'gift',        'প্যাকেজ',   'Packages'),
                item('/admin/categories',      'tag',         'কেটাগরি',   'Categories'),
                item('/admin/settings/brands', 'badge-check', 'ব্র্যান্ড', 'Brands'),
            ]),
            link('/admin/inventory', 'warehouse', 'গুদাম', 'Inventory'),
        ]

    if role == 'DELIVERY':
        return [
            link('/delivery/orders', 'truck', 'ডেলিভারি', 'My Deliveries'),
        ]

    if role == 'CUSTOMER':
        return [
            link('/',         'home',         'হোম',           'Home'),
            link('/products', 'store',        'পণ্য',           'Products'),
            link('/packages', 'gift',         'প্যাকেজ',        'Packages'),
            link('/track',    'truck',        'অর্ডার ট্র্যাক', 'Track Order'),
            link('/orders',   'shopping-bag', 'আমার অর্ডার',   'My Orders'),
        ]

    return []


# ─── Admin: user list & create ───────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def list_users(request):
    try:
        qs = _svc.list_users(
            role=request.query_params.get('role', ''),
            search=request.query_params.get('search', ''),
            is_active=request.query_params.get('is_active', ''),
        )
        page_data, pagination = paginate_queryset(qs, request)
        return ApiResponse(
            message="Users retrieved successfully",
            data=UserSerializer(page_data, many=True).data,
            pagination=pagination,
            status_code=status.HTTP_200_OK,
        )
    except Exception as e:
        logger.error(f"List users error: {e}", exc_info=True)
        return ApiResponse(message=str(e), errors=str(e), status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAdmin])
def create_user(request):
    serializer = AdminCreateUserSerializer(data=request.data)
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    try:
        user = _svc.create_user(serializer.validated_data)
        return ApiResponse(message="User created", data=UserSerializer(user).data, status_code=201)
    except Exception as e:
        logger.error(f"Create user error: {e}", exc_info=True)
        return ApiResponse(message=str(e), errors=str(e), status_code=400)


# ─── Admin: user detail ───────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def get_user(request, pk):
    try:
        user = _svc.get_user(pk)
        return ApiResponse(message="User retrieved", data=UserSerializer(user).data)
    except User.DoesNotExist:
        return ApiResponse(message="User not found", errors="Not found", status_code=404)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, IsAdmin])
def update_user(request, pk):
    try:
        user = _svc.get_user(pk)
    except User.DoesNotExist:
        return ApiResponse(message="User not found", errors="Not found", status_code=404)
    serializer = AdminUpdateUserSerializer(user, data=request.data, partial=True, context={'request': request})
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    try:
        updated = _svc.update_user(user, serializer.validated_data)
        return ApiResponse(message="User updated", data=UserSerializer(updated).data)
    except Exception as e:
        logger.error(f"Update user error: {e}", exc_info=True)
        return ApiResponse(message=str(e), errors=str(e), status_code=400)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, IsAdmin])
def delete_user(request, pk):
    try:
        user = _svc.get_user(pk)
        _svc.deactivate(user)
        return ApiResponse(message="User deactivated")
    except User.DoesNotExist:
        return ApiResponse(message="User not found", errors="Not found", status_code=404)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAdmin])
def activate_user(request, pk):
    try:
        user = _svc.get_user(pk)
        _svc.activate(user)
        return ApiResponse(message="User activated", data=UserSerializer(user).data)
    except User.DoesNotExist:
        return ApiResponse(message="User not found", errors="Not found", status_code=404)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAdmin])
def deactivate_user(request, pk):
    try:
        user = _svc.get_user(pk)
        _svc.deactivate(user)
        return ApiResponse(message="User deactivated", data=UserSerializer(user).data)
    except User.DoesNotExist:
        return ApiResponse(message="User not found", errors="Not found", status_code=404)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsAdmin])
def change_role(request, pk):
    try:
        user = _svc.get_user(pk)
    except User.DoesNotExist:
        return ApiResponse(message="User not found", errors="Not found", status_code=404)
    serializer = ChangeRoleSerializer(data=request.data, context={'request': request, 'target_user': user})
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    try:
        updated = _svc.change_role(user, serializer.validated_data['role'])
        return ApiResponse(message="Role updated", data=UserSerializer(updated).data)
    except Exception as e:
        return ApiResponse(message=str(e), errors=str(e), status_code=400)


# ─── Current user ─────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_me(request):
    data = UserSerializer(request.user, context={'request': request}).data
    data['nav_menu'] = _build_nav_menu(request.user.role)
    return ApiResponse(message="Profile retrieved", data=data)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def update_me(request):
    # Plain dict (not QueryDict.copy()/deepcopy) — deepcopy chokes on the file
    # handle of large uploads (TemporaryUploadedFile is unpicklable).
    data = request.data.dict() if hasattr(request.data, 'dict') else dict(request.data)
    data.pop('avatar', None)

    phone = request.data.get('phone')
    if phone:
        phone = phone.strip()
        if not re.fullmatch(r'01\d{9}', phone):
            return ApiResponse(
                message="Validation failed",
                errors={'phone': {
                    'message_bn': 'সঠিক ১১ ডিজিটের ফোন নম্বর দিন (যেমনঃ 01XXXXXXXXX)',
                    'message_en': 'Enter a valid 11-digit phone number (e.g. 01XXXXXXXXX)',
                }},
                status_code=422,
            )
        if User.objects.exclude(pk=request.user.pk).filter(phone=phone).exists():
            return ApiResponse(
                message="Validation failed",
                errors={'phone': {
                    'message_bn': 'এই ফোন নম্বরটি ইতিমধ্যে ব্যবহৃত হচ্ছে',
                    'message_en': 'This phone number is already in use',
                }},
                status_code=422,
            )

    avatar_file = request.FILES.get('avatar')
    if avatar_file:
        ALLOWED_TYPES = {'image/jpeg', 'image/png', 'image/webp', 'image/gif'}
        MAX_SIZE = 5 * 1024 * 1024  # 5MB
        if avatar_file.content_type not in ALLOWED_TYPES:
            return ApiResponse(
                message="Validation failed",
                errors={'avatar': {
                    'message_bn': 'শুধুমাত্র JPG, PNG, WEBP বা GIF ছবি আপলোড করা যাবে',
                    'message_en': 'Only JPG, PNG, WEBP, or GIF images are allowed',
                }},
                status_code=422,
            )
        if avatar_file.size > MAX_SIZE:
            return ApiResponse(
                message="Validation failed",
                errors={'avatar': {
                    'message_bn': 'ছবির আকার ৫ এমবি-এর বেশি হতে পারবে না',
                    'message_en': 'Image size must not exceed 5MB',
                }},
                status_code=422,
            )

    serializer = ProfileSerializer(request.user.profile, data=data, partial=True,
                                   context={'request': request})
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    try:
        profile_data = {
            **serializer.validated_data,
            'preferred_language': request.data.get('preferred_language'),
            'phone': phone,
        }
        if avatar_file:
            path = default_storage.save(f'avatars/{avatar_file.name}', avatar_file)
            profile_data['avatar'] = settings.BACKEND_URL + settings.MEDIA_URL + path
        _svc.update_profile(request.user, profile_data)
        data = UserSerializer(request.user, context={'request': request}).data
        data['nav_menu'] = _build_nav_menu(request.user.role)
        return ApiResponse(message="Profile updated", data=data)
    except Exception as e:
        return ApiResponse(message=str(e), errors=str(e), status_code=400)


@api_view(['POST', 'PATCH'])
@permission_classes([IsAuthenticated])
def change_password(request):
    serializer = ChangePasswordSerializer(data=request.data, context={'request': request})
    if not serializer.is_valid():
        return ApiResponse(message="Validation failed", errors=serializer.errors, status_code=422)
    try:
        _svc.change_password(request.user, serializer.validated_data['new_password'])
        return ApiResponse(message="Password changed successfully")
    except Exception as e:
        return ApiResponse(message=str(e), errors=str(e), status_code=400)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdminOrWarehouse])
def list_delivery_persons(request):
    persons = _svc.list_delivery_persons()
    return ApiResponse(message="Delivery persons retrieved", data=UserSerializer(persons, many=True, context={'request': request}).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdminOrWarehouse])
def lookup_user_by_phone(request):
    phone = request.query_params.get('phone', '').strip()
    if not phone:
        return ApiResponse(message="Phone required", errors="phone param required", status_code=400)
    try:
        user = User.objects.get(phone=phone)
        return ApiResponse(message="User found", data=UserSerializer(user).data)
    except User.DoesNotExist:
        return ApiResponse(message="Not found", errors="No user with this phone", status_code=404)
