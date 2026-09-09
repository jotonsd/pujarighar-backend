# Generated manually — a role granted this can view and send promotional
# push-notification broadcasts to customers with the app installed (see
# api/views/promo_push_views.py). ADMIN always has it regardless.

from uuid import uuid4
from django.db import migrations

NEW_PERMISSIONS = [
    ('promo_notifications', 'view',   'প্রমোশনাল নোটিফিকেশন', 'Promotional Notifications'),
    ('promo_notifications', 'create', 'প্রমোশনাল নোটিফিকেশন', 'Promotional Notifications'),
]

ACTION_LABELS = {
    'view':   ('দেখুন', 'View'),
    'create': ('পাঠান', 'Send'),
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
        ('api', '0093_devicetoken_promopush'),
    ]

    operations = [
        migrations.RunPython(add_permissions, remove_permissions),
    ]
