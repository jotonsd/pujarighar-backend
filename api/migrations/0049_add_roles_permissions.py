from uuid import uuid4
import django.db.models.deletion
from django.db import migrations, models


# ─── Permission catalog seed ────────────────────────────────────────────────
# One entry per (module, [applicable actions]) — modules map 1:1 onto existing
# admin nav leaf items / resource view-files. Read-only report/dashboard
# modules only get 'view' since no create/edit/delete endpoint exists for them.
MODULE_ACTIONS = {
    'pos':                          (['view', 'create'],                  'পিওএস',                 'POS'),
    'orders':                       (['view', 'create', 'edit'],          'অর্ডার',                 'Orders'),
    'dashboard_overview':           (['view'],                            'ওভারভিউ',                'Overview'),
    'analytics':                   (['view'],                            'অ্যানালিটিক্স ও এসইও',   'Analytics & SEO'),
    'courier':                     (['view', 'create', 'edit'],          'কুরিয়ার',                'Courier'),
    'products':                    (['view', 'create', 'edit', 'delete'], 'পণ্য',                  'Products'),
    'packages':                    (['view', 'create', 'edit', 'delete'], 'প্যাকেজ',                'Packages'),
    'categories':                  (['view', 'create', 'edit', 'delete'], 'কেটাগরি',                'Categories'),
    'brands':                      (['view', 'create', 'edit', 'delete'], 'ব্র্যান্ড',               'Brands'),
    'discounts':                   (['view', 'create', 'edit', 'delete'], 'ডিসকাউন্ট',              'Discounts'),
    'inventory_stock':             (['view', 'create', 'edit'],          'স্টক',                   'Stock'),
    'suppliers':                   (['view', 'create', 'edit', 'delete'], 'সরবরাহকারী',             'Suppliers'),
    'shipping_addresses':          (['view', 'create', 'edit'],          'শিপিং ঠিকানা',           'Shipping Addresses'),
    'users_admin':                 (['view', 'create', 'edit'],          'ব্যবহারকারী',             'Users'),
    'partners':                    (['view', 'create', 'edit', 'delete'], 'অংশীদার',                'Partners'),
    'loans':                       (['view', 'create', 'edit', 'delete'], 'ঋণ বিনিয়োগকারী',        'Loan Investors'),
    'accounting_journal':          (['view', 'create'],                  'জার্নাল',                'Journal'),
    'accounting_ledger':           (['view'],                            'খাতা',                   'Ledger'),
    'accounting_profit_loss':      (['view'],                            'লাভ-ক্ষতি',              'Profit & Loss'),
    'accounting_trial_balance':    (['view'],                            'ট্রায়াল ব্যালেন্স',       'Trial Balance'),
    'accounting_sales_summary':    (['view'],                            'বিক্রয় সারসংক্ষেপ',       'Sales Summary'),
    'expenses':                    (['view', 'create', 'edit', 'delete'], 'খরচ',                   'Expenses'),
    'delivery_charges':            (['view', 'edit'],                    'ডেলিভারি চার্জ',          'Delivery Charges'),
    'cashback':                     (['view', 'edit'],                    'ক্যাশব্যাক',              'Cashback'),
    'reports_purchases':           (['view'],                            'ক্রয় রিপোর্ট',            'Purchase Report'),
    'reports_supplier_returns':    (['view'],                            'সরবরাহকারীকে ফেরত রিপোর্ট', 'Supplier Return Report'),
    'reports_supplier_outstanding':(['view'],                            'সরবরাহকারী বকেয়া রিপোর্ট', 'Supplier Outstanding Report'),
    'reports_product_stock':       (['view'],                            'পণ্য স্টক রিপোর্ট',        'Product Stock Report'),
    'reports_income':              (['view'],                            'আয় রিপোর্ট',              'Income Report'),
    'reports_expenses':            (['view'],                            'ব্যয় রিপোর্ট',            'Expense Report'),
    'hero_slider':                 (['view', 'create', 'edit', 'delete'], 'হিরো স্লাইডার',          'Hero Slider'),
    'banners':                     (['view', 'create', 'edit', 'delete'], 'ব্যানার',                'Banners'),
    'blog':                        (['view', 'create', 'edit', 'delete'], 'ব্লগ পোস্ট',              'Blog Posts'),
    'promo_emails':                (['view', 'create', 'edit'],          'প্রোমো ইমেইল',            'Promo Emails'),
    'reviews':                     (['view', 'edit', 'delete'],          'রিভিউ',                  'Reviews'),
}

ACTION_LABELS = {
    'view':   ('দেখুন', 'View'),
    'create': ('তৈরি করুন', 'Create'),
    'edit':   ('সম্পাদনা', 'Edit'),
    'delete': ('মুছুন', 'Delete'),
}

# Which (module, action) pairs WAREHOUSE keeps — matches its exact current
# capability set (nav_menu links + the IsAdminOrWarehouse-gated endpoints),
# so this migration is a zero-behavior-change seed for existing WAREHOUSE users.
WAREHOUSE_GRANTS = {
    'pos':                 ['view', 'create'],
    'orders':              ['view', 'edit'],
    'products':            ['view'],
    'packages':            ['view'],
    'categories':          ['view'],
    'brands':              ['view'],
    'inventory_stock':     ['view', 'edit'],
    'suppliers':           ['view'],
    'shipping_addresses':  ['view', 'create', 'edit'],
}

SYSTEM_ROLES = [
    ('ADMIN',     'অ্যাডমিন',       'Admin'),
    ('WAREHOUSE', 'গুদামঘর কর্মী',   'Warehouse'),
    ('DELIVERY',  'ডেলিভারিম্যান',  'Delivery'),
    ('CUSTOMER',  'গ্রাহক',         'Customer'),
]


def seed_and_repoint(apps, schema_editor):
    Permission = apps.get_model('api', 'Permission')
    Role = apps.get_model('api', 'Role')
    User = apps.get_model('api', 'User')

    permissions_by_key = {}
    for module, (actions, label_bn, label_en) in MODULE_ACTIONS.items():
        for action in actions:
            action_bn, action_en = ACTION_LABELS[action]
            perm = Permission.objects.create(
                id=uuid4(), module=module, action=action,
                label_bn=f'{label_bn} — {action_bn}', label_en=f'{label_en} — {action_en}',
            )
            permissions_by_key[(module, action)] = perm

    roles_by_code = {}
    for code, name_bn, name_en in SYSTEM_ROLES:
        role = Role.objects.create(id=uuid4(), name_bn=name_bn, name_en=name_en, code=code, is_system=True)
        roles_by_code[code] = role

    warehouse_role = roles_by_code['WAREHOUSE']
    for module, actions in WAREHOUSE_GRANTS.items():
        for action in actions:
            perm = permissions_by_key.get((module, action))
            if perm:
                warehouse_role.permissions.add(perm)

    # Re-point every existing user's old string role value to the matching Role FK.
    for old_code, role in roles_by_code.items():
        User.objects.filter(role_new__isnull=True, role=old_code).update(role_new_id=role.id)


def unseed(apps, schema_editor):
    Role = apps.get_model('api', 'Role')
    Permission = apps.get_model('api', 'Permission')
    Role.objects.all().delete()
    Permission.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0048_remove_category_faqs'),
    ]

    operations = [
        migrations.CreateModel(
            name='Permission',
            fields=[
                ('id', models.UUIDField(default=uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('module', models.CharField(max_length=50)),
                ('action', models.CharField(choices=[('view', 'View'), ('create', 'Create'), ('edit', 'Edit'), ('delete', 'Delete')], max_length=10)),
                ('label_bn', models.CharField(max_length=100)),
                ('label_en', models.CharField(max_length=100)),
            ],
            options={
                'ordering': ['module', 'action'],
                'unique_together': {('module', 'action')},
            },
        ),
        migrations.CreateModel(
            name='Role',
            fields=[
                ('id', models.UUIDField(default=uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name_bn', models.CharField(max_length=100)),
                ('name_en', models.CharField(max_length=100)),
                ('code', models.CharField(blank=True, max_length=20, null=True, unique=True)),
                ('is_system', models.BooleanField(default=False)),
                ('permissions', models.ManyToManyField(blank=True, related_name='roles', to='api.permission')),
            ],
            options={
                'ordering': ['name_bn'],
            },
        ),
        migrations.AddField(
            model_name='user',
            name='role_new',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name='users', to='api.role'),
        ),
        migrations.RunPython(seed_and_repoint, unseed),
        migrations.RemoveField(
            model_name='user',
            name='role',
        ),
        migrations.RenameField(
            model_name='user',
            old_name='role_new',
            new_name='role',
        ),
        migrations.AlterField(
            model_name='user',
            name='role',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='users', to='api.role'),
        ),
    ]
