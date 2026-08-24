from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0072_add_sms_create_permission'),
    ]

    operations = [
        migrations.AddField(
            model_name='salesorder',
            name='first_order_discount_amount',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name='sitesetting',
            name='first_order_discount_percent',
            field=models.DecimalField(decimal_places=2, default=Decimal('20.00'), max_digits=5),
        ),
    ]
