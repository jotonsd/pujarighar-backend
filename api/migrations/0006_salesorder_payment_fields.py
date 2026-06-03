from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0005_add_hero_slide'),
    ]

    operations = [
        migrations.AddField(
            model_name='salesorder',
            name='payment_method',
            field=models.CharField(
                choices=[('COD', 'ক্যাশ অন ডেলিভারি'), ('ONLINE', 'অনলাইন পেমেন্ট')],
                default='COD',
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='salesorder',
            name='payment_status',
            field=models.CharField(
                choices=[('UNPAID', 'অপরিশোধিত'), ('PAID', 'পরিশোধিত')],
                default='UNPAID',
                max_length=10,
            ),
        ),
    ]
