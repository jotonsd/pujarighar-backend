from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken
from api.models import User
from api.serializers.user_serializers import UserSerializer


class RegisterSerializer(serializers.ModelSerializer):
    password        = serializers.CharField(write_only=True, validators=[validate_password])
    full_name_bn    = serializers.CharField(required=False, allow_blank=True)
    full_name_en    = serializers.CharField(required=False, allow_blank=True)
    referral_code   = serializers.CharField(required=False, allow_blank=True, write_only=True)

    class Meta:
        model  = User
        fields = ['email', 'phone', 'password', 'preferred_language', 'full_name_bn', 'full_name_en', 'referral_code']

    def validate_referral_code(self, value):
        if not value:
            return value
        try:
            User.objects.get(referral_code=value.upper().strip())
        except User.DoesNotExist:
            raise serializers.ValidationError({
                'message_bn': 'রেফারেল কোড সঠিক নয়',
                'message_en': 'Invalid referral code',
            })
        return value.upper().strip()

    def create(self, validated_data):
        full_name_bn  = validated_data.pop('full_name_bn', '')
        full_name_en  = validated_data.pop('full_name_en', '')
        referral_code = validated_data.pop('referral_code', '')
        user = User.objects.create_user(**validated_data)
        if referral_code:
            try:
                referrer = User.objects.get(referral_code=referral_code)
                user.referred_by = referrer
                user.save(update_fields=['referred_by'])
            except User.DoesNotExist:
                pass
        user.profile.full_name_bn = full_name_bn
        user.profile.full_name_en = full_name_en
        user.profile.save()
        return user

    def to_representation(self, instance):
        refresh = RefreshToken.for_user(instance)
        return {
            'user':    UserSerializer(instance).data,
            'access':  str(refresh.access_token),
            'refresh': str(refresh),
        }


class LoginSerializer(serializers.Serializer):
    identifier = serializers.CharField()   # email or phone
    password   = serializers.CharField(write_only=True)

    def validate(self, data):
        identifier = data['identifier'].strip()
        password   = data['password']

        # Try to find user by phone first, then email
        user = None
        if identifier.startswith('01') and identifier.isdigit():
            user = User.objects.filter(phone=identifier).first()
            if user and not user.check_password(password):
                user = None
        if user is None:
            user = authenticate(email=identifier, password=password)

        if not user:
            raise serializers.ValidationError({
                'message_bn': 'ইমেইল/ফোন বা পাসওয়ার্ড ভুল',
                'message_en': 'Invalid email/phone or password',
            })
        if not user.is_active:
            raise serializers.ValidationError({
                'message_bn': 'অ্যাকাউন্ট নিষ্ক্রিয়',
                'message_en': 'Account is inactive',
            })
        data['user'] = user
        return data


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, validators=[validate_password])

    def validate_old_password(self, value):
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError({
                'message_bn': 'বর্তমান পাসওয়ার্ড ভুল',
                'message_en': 'Current password is incorrect',
            })
        return value
