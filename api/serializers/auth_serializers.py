from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken
from api.models import User, Role
from api.serializers.user_serializers import UserSerializer


class RegisterSerializer(serializers.ModelSerializer):
    password        = serializers.CharField(write_only=True, validators=[validate_password])
    full_name_bn    = serializers.CharField(required=False, allow_blank=True)
    full_name_en    = serializers.CharField(required=False, allow_blank=True)
    referral_code   = serializers.CharField(required=False, allow_blank=True, write_only=True)

    class Meta:
        model  = User
        fields = ['email', 'phone', 'password', 'preferred_language', 'full_name_bn', 'full_name_en', 'referral_code']

    def to_internal_value(self, data):
        # Blank out empty-string email/phone to None *before* the normal
        # field validation (including the auto-attached UniqueValidator from
        # the model's unique=True) runs — otherwise the first phone-only
        # signup stores email="" literally, and the second phone-only signup
        # gets rejected as "email already registered" against that blank
        # string. DRF's UniqueValidator already skips None values by design,
        # so normalizing to None (not "") is what actually fixes this.
        data = data.copy() if hasattr(data, 'copy') else dict(data)
        if not str(data.get('email') or '').strip():
            data['email'] = None
        if not str(data.get('phone') or '').strip():
            data['phone'] = None
        return super().to_internal_value(data)

    def validate(self, data):
        # Keyed to the actual form fields (not a generic top-level message) —
        # the frontend's error-mapping only renders errors that land under a
        # real field name, so both email and phone get the same message
        # since either one resolves it.
        if not data.get('email') and not data.get('phone'):
            msg = {'message_bn': 'ইমেইল অথবা ফোন নম্বর আবশ্যক', 'message_en': 'Email or phone number is required'}
            raise serializers.ValidationError({'email': msg, 'phone': msg})
        if not data.get('full_name_bn', '').strip() and not data.get('full_name_en', '').strip():
            raise serializers.ValidationError({'full_name_bn': {
                'message_bn': 'নাম আবশ্যক', 'message_en': 'Name is required',
            }})
        return data

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
        # role has no DB-level default — every self-registered account is a
        # CUSTOMER, never chosen by the client.
        customer_role, _ = Role.objects.get_or_create(
            code='CUSTOMER',
            defaults={'name_bn': 'গ্রাহক', 'name_en': 'Customer', 'is_system': True},
        )
        user = User.objects.create_user(role=customer_role, **validated_data)
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


class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()


class ResetPasswordSerializer(serializers.Serializer):
    uid          = serializers.CharField()
    token        = serializers.CharField()
    new_password = serializers.CharField(write_only=True, validators=[validate_password])


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
