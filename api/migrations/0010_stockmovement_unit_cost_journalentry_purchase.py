from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0009_alter_cartitem_options'),
    ]

    operations = [
        migrations.AddField(
            model_name='stockmovement',
            name='unit_cost',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AlterField(
            model_name='journalentry',
            name='reference_type',
            field=models.CharField(
                choices=[
                    ('PURCHASE', 'ক্রয়'),
                    ('SALE', 'বিক্রয়'),
                    ('PAYMENT', 'পেমেন্ট'),
                    ('RETURN', 'ফেরত'),
                    ('ADJUSTMENT', 'সমন্বয়'),
                ],
                max_length=20,
            ),
        ),
    ]
