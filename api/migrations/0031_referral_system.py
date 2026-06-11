import secrets
import string

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def _gen_code(existing):
    chars = string.ascii_uppercase + string.digits
    while True:
        code = ''.join(secrets.choice(chars) for _ in range(8))
        if code not in existing:
            return code


def populate_referral_codes(apps, schema_editor):
    User = apps.get_model('api', 'User')
    used = set()
    for user in User.objects.all():
        code = _gen_code(used)
        used.add(code)
        user.referral_code = code
        user.save(update_fields=['referral_code'])


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0030_sitesetting_logo_favicon'),
    ]

    operations = [
        # 1. Add field without unique constraint so existing rows get empty string
        migrations.AddField(
            model_name='user',
            name='referral_code',
            field=models.CharField(blank=True, max_length=8, default=''),
            preserve_default=False,
        ),
        # 2. Populate unique codes for all existing users
        migrations.RunPython(populate_referral_codes, migrations.RunPython.noop),
        # 3. Now safe to add the unique constraint
        migrations.AlterField(
            model_name='user',
            name='referral_code',
            field=models.CharField(blank=True, max_length=8, unique=True),
        ),
        migrations.AddField(
            model_name='user',
            name='referred_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='referrals', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='sitesetting',
            name='id',
            field=models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
        ),
        migrations.CreateModel(
            name='ReferralBonus',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('amount', models.DecimalField(decimal_places=2, default=8, max_digits=8)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('order', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='referral_bonus', to='api.salesorder')),
                ('referred_user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='referral_bonus_given', to=settings.AUTH_USER_MODEL)),
                ('referrer', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='referral_bonuses_earned', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'unique_together': {('referrer', 'referred_user')},
            },
        ),
    ]
