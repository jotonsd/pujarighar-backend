# Generated manually — a role granted this can receive admin-facing system
# notifications (new order / courier webhook / review / bayna booking, etc:
# see api/services/notification_recipients.py) alongside the ADMIN role,
# which always receives them regardless of this permission.

from uuid import uuid4
from django.db import migrations

NEW_PERMISSIONS = [
    ('notifications', 'view', 'নোটিফিকেশন', 'Notifications'),
]

ACTION_LABELS = {
    'view': ('দেখুন', 'View'),
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
        ('api', '0084_alter_salesorder_status'),
    ]

    operations = [
        migrations.RunPython(add_permissions, remove_permissions),
    ]
