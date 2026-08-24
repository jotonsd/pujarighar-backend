from uuid import uuid4
import django.db.models.deletion
from django.db import migrations, models


NEW_PERMISSIONS = [
    ('sms', 'view', 'এসএমএস', 'SMS'),
    ('sms', 'edit', 'এসএমএস', 'SMS'),
]

ACTION_LABELS = {
    'view': ('দেখুন', 'View'),
    'edit': ('সম্পাদনা', 'Edit'),
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
        ('api', '0070_add_sales_report_permission'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesetting',
            name='sms_api_key',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='sitesetting',
            name='sms_sender_id',
            field=models.CharField(blank=True, default='', max_length=32),
        ),
        migrations.CreateModel(
            name='SmsLog',
            fields=[
                ('id', models.UUIDField(default=uuid4, editable=False, primary_key=True, serialize=False)),
                ('phone', models.CharField(max_length=20)),
                ('message', models.TextField()),
                ('status', models.CharField(choices=[('SUCCESS', 'সফল'), ('FAILED', 'ব্যর্থ')], max_length=10)),
                ('response_code', models.CharField(blank=True, default='', max_length=10)),
                ('response_text', models.CharField(blank=True, default='', max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('order', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='sms_logs', to='api.salesorder')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.RunPython(add_permissions, remove_permissions),
    ]
