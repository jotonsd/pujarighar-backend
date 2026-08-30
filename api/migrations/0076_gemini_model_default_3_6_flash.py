from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0075_add_gemini_support_chat'),
    ]

    operations = [
        migrations.AlterField(
            model_name='sitesetting',
            name='gemini_model',
            field=models.CharField(blank=True, default='gemini-3.6-flash', max_length=64),
        ),
    ]
