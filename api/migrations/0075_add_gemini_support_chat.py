from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0074_add_cart_report_permission'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesetting',
            name='gemini_api_key',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='sitesetting',
            name='gemini_model',
            field=models.CharField(blank=True, default='gemini-2.5-flash', max_length=64),
        ),
    ]
