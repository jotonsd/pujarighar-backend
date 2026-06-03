import logging
from rest_framework_simplejwt.tokens import RefreshToken
from api.models import User

logger = logging.getLogger(__name__)


class AuthService:

    def register(self, user: User) -> dict:
        """Generate tokens after a new user is created by the serializer."""
        refresh = RefreshToken.for_user(user)
        logger.info(f"New user registered: {user.email}")
        return {
            'access':  str(refresh.access_token),
            'refresh': str(refresh),
        }

    def login(self, user: User) -> dict:
        """Generate JWT tokens for authenticated user."""
        refresh = RefreshToken.for_user(user)
        logger.info(f"User logged in: {user.email}")
        return {
            'access':  str(refresh.access_token),
            'refresh': str(refresh),
        }

    def logout(self, refresh_token: str) -> None:
        """Blacklist the refresh token."""
        from rest_framework_simplejwt.exceptions import TokenError
        try:
            token = RefreshToken(refresh_token)
            token.blacklist()
            logger.info("Refresh token blacklisted")
        except TokenError:
            pass
