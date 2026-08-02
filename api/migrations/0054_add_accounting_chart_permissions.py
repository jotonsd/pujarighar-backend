# Generated manually — seeds the Permission catalog for the new "Chart of
# Accounts" admin page (list account heads + create + edit/status-toggle; no
# delete, matching the system-wide removal of the delete action).

from uuid import uuid4
from django.db import migrations

NEW_PERMISSIONS = [
    ('accounting_chart', 'view',   'হিসাবের তালিকা', 'Chart of Accounts'),
    ('accounting_chart', 'create', 'হিসাবের তালিকা', 'Chart of Accounts'),
    ('accounting_chart', 'edit',   'হিসাবের তালিকা', 'Chart of Accounts'),
]

ACTION_LABELS = {
    'view':   ('দেখুন', 'View'),
    'create': ('তৈরি করুন', 'Create'),
    'edit':   ('সম্পাদনা', 'Edit'),
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
        ('api', '0053_alter_permission_action'),
    ]

    operations = [
        migrations.RunPython(add_permissions, remove_permissions),
    ]
