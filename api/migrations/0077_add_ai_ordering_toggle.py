from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0076_gemini_model_default_3_6_flash'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesetting',
            name='ai_ordering_enabled',
            field=models.BooleanField(default=False),
        ),
    ]
