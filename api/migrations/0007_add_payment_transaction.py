import uuid
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0006_salesorder_payment_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='PaymentTransaction',
            fields=[
                ('id',           models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('tran_id',      models.CharField(max_length=100, unique=True)),
                ('session_key',  models.CharField(blank=True, max_length=200)),
                ('val_id',       models.CharField(blank=True, max_length=200)),
                ('bank_tran_id', models.CharField(blank=True, max_length=100)),
                ('card_type',    models.CharField(blank=True, max_length=50)),
                ('amount',       models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ('status',       models.CharField(
                    choices=[
                        ('INITIATED', 'শুরু হয়েছে'),
                        ('PAID',      'পরিশোধিত'),
                        ('FAILED',    'ব্যর্থ'),
                        ('CANCELLED', 'বাতিল'),
                    ],
                    default='INITIATED',
                    max_length=20,
                )),
                ('created_at',   models.DateTimeField(auto_now_add=True)),
                ('updated_at',   models.DateTimeField(auto_now=True)),
                ('order',        models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='payment_transaction',
                    to='api.salesorder',
                )),
            ],
        ),
    ]
