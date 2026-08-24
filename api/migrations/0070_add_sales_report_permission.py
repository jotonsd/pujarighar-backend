# Generated manually — seeds the Permission catalog for the new "Sales
# Report" admin Reports-menu page. View-only, matching the other reports_*
# modules (reports_income, reports_purchases, etc.) — no create/edit/delete
# endpoint exists for a report.

from uuid import uuid4
from django.db import migrations

NEW_PERMISSIONS = [
    ('reports_sales', 'view', 'বিক্রয় রিপোর্ট', 'Sales Report'),
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
        ('api', '0069_user_email_nullable'),
    ]

    operations = [
        migrations.RunPython(add_permissions, remove_permissions),
    ]
