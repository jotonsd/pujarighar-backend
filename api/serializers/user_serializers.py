from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from api.models import User, Profile


class ProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Profile
        fields = [
            'full_name_bn', 'full_name_en', 'avatar',
            'address_bn', 'address_en',
            'district', 'thana', 'post_code',
            'cashback_balance',
            'notify_marketing', 'notify_new_product', 'notify_new_package', 'notify_offers',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['cashback_balance', 'created_at', 'updated_at']

    def to_representation(self, instance):
        data = super().to_representation(instance)
        avatar = data.get('avatar') or ''
        if avatar and not avatar.startswith('http'):
            data['avatar'] = f"{settings.BACKEND_URL}{settings.MEDIA_URL}{avatar}"
        return data



class UserSerializer(serializers.ModelSerializer):
    profile = ProfileSerializer(read_only=True)

    class Meta:
        model  = User
        fields = ['id', 'email', 'phone', 'role', 'preferred_language', 'is_active', 'date_joined', 'referral_code', 'profile']
        read_only_fields = ['id', 'date_joined', 'referral_code']


class AdminCreateUserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, validators=[validate_password])

    class Meta:
        model  = User
        fields = ['email', 'phone', 'password', 'role', 'preferred_language']

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)


class AdminUpdateUserSerializer(serializers.ModelSerializer):
    class Meta:
        model  = User
        fields = ['email', 'phone', 'role', 'is_active', 'preferred_language']

    def validate_role(self, value):
        if self.instance and self.instance == self.context['request'].user:
            raise serializers.ValidationError({
                'message_bn': 'নিজের রোল পরিবর্তন করা যাবে না',
                'message_en': 'Cannot change your own role',
            })
        return value


class ChangeRoleSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=['ADMIN', 'WAREHOUSE', 'DELIVERY', 'CUSTOMER'])

    def validate_role(self, value):
        if self.context.get('target_user') == self.context['request'].user:
            raise serializers.ValidationError({
                'message_bn': 'নিজের রোল পরিবর্তন করা যাবে না',
                'message_en': 'Cannot demote yourself',
            })
        return value
