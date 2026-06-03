import logging
from django.db.models import Q
from api.models import User, Profile

logger = logging.getLogger(__name__)


class UserService:

    def list_users(self, role: str = '', search: str = '', is_active: str = ''):
        qs = User.objects.select_related('profile').all()
        if role:
            qs = qs.filter(role=role)
        if search:
            qs = qs.filter(Q(email__icontains=search) | Q(phone__icontains=search))
        if is_active != '':
            qs = qs.filter(is_active=is_active.lower() == 'true')
        return qs

    def get_user(self, pk: str) -> User:
        return User.objects.select_related('profile').get(pk=pk)

    def create_user(self, validated_data: dict) -> User:
        user = User.objects.create_user(**validated_data)
        logger.info(f"Admin created user: {user.email} role={user.role}")
        return user

    def update_user(self, user: User, validated_data: dict) -> User:
        for attr, value in validated_data.items():
            setattr(user, attr, value)
        user.save()
        return user

    def deactivate(self, user: User) -> User:
        user.is_active = False
        user.save(update_fields=['is_active'])
        logger.info(f"User deactivated: {user.email}")
        return user

    def activate(self, user: User) -> User:
        user.is_active = True
        user.save(update_fields=['is_active'])
        logger.info(f"User activated: {user.email}")
        return user

    def change_role(self, user: User, role: str) -> User:
        user.role = role
        user.save(update_fields=['role'])
        logger.info(f"Role changed: {user.email} → {role}")
        return user

    def update_profile(self, user: User, validated_data: dict) -> User:
        profile: Profile = user.profile
        preferred_language = validated_data.pop('preferred_language', None)
        for attr, value in validated_data.items():
            setattr(profile, attr, value)
        profile.save()
        if preferred_language:
            user.preferred_language = preferred_language
            user.save(update_fields=['preferred_language'])
        return user

    def change_password(self, user: User, new_password: str) -> None:
        user.set_password(new_password)
        user.save()

    def list_delivery_persons(self):
        return User.objects.filter(role='DELIVERY', is_active=True).select_related('profile')
