from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0029_sitesetting_general'),
    ]

    operations = [
        migrations.AddField(model_name='sitesetting', name='logo',
            field=models.ImageField(blank=True, null=True, upload_to='site/')),
        migrations.AddField(model_name='sitesetting', name='favicon',
            field=models.ImageField(blank=True, null=True, upload_to='site/')),
    ]
