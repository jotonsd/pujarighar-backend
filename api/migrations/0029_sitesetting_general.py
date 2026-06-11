from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0028_sitesetting'),
    ]

    operations = [
        migrations.AddField(model_name='sitesetting', name='company_name_bn',
            field=models.CharField(default='পূজারিঘর', max_length=100)),
        migrations.AddField(model_name='sitesetting', name='company_name_en',
            field=models.CharField(default='PujariGhar', max_length=100)),
        migrations.AddField(model_name='sitesetting', name='contact_phone',
            field=models.CharField(blank=True, default='01978604807', max_length=20)),
        migrations.AddField(model_name='sitesetting', name='contact_email',
            field=models.EmailField(blank=True, default='')),
        migrations.AddField(model_name='sitesetting', name='address_bn',
            field=models.TextField(blank=True, default='')),
        migrations.AddField(model_name='sitesetting', name='address_en',
            field=models.TextField(blank=True, default='')),
    ]
