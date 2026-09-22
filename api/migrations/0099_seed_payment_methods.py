from django.db import migrations


def seed(apps, schema_editor):
    PaymentMethod = apps.get_model('api', 'PaymentMethod')
    rows = [
        # code         name_bn                  name_en                enabled integrated sort
        ('COD',        'ক্যাশ অন ডেলিভারি',      'Cash on Delivery',    True,   True,   0),
        ('SSLCOMMERZ', 'অনলাইন পেমেন্ট (SSLCommerz)', 'Online Payment (SSLCommerz)', True, True, 1),
        ('BKASH',      'বিকাশ',                  'bKash',               False,  False,  2),
        ('NAGAD',      'নগদ',                    'Nagad',               False,  False,  3),
        ('STRIPE',     'স্ট্রাইপ (কার্ড)',        'Stripe (Card)',       False,  False,  4),
    ]
    for code, name_bn, name_en, enabled, integrated, sort_order in rows:
        PaymentMethod.objects.get_or_create(
            code=code,
            defaults={
                'name_bn': name_bn, 'name_en': name_en,
                'is_enabled': enabled, 'is_integrated': integrated,
                'sort_order': sort_order,
            },
        )


def unseed(apps, schema_editor):
    PaymentMethod = apps.get_model('api', 'PaymentMethod')
    PaymentMethod.objects.filter(code__in=['COD', 'SSLCOMMERZ', 'BKASH', 'NAGAD', 'STRIPE']).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0098_paymentmethod_salesorder_gateway_charge_amount'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
