# Generated manually — re-adds 'create' for the 'bayna' module: staff with
# this permission can manually add a booking from the admin panel (e.g. a
# customer who called in directly), in addition to the always-open public
# request form (create_booking view stays AllowAny regardless).

from uuid import uuid4
from django.db import migrations

NEW_PERMISSIONS = [
    ('bayna', 'create', 'বায়না বুকিং', 'Bayna Bookings'),
]

ACTION_LABELS = {
    'create': ('তৈরি করুন', 'Create'),
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
        ('api', '0059_bayna_location_required'),
    ]

    operations = [
        migrations.RunPython(add_permissions, remove_permissions),
    ]
