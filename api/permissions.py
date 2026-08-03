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


def has_any_permission(*module_actions: tuple[str, str]):
    """
    Like has_permission(), but passes if the user holds ANY of the given
    (module, action) pairs. For endpoints that serve as a cross-module lookup
    rather than management of their own resource — e.g. the supplier list is
    a filter-dropdown source for purchase/return/outstanding reports and the
    inventory stock-adjust panel, and the courier-provider list/send-to-courier
    action is used from the order-details assign flow — so a role granted
    only the *consuming* module's permission shouldn't also need the owning
    module's permission just to populate a dropdown or perform the assignment.

    Usage: @permission_classes([IsAuthenticated, has_any_permission(('suppliers', 'view'), ('reports_purchases', 'view'))])
    """
    class _HasAnyPermission(BasePermission):
        def has_permission(self, request, view):
            user = request.user
            if not (user and user.is_authenticated):
                return False
            if user.role.code == 'ADMIN':
                return True
            for module, action in module_actions:
                if user.role.permissions.filter(module=module, action=action).exists():
                    return True
            return False
    return _HasAnyPermission
