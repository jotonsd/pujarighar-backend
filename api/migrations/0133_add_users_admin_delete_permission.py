# Generated manually — makes permanently deleting a user account (see
# hard_delete_user) a grantable permission, same as the 'delete' action
# already seeded for products/discounts/banners/etc. in 0049 — instead of
# being ADMIN-only forever, an admin can now assign it to a custom role via
# Admin > Roles, same as any other module's delete permission. The ADMIN
# system role still always bypasses the check regardless (see Role.is_system
# and has_permission in api/permissions.py).

from uuid import uuid4
from django.db import migrations

NEW_PERMISSIONS = [
    ('users_admin', 'delete', 'ব্যবহারকারী', 'Users'),
]

ACTION_LABELS = {
    'delete': ('মুছুন', 'Delete'),
}


def add_permissions(apps, schema_editor):
    Permission = apps.get_model('api', 'Permission')
    for module, action, label_bn, label_en in NEW_PERMISSIONS:
        action_bn, action_en = ACTION_LABELS[action]
        Permission.objects.get_or_create(
            module=module, action=action,
            defaults={'id': uuid4(), 'label_bn': f'{label_bn} — {action_bn}', 'label_en': f'{label_en} — {action_en}'},
        )


def remove_permissions(apps, schema_editor):
    Permission = apps.get_model('api', 'Permission')
    for module, action, _, _ in NEW_PERMISSIONS:
        Permission.objects.filter(module=module, action=action).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0132_courierprovider_logo'),
    ]

    operations = [
        migrations.RunPython(add_permissions, remove_permissions),
    ]
