from django.db.models import Q

from api.models import User


def get_notified_users():
    """Every admin-facing system notification (new order, courier webhook,
    new review, bayna booking, etc.) goes to this queryset. ADMIN always
    receives them; any other role (including custom admin-staff roles)
    only does if explicitly granted the 'notifications'/'view' permission —
    a role created for a narrow purpose (e.g. delivery-only staff) shouldn't
    be flooded with admin alerts by default."""
    return User.objects.filter(
        Q(role__code='ADMIN') | Q(role__permissions__module='notifications', role__permissions__action='view'),
        is_active=True,
    ).distinct()
