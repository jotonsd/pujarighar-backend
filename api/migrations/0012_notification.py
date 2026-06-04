import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0011_product_unit_price_default'),
    ]

    operations = [
        migrations.CreateModel(
            name='Notification',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('title_bn', models.CharField(max_length=200)),
                ('title_en', models.CharField(max_length=200)),
                ('body_bn', models.TextField(blank=True)),
                ('body_en', models.TextField(blank=True)),
                ('is_read', models.BooleanField(default=False)),
                ('reference_type', models.CharField(blank=True, max_length=30)),
                ('reference_id', models.UUIDField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='notifications',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={'ordering': ['-created_at']},
        ),
    ]
