from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0027_salesorderitem_original_unit_price'),
    ]

    operations = [
        migrations.CreateModel(
            name='SiteSetting',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('invoice_page_size', models.CharField(
                    choices=[
                        ('A4',      'A4 (210×297mm)'),
                        ('A5',      'A5 (148×210mm)'),
                        ('LETTER',  'US Letter (216×279mm)'),
                        ('THERMAL', 'POS Thermal (80mm)'),
                    ],
                    default='A5',
                    max_length=10,
                )),
            ],
            options={'verbose_name': 'Site Setting'},
        ),
    ]
