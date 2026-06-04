from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0010_stockmovement_unit_cost_journalentry_purchase'),
    ]

    operations = [
        migrations.AlterField(
            model_name='product',
            name='unit_price',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
    ]
