from rest_framework import serializers
from api.models import Permission, Role


class PermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Permission
        fields = ['id', 'module', 'action', 'label_bn', 'label_en']


class RoleSerializer(serializers.ModelSerializer):
    permissions    = PermissionSerializer(many=True, read_only=True)
    permission_ids = serializers.PrimaryKeyRelatedField(
        source='permissions', queryset=Permission.objects.all(), many=True, write_only=True, required=False,
    )
    user_count = serializers.IntegerField(source='users.count', read_only=True)

    class Meta:
        model  = Role
        fields = ['id', 'name_bn', 'name_en', 'code', 'is_system', 'permissions', 'permission_ids', 'user_count', 'created_at']
        read_only_fields = ['id', 'code', 'is_system', 'created_at']

    def validate(self, attrs):
        if self.instance and self.instance.is_system:
            raise serializers.ValidationError({
                'message_bn': 'সিস্টেম ভূমিকা সম্পাদনা করা যাবে না',
                'message_en': 'System roles cannot be edited',
            })
        return attrs
