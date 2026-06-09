from django.db import migrations


ACCOUNTS = [
    ('1000', 'নগদ',                    'Cash',                      'ASSET'),
    ('1050', 'ব্যাংক হিসাব',            'Bank Account',              'ASSET'),
    ('1100', 'প্রাপ্য হিসাব',           'Accounts Receivable',       'ASSET'),
    ('1200', 'অগ্রদত্ত খরচ',            'Prepaid Expenses',          'ASSET'),
    ('1300', 'মজুদ পণ্য',               'Inventory',                 'ASSET'),
    ('1400', 'স্থায়ী সম্পদ',            'Fixed Assets',              'ASSET'),
    ('1450', 'জমা অবচয়',               'Accumulated Depreciation',  'ASSET'),
    ('2000', 'পরিশোধযোগ্য হিসাব',       'Accounts Payable',          'LIABILITY'),
    ('2100', 'কর দায়',                  'Tax Payable',               'LIABILITY'),
    ('2200', 'অর্জিত দায়',              'Accrued Liabilities',       'LIABILITY'),
    ('2300', 'অংশীদার লাভ দেনা',        'Partner Profit Payable',    'LIABILITY'),
    ('3000', 'মালিকানা মূলধন',          "Owner's Capital",           'EQUITY'),
    ('3100', 'সংরক্ষিত আয়',             'Retained Earnings',         'EQUITY'),
    ('3200', 'মালিকের উত্তোলন',          "Owner's Drawings",          'EQUITY'),
    ('4000', 'বিক্রয় আয়',              'Sales Revenue',             'REVENUE'),
    ('4050', 'বিক্রয় ছাড়',             'Sales Discount',            'REVENUE'),
    ('4100', 'বিক্রয় ফেরত',             'Sales Returns',             'REVENUE'),
    ('4200', 'ডেলিভারি আয়',             'Delivery Income',           'REVENUE'),
    ('4300', 'অন্যান্য আয়',              'Other Income',              'REVENUE'),
    ('5000', 'বিক্রীত মালের খরচ',       'Cost of Goods Sold',        'EXPENSE'),
    ('5100', 'পরিচালনা খরচ',            'Operating Expense',         'EXPENSE'),
    ('6000', 'বেতন ও মজুরি',            'Salary & Wages',            'EXPENSE'),
    ('6100', 'ভাড়া খরচ',               'Rent Expense',              'EXPENSE'),
    ('6200', 'বিদ্যুৎ ও পানি',          'Utilities',                 'EXPENSE'),
    ('6300', 'বিজ্ঞাপন খরচ',            'Marketing & Advertising',   'EXPENSE'),
    ('6400', 'অফিস সরবরাহ',             'Office Supplies',           'EXPENSE'),
    ('6500', 'ডেলিভারি খরচ',            'Delivery Expense',          'EXPENSE'),
    ('6600', 'ব্যাংক চার্জ',             'Bank Charges',              'EXPENSE'),
    ('6700', 'অবচয় খরচ',               'Depreciation Expense',      'EXPENSE'),
    ('6800', 'বিবিধ খরচ',               'Miscellaneous Expense',     'EXPENSE'),
]


def seed_accounts(apps, schema_editor):
    Account = apps.get_model('api', 'Account')
    import uuid
    for code, name_bn, name_en, account_type in ACCOUNTS:
        Account.objects.get_or_create(
            code=code,
            defaults={
                'id': uuid.uuid4(),
                'name_bn': name_bn,
                'name_en': name_en,
                'account_type': account_type,
                'is_active': True,
            },
        )


def reverse_seed(apps, schema_editor):
    pass  # intentionally non-destructive


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0018_partner_invested_amount_profit_payments'),
    ]

    operations = [
        migrations.RunPython(seed_accounts, reverse_seed),
    ]
