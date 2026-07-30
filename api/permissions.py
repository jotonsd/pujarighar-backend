from rest_framework.permissions import BasePermission, SAFE_METHODS


class IsAdmin(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role.code == 'ADMIN'


class IsWarehouse(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role.code == 'WAREHOUSE'


class IsDelivery(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role.code == 'DELIVERY'


class IsCustomer(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role.code == 'CUSTOMER'


class IsAdminOrWarehouse(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role.code in ('ADMIN', 'WAREHOUSE')


class IsAdminOrDelivery(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role.code in ('ADMIN', 'DELIVERY')


class IsAdminOrReadOnly(BasePermission):
    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        return request.user.is_authenticated and request.user.role.code == 'ADMIN'


def has_permission(module: str, action: str):
    """
    Factory returning a parametrized DRF permission class gating access by
    (module, action) instead of a fixed role string — the ADMIN system role
    always bypasses the check (see Role.is_system in api/models.py), every
    other role (including new admin-creatable custom roles) is checked
    against its granted Permission rows.

    Usage: @permission_classes([IsAuthenticated, has_permission('products', 'edit')])
    """
    class _HasPermission(BasePermission):
        def has_permission(self, request, view):
            user = request.user
            if not (user and user.is_authenticated):
                return False
            if user.role.code == 'ADMIN':
                return True
            return user.role.permissions.filter(module=module, action=action).exists()
    return _HasPermission
