from django.conf import settings
from django.http import JsonResponse


class MaintenanceModeMiddleware:
    """
    When MAINTENANCE_MODE=True in the backend .env, blocks all traffic with a
    503 response — except authenticated ADMIN users, so staff can still verify
    the site while it's down for everyone else.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if settings.MAINTENANCE_MODE and not self._is_admin(request):
            return JsonResponse(
                {
                    'status': 'error',
                    'message': 'Site is under maintenance. Please check back soon.',
                    'data': {},
                    'errors': None,
                },
                status=503,
            )
        return self.get_response(request)

    @staticmethod
    def _is_admin(request) -> bool:
        from rest_framework_simplejwt.authentication import JWTAuthentication
        from rest_framework_simplejwt.exceptions import InvalidToken

        try:
            auth = JWTAuthentication().authenticate(request)
        except InvalidToken:
            return False
        if not auth:
            return False
        user, _ = auth
        return user.is_authenticated and user.role == 'ADMIN'
