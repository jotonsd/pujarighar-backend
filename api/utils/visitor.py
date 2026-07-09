def get_visitor(request):
    """Returns (user_or_None, guest_id). guest_id comes from the frontend-issued
    `X-Guest-Id` header and is only meaningful when there's no authenticated user.
    """
    user = request.user if request.user.is_authenticated else None
    guest_id = '' if user else request.META.get('HTTP_X_GUEST_ID', '')[:64]
    return user, guest_id
