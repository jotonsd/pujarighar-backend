import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0007_add_payment_transaction'),
    ]

    operations = [
        migrations.CreateModel(
            name='ShippingAddress',
            fields=[
                ('id',           models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('created_at',   models.DateTimeField(auto_now_add=True)),
                ('updated_at',   models.DateTimeField(auto_now=True)),
                ('label',        models.CharField(blank=True, max_length=100)),
                ('full_name_bn', models.CharField(max_length=200)),
                ('full_name_en', models.CharField(blank=True, max_length=200)),
                ('phone',        models.CharField(max_length=15)),
                ('address_bn',   models.TextField()),
                ('address_en',   models.TextField(blank=True)),
                ('district',     models.CharField(blank=True, max_length=100)),
                ('thana',        models.CharField(blank=True, max_length=100)),
                ('post_code',    models.CharField(blank=True, max_length=10)),
                ('is_default',   models.BooleanField(default=False)),
                ('user',         models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='shipping_addresses',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'ordering': ['-is_default', '-created_at'],
            },
        ),
    ]
