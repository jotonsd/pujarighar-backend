from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0026_discount_dates'),
    ]

    operations = [
        migrations.AddField(
            model_name='salesorderitem',
            name='original_unit_price',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
    ]
