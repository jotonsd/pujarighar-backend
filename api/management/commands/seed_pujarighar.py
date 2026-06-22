import random
from decimal import Decimal
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = 'Seed PujariGhar with chart of accounts, categories, products, and users'

    def add_arguments(self, parser):
        parser.add_argument(
            '--with-demo-products', action='store_true',
            help='Force-seed the demo product catalog even in production (ENVIRONMENT=production).',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        self._seed_accounts()
        self._seed_categories()
        self._seed_brands()

        if not settings.IS_PRODUCTION or options['with_demo_products']:
            self._seed_products()
            self._seed_users()
            self._seed_images()
        else:
            self.stdout.write(self.style.WARNING(
                '  – Skipped demo products/users/images (ENVIRONMENT=production). '
                'Pass --with-demo-products to force.'
            ))

        self.stdout.write(self.style.SUCCESS('✓ Seed completed successfully'))

    def _seed_accounts(self):
        from api.models import Account
        rows = [
            # ── ASSETS ──────────────────────────────────────────────────────
            ('1000', 'নগদ',                    'Cash',                      'ASSET'),
            ('1050', 'ব্যাংক হিসাব',            'Bank Account',              'ASSET'),
            ('1100', 'প্রাপ্য হিসাব',           'Accounts Receivable',       'ASSET'),
            ('1200', 'অগ্রদত্ত খরচ',            'Prepaid Expenses',          'ASSET'),
            ('1300', 'মজুদ পণ্য',               'Inventory',                 'ASSET'),
            ('1400', 'স্থায়ী সম্পদ',            'Fixed Assets',              'ASSET'),
            ('1450', 'জমা অবচয়',               'Accumulated Depreciation',  'ASSET'),
            # ── LIABILITIES ─────────────────────────────────────────────────
            ('2000', 'পরিশোধযোগ্য হিসাব',       'Accounts Payable',          'LIABILITY'),
            ('2100', 'কর দায়',                  'Tax Payable',               'LIABILITY'),
            ('2200', 'অর্জিত দায়',              'Accrued Liabilities',       'LIABILITY'),
            ('2300', 'অংশীদার লাভ দেনা',        'Partner Profit Payable',    'LIABILITY'),
            # ── EQUITY ──────────────────────────────────────────────────────
            ('3000', 'মালিকানা মূলধন',          'Owner\'s Capital',          'EQUITY'),
            ('3100', 'সংরক্ষিত আয়',             'Retained Earnings',         'EQUITY'),
            ('3200', 'মালিকের উত্তোলন',          'Owner\'s Drawings',         'EQUITY'),
            # ── REVENUE ─────────────────────────────────────────────────────
            ('4000', 'বিক্রয় আয়',              'Sales Revenue',             'REVENUE'),
            ('4050', 'বিক্রয় ছাড়',             'Sales Discount',            'REVENUE'),
            ('4100', 'বিক্রয় ফেরত',             'Sales Returns',             'REVENUE'),
            ('4200', 'ডেলিভারি আয়',             'Delivery Income',           'REVENUE'),
            ('4300', 'অন্যান্য আয়',              'Other Income',              'REVENUE'),
            # ── COST OF SALES ────────────────────────────────────────────────
            ('5000', 'বিক্রীত মালের খরচ',       'Cost of Goods Sold',        'EXPENSE'),
            # ── OPERATING EXPENSES ───────────────────────────────────────────
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
        for code, name_bn, name_en, acct_type in rows:
            Account.objects.get_or_create(
                code=code,
                defaults={'name_bn': name_bn, 'name_en': name_en, 'account_type': acct_type},
            )
        self.stdout.write('  ✓ Chart of accounts seeded')

    def _seed_categories(self):
        from api.models import Category
        rows = [
            ('প্রদীপ ও মোমবাতি',      'Lamps & Candles',       'lamps-candles'),
            ('ধূপকাঠি ও আগরবাতি',    'Incense Sticks',        'incense-sticks'),
            ('ফুল ও মালা',            'Flowers & Garlands',    'flowers-garlands'),
            ('পূজার থালি',            'Puja Thali',            'puja-thali'),
            ('প্রতিমা ও মূর্তি',      'Idols & Figurines',     'idols-figurines'),
            ('পূজার সরঞ্জাম',         'Puja Accessories',      'puja-accessories'),
            ('পবিত্র উপকরণ',          'Sacred Ingredients',    'sacred-ingredients'),
        ]
        for name_bn, name_en, slug in rows:
            Category.objects.get_or_create(slug=slug, defaults={'name_bn': name_bn, 'name_en': name_en})
        self.stdout.write('  ✓ Categories seeded')

    def _seed_brands(self):
        from api.models import Brand
        rows = [
            ('সাইকেল',       'Cycle',       'cycle'),
            ('মঙ্গলদীপ',     'Mangaldeep',  'mangaldeep'),
            ('পতঞ্জলি',      'Patanjali',   'patanjali'),
            ('দেবজ্যোতি',    'Devjyoti',    'devjyoti'),
            ('পূজারিঘর',     'PujariGhar',  'pujarighar'),
        ]
        for name_bn, name_en, slug in rows:
            Brand.objects.get_or_create(slug=slug, defaults={'name_bn': name_bn, 'name_en': name_en})
        self.stdout.write('  ✓ Brands seeded')

    def _seed_products(self):
        from api.models import Brand, Category, Product, StockMovement, User

        admin  = User.objects.filter(role='ADMIN').first()
        cat    = {c.slug: c for c in Category.objects.all()}
        brands = {b.slug: b for b in Brand.objects.all()}

        brand_list = list(brands.values())

        # (name_bn, name_en, desc_bn, desc_en, sku, cat_slug, price, cost, stock)
        items = [
            # ── Incense ──────────────────────────────────────────────────────
            ('চন্দন কাঠের ধূপকাঠি',   'Sandalwood Incense Sticks',
             'খাঁটি চন্দন কাঠ থেকে তৈরি সুগন্ধি ধূপকাঠি। পূজায় মনকে শান্ত করে।',
             'Pure sandalwood incense sticks. Calms the mind during worship.',
             'SKU-001', 'incense-sticks', 50, 30, 200),

            ('গোলাপ আগরবাতি',          'Rose Agarbatti',
             'গোলাপের সুগন্ধে ভরা আগরবাতি। প্রতিটি প্যাকেটে ২০টি কাঠি।',
             'Rose-scented agarbatti. 20 sticks per pack.',
             'SKU-002', 'incense-sticks', 40, 25, 200),

            ('চামেলি আগরবাতি',         'Jasmine Agarbatti',
             'চামেলি ফুলের মিষ্টি সুগন্ধ। দেবীর পূজায় বিশেষভাবে ব্যবহৃত।',
             'Sweet jasmine fragrance. Specially used in goddess worship.',
             'SKU-003', 'incense-sticks', 40, 25, 150),

            ('কস্তুরী ধূপকাঠি',        'Musk Incense Sticks',
             'কস্তুরীর গভীর সুগন্ধে ঘর মাতিয়ে তোলে। দীর্ঘস্থায়ী সুগন্ধ।',
             'Deep musk fragrance fills the room. Long-lasting scent.',
             'SKU-004', 'incense-sticks', 60, 38, 120),

            ('নিম আগরবাতি',            'Neem Agarbatti',
             'নিম পাতার নির্যাসে তৈরি। পবিত্রতা ও রোগমুক্তির জন্য আদর্শ।',
             'Made from neem leaf extract. Ideal for purification and healing.',
             'SKU-005', 'incense-sticks', 35, 20, 180),

            # ── Lamps ────────────────────────────────────────────────────────
            ('মাটির প্রদীপ',           'Clay Diya',
             'হাতে তৈরি মাটির প্রদীপ। পরিবেশবান্ধব ও ঐতিহ্যবাহী।',
             'Handmade clay diya. Eco-friendly and traditional.',
             'SKU-006', 'lamps-candles', 20, 10, 500),

            ('ঘি প্রদীপ',              'Ghee Lamp',
             'গরুর ঘি দিয়ে জ্বালানোর জন্য বিশেষভাবে তৈরি পিতলের প্রদীপ।',
             'Brass lamp specially designed for burning with cow ghee.',
             'SKU-007', 'lamps-candles', 120, 75, 100),

            ('পিতলের পঞ্চপ্রদীপ',     'Brass Panchdiya (5-wick Lamp)',
             'পাঁচটি বাতির পিতলের প্রদীপ। আরতির জন্য আদর্শ।',
             'Five-wick brass lamp. Ideal for aarti.',
             'SKU-008', 'lamps-candles', 280, 170, 80),

            ('রঙিন মোমবাতি সেট',       'Coloured Candles Set',
             '১২টি রঙিন মোমবাতির সেট। উৎসব ও পূজায় ব্যবহারযোগ্য।',
             'Set of 12 coloured candles. Suitable for festivals and puja.',
             'SKU-009', 'lamps-candles', 80, 50, 150),

            ('তুলসী প্রদীপ',           'Tulsi Diya',
             'তুলসী পাতার নকশায় তৈরি বিশেষ প্রদীপ। বিষ্ণু পূজায় ব্যবহৃত।',
             'Special lamp with tulsi leaf design. Used in Vishnu worship.',
             'SKU-010', 'lamps-candles', 95, 60, 120),

            # ── Flowers & Garlands ───────────────────────────────────────────
            ('গাঁদা ফুলের মালা',       'Marigold Garland',
             'তাজা গাঁদা ফুলের মালা। প্রতিটি মালা ৩ ফুট লম্বা।',
             'Fresh marigold garland. Each garland is 3 feet long.',
             'SKU-011', 'flowers-garlands', 60, 35, 100),

            ('পদ্ম ফুল',               'Lotus Flower',
             'পূজার জন্য তাজা পদ্ম ফুল। দেবীর চরণে নিবেদনের জন্য আদর্শ।',
             'Fresh lotus flower for puja. Ideal for offering at the feet of the deity.',
             'SKU-012', 'flowers-garlands', 30, 18, 150),

            ('বেলফুলের মালা',          'Jasmine Garland',
             'সুগন্ধি বেলফুলের মালা। শিব ও দুর্গা পূজায় বিশেষ উপযোগী।',
             'Fragrant jasmine garland. Especially suitable for Shiva and Durga puja.',
             'SKU-013', 'flowers-garlands', 70, 42, 80),

            ('লাল গোলাপ মালা',         'Red Rose Garland',
             'লাল গোলাপের মালা। প্রেম ও ভক্তির প্রতীক।',
             'Red rose garland. Symbol of love and devotion.',
             'SKU-014', 'flowers-garlands', 90, 55, 60),

            ('তুলসী মালা',             'Tulsi Mala',
             'পবিত্র তুলসী পাতার মালা। বিষ্ণু ভক্তদের জন্য অপরিহার্য।',
             'Sacred tulsi leaf garland. Essential for Vishnu devotees.',
             'SKU-015', 'flowers-garlands', 45, 28, 200),

            # ── Puja Thali ──────────────────────────────────────────────────
            ('পিতলের পূজার থালি',      'Brass Puja Thali',
             'হাতে খোদাই করা পিতলের থালি। ব্যাস ১২ ইঞ্চি। সম্পূর্ণ সেট।',
             'Hand-engraved brass thali. 12-inch diameter. Complete set.',
             'SKU-016', 'puja-thali', 350, 200, 50),

            ('রূপালি প্রলেপের থালি',  'Silver Plated Thali',
             'রূপালি প্রলেপযুক্ত সুন্দর থালি। বিশেষ অনুষ্ঠানের জন্য আদর্শ।',
             'Beautiful silver-plated thali. Ideal for special occasions.',
             'SKU-017', 'puja-thali', 550, 320, 30),

            ('স্টেনলেস স্টিলের থালি', 'Stainless Steel Thali',
             'টেকসই স্টেনলেস স্টিলের থালি। দৈনন্দিন পূজার জন্য উপযুক্ত।',
             'Durable stainless steel thali. Suitable for daily puja.',
             'SKU-018', 'puja-thali', 180, 110, 100),

            ('মাটির সাজানো থালি',     'Decorated Clay Thali',
             'হাতে রঙ করা মাটির থালি। পরিবেশবান্ধব ও সুন্দর।',
             'Hand-painted clay thali. Eco-friendly and beautiful.',
             'SKU-019', 'puja-thali', 120, 70, 80),

            ('কাঠের পূজার থালি',      'Wooden Puja Thali',
             'আমকাঠে তৈরি সুন্দর থালি। হস্তশিল্পীদের দ্বারা তৈরি।',
             'Beautiful mango wood thali. Handcrafted by artisans.',
             'SKU-020', 'puja-thali', 250, 150, 60),

            # ── Idols ────────────────────────────────────────────────────────
            ('দুর্গা মূর্তি',          'Durga Idol',
             'অষ্টধাতু নির্মিত দুর্গা মূর্তি। উচ্চতা ৬ ইঞ্চি। অত্যন্ত সূক্ষ্ম কারুকাজ।',
             'Ashtadhatu Durga idol. 6 inches height. Extremely fine craftsmanship.',
             'SKU-021', 'idols-figurines', 500, 300, 40),

            ('গণেশ মূর্তি',            'Ganesha Idol',
             'পিতলের গণেশ মূর্তি। উচ্চতা ৫ ইঞ্চি। সিদ্ধিদাতা গণেশের আশীর্বাদ পান।',
             'Brass Ganesha idol. 5 inches height. Receive blessings of Siddhidata Ganesha.',
             'SKU-022', 'idols-figurines', 400, 250, 50),

            ('কালী মূর্তি',            'Kali Idol',
             'কালো রঙের কালী মূর্তি। উচ্চতা ৭ ইঞ্চি। শক্তির দেবীর অপরূপ রূপ।',
             'Black Kali idol. 7 inches height. Magnificent form of the goddess of power.',
             'SKU-023', 'idols-figurines', 450, 280, 35),

            ('লক্ষ্মী মূর্তি',         'Lakshmi Idol',
             'সোনালি রঙের লক্ষ্মী মূর্তি। উচ্চতা ৫ ইঞ্চি। ধন ও সমৃদ্ধির দেবী।',
             'Golden Lakshmi idol. 5 inches height. Goddess of wealth and prosperity.',
             'SKU-024', 'idols-figurines', 420, 260, 45),

            ('সরস্বতী মূর্তি',         'Saraswati Idol',
             'সাদা রঙের সরস্বতী মূর্তি। উচ্চতা ৬ ইঞ্চি। বিদ্যা ও সংগীতের দেবী।',
             'White Saraswati idol. 6 inches height. Goddess of knowledge and music.',
             'SKU-025', 'idols-figurines', 430, 270, 40),

            ('রাধা-কৃষ্ণ মূর্তি',      'Radha-Krishna Idol',
             'পিতলের রাধা-কৃষ্ণ যুগল মূর্তি। উচ্চতা ৮ ইঞ্চি। প্রেম ও ভক্তির প্রতীক।',
             'Brass Radha-Krishna idol. 8 inches height. Symbol of love and devotion.',
             'SKU-026', 'idols-figurines', 650, 400, 25),

            ('শিব লিঙ্গম',             'Shiva Lingam',
             'কালো পাথরের শিব লিঙ্গম। উচ্চতা ৪ ইঞ্চি। মহাদেবের পূজার জন্য।',
             'Black stone Shiva Lingam. 4 inches height. For worship of Mahadeva.',
             'SKU-027', 'idols-figurines', 380, 230, 60),

            # ── Puja Accessories ─────────────────────────────────────────────
            ('পিতলের ঘণ্টা',           'Brass Bell',
             'পিতলের হাতঘণ্টা। পূজার শুরুতে বাজানো হয়। মিষ্টি শব্দ।',
             'Brass hand bell. Rung at the start of puja. Sweet sound.',
             'SKU-028', 'puja-accessories', 150, 90, 100),

            ('শঙ্খ',                    'Conch Shell',
             'প্রাকৃতিক শঙ্খ। পূজায় ফুঁ দেওয়ার জন্য। পবিত্র শব্দ তৈরি করে।',
             'Natural conch shell. For blowing in puja. Creates a sacred sound.',
             'SKU-029', 'puja-accessories', 220, 130, 60),

            ('আরতির ধূপদানি',          'Incense Holder for Aarti',
             'পিতলের আরতির ধূপদানি। ৫টি ধূপকাঠি একসঙ্গে রাখা যায়।',
             'Brass incense holder for aarti. Can hold 5 incense sticks at once.',
             'SKU-030', 'puja-accessories', 180, 110, 80),

            ('পিতলের কলস',             'Brass Kalash',
             'পূজার জন্য পিতলের কলস। ধারণক্ষমতা ৫০০ মিলি। সুন্দর কারুকাজ।',
             'Brass kalash for puja. 500ml capacity. Beautiful craftsmanship.',
             'SKU-031', 'puja-accessories', 300, 180, 70),

            ('আরতি স্ট্যান্ড',          'Aarti Stand',
             'পাঁচটি বাতির আরতি স্ট্যান্ড। পিতল নির্মিত। উচ্চতা ১০ ইঞ্চি।',
             'Five-wick aarti stand. Made of brass. Height 10 inches.',
             'SKU-032', 'puja-accessories', 350, 210, 45),

            # ── Sacred Ingredients ────────────────────────────────────────────
            ('গঙ্গাজল',                'Gangajal (Holy Water)',
             'পবিত্র গঙ্গানদীর জল। ১০০ মিলি বোতলে। পূজায় ছিটানোর জন্য।',
             'Sacred Ganges river water. In 100ml bottle. For sprinkling in puja.',
             'SKU-033', 'sacred-ingredients', 50, 30, 300),

            ('চন্দন পেস্ট',            'Sandalwood Paste',
             'খাঁটি চন্দন কাঠ থেকে তৈরি পেস্ট। দেবতার কপালে তিলক দেওয়ার জন্য।',
             'Paste made from pure sandalwood. For applying tilak on deity\'s forehead.',
             'SKU-034', 'sacred-ingredients', 80, 50, 150),

            ('কুমকুম',                  'Kumkum (Vermilion)',
             'লাল কুমকুম। দেবীর পূজায় ব্যবহারের জন্য। ৫০ গ্রাম প্যাকেট।',
             'Red kumkum. For use in goddess worship. 50g packet.',
             'SKU-035', 'sacred-ingredients', 30, 18, 400),

            ('হলুদ গুঁড়া',             'Turmeric Powder',
             'পূজার জন্য বিশুদ্ধ হলুদ গুঁড়া। ১০০ গ্রাম প্যাকেট।',
             'Pure turmeric powder for puja. 100g packet.',
             'SKU-036', 'sacred-ingredients', 25, 15, 400),

            ('আক্ষত চাল',              'Akshata Rice',
             'পূজায় নিবেদনের জন্য বিশুদ্ধ আক্ষত চাল। হলুদ মেশানো। ২৫০ গ্রাম।',
             'Pure akshata rice for puja offering. Mixed with turmeric. 250g.',
             'SKU-037', 'sacred-ingredients', 40, 25, 300),
        ]

        for name_bn, name_en, desc_bn, desc_en, sku, cat_slug, price, cost, stock in items:
            category = cat.get(cat_slug)
            if not category:
                continue
            p, created = Product.objects.get_or_create(sku=sku, defaults={
                'name_bn': name_bn, 'name_en': name_en,
                'description_bn': desc_bn, 'description_en': desc_en,
                'category': category,
                'brand': random.choice(brand_list) if brand_list else None,
                'unit_price': Decimal(price), 'cost_price': Decimal(cost),
            })
            if not created and p.brand is None and brand_list:
                p.brand = random.choice(brand_list)
                p.save(update_fields=['brand'])
            if admin and not p.stock_movements.exists():
                StockMovement.objects.create(
                    product=p, movement_type='PURCHASE',
                    quantity=Decimal(stock),
                    unit_cost=Decimal(cost),
                    created_by=admin,
                    note_bn='প্রাথমিক স্টক', note_en='Initial stock',
                )

        self._seed_packages(cat)
        self.stdout.write(f'  ✓ {len(items)} products seeded')

    def _seed_packages(self, cat):
        from api.models import Product, ProductPackageItem

        def get(sku):
            return Product.objects.filter(sku=sku).first()

        # ── Package 1: সম্পূর্ণ পূজার সেট ──────────────────────────────────
        if all([get('SKU-016'), get('SKU-001'), get('SKU-006'), get('SKU-011')]):
            pkg, _ = Product.objects.get_or_create(sku='PKG-001', defaults={
                'name_bn': 'সম্পূর্ণ পূজার সেট',
                'name_en': 'Complete Puja Set',
                'description_bn': 'পূজার জন্য প্রয়োজনীয় সব কিছু একটি সেটে। থালি, ধূপকাঠি, প্রদীপ ও মালা সহ।',
                'description_en': 'Everything needed for puja in one set. Includes thali, incense, diya and garland.',
                'category': cat.get('puja-thali') or get('SKU-016').category,
                'unit_price': Decimal('480'), 'cost_price': Decimal('290'),
                'is_package': True,
            })
            components = [('SKU-016', 1), ('SKU-001', 2), ('SKU-006', 3), ('SKU-011', 1)]
            for sku, qty in components:
                ProductPackageItem.objects.get_or_create(
                    package=pkg, component=get(sku), defaults={'quantity': qty},
                )

        # ── Package 2: নবরাত্রি পূজা প্যাকেজ ──────────────────────────────
        if all([get('SKU-021'), get('SKU-013'), get('SKU-008'), get('SKU-035'), get('SKU-033')]):
            pkg2, _ = Product.objects.get_or_create(sku='PKG-002', defaults={
                'name_bn': 'নবরাত্রি পূজা প্যাকেজ',
                'name_en': 'Navratri Puja Package',
                'description_bn': 'নবরাত্রির ৯ দিনের পূজার জন্য সম্পূর্ণ প্যাকেজ। দুর্গা মূর্তি ও সকল উপকরণ সহ।',
                'description_en': 'Complete package for 9 days of Navratri puja. Includes Durga idol and all essentials.',
                'category': cat.get('puja-thali') or get('SKU-021').category,
                'unit_price': Decimal('950'), 'cost_price': Decimal('570'),
                'is_package': True,
            })
            components2 = [('SKU-021', 1), ('SKU-013', 2), ('SKU-008', 5), ('SKU-035', 1), ('SKU-033', 1)]
            for sku, qty in components2:
                ProductPackageItem.objects.get_or_create(
                    package=pkg2, component=get(sku), defaults={'quantity': qty},
                )

        # ── Package 3: সত্যনারায়ণ পূজার কিট ──────────────────────────────
        if all([get('SKU-031'), get('SKU-028'), get('SKU-004'), get('SKU-034'), get('SKU-037')]):
            pkg3, _ = Product.objects.get_or_create(sku='PKG-003', defaults={
                'name_bn': 'সত্যনারায়ণ পূজার কিট',
                'name_en': 'Satyanarayan Puja Kit',
                'description_bn': 'সত্যনারায়ণ পূজার সম্পূর্ণ কিট। কলস, ঘণ্টা, চন্দন পেস্ট ও আক্ষত সহ।',
                'description_en': 'Complete kit for Satyanarayan puja. Includes kalash, bell, sandalwood paste and akshata.',
                'category': cat.get('puja-accessories') or get('SKU-031').category,
                'unit_price': Decimal('650'), 'cost_price': Decimal('390'),
                'is_package': True,
            })
            components3 = [('SKU-031', 1), ('SKU-028', 1), ('SKU-004', 2), ('SKU-034', 1), ('SKU-037', 2)]
            for sku, qty in components3:
                ProductPackageItem.objects.get_or_create(
                    package=pkg3, component=get(sku), defaults={'quantity': qty},
                )

        # Packages have NO direct stock — availability is computed from components.
        self.stdout.write('  ✓ 3 package bundles seeded')

    def _seed_images(self):
        from io import BytesIO
        from PIL import Image, ImageDraw
        from django.core.files.base import ContentFile
        from api.models import Product, ProductImage

        # Background colour per category slug
        PALETTE = {
            'incense-sticks':     ((255, 200, 150), (180, 100,  40)),
            'lamps-candles':      ((255, 240, 160), (200, 150,  30)),
            'flowers-garlands':   ((255, 190, 210), (180,  60,  90)),
            'puja-thali':         ((210, 215, 225), (100, 110, 140)),
            'idols-figurines':    ((215, 185, 135), (130,  80,  30)),
            'puja-accessories':   ((185, 165, 125), (100,  70,  30)),
            'sacred-ingredients': ((235, 225, 205), (150, 120,  60)),
        }
        DEFAULT_PALETTE = ((230, 220, 210), (120, 100, 80))

        SIZE = 400

        def make_image(bg: tuple, fg: tuple, label: str) -> bytes:
            img  = Image.new('RGB', (SIZE, SIZE), bg)
            draw = ImageDraw.Draw(img)

            # Subtle gradient rings
            for r in [180, 140, 100]:
                lighter = tuple(min(255, c + 25) for c in bg)
                draw.ellipse(
                    [SIZE//2 - r, SIZE//2 - r, SIZE//2 + r, SIZE//2 + r],
                    outline=lighter, width=2,
                )

            # Centre filled circle
            cx = SIZE // 2
            draw.ellipse([cx-80, cx-80, cx+80, cx+80], fill=fg)

            # First letter of English name
            letter = label[0].upper() if label else '?'
            draw.text((cx - 12, cx - 18), letter, fill=bg)

            buf = BytesIO()
            img.save(buf, format='JPEG', quality=85)
            buf.seek(0)
            return buf.read()

        count = 0
        for product in Product.objects.filter(is_package=False).prefetch_related('images'):
            if product.images.exists():
                continue

            slug = product.category.slug if product.category else ''
            bg, fg = PALETTE.get(slug, DEFAULT_PALETTE)

            data     = make_image(bg, fg, product.name_en)
            filename = f'{product.sku.lower()}.jpg'

            pi = ProductImage(
                product=product,
                alt_bn=product.name_bn,
                alt_en=product.name_en,
                order=0,
            )
            pi.image.save(filename, ContentFile(data), save=True)
            count += 1

        self.stdout.write(f'  ✓ {count} product images generated')

    def _seed_users(self):
        from api.models import User
        rows = [
            ('admin@pujarighar.local',     '01700000001', 'Admin1234!',  'ADMIN',     'সুপার অ্যাডমিন', 'Super Admin'),
            ('warehouse@pujarighar.local', '01700000002', 'Warehouse1!', 'WAREHOUSE', 'গুদামঘর কর্মী',  'Warehouse Staff'),
            ('delivery@pujarighar.local',  '01700000003', 'Delivery1!',  'DELIVERY',  'ডেলিভারিম্যান',  'Delivery Person'),
            ('customer@pujarighar.local',  '01700000004', 'Customer1!',  'CUSTOMER',  'গ্রাহক',          'Customer'),
        ]
        for email, phone, password, role, name_bn, name_en in rows:
            if not User.objects.filter(email=email).exists():
                user = User.objects.create_user(email=email, phone=phone, password=password, role=role)
                user.profile.full_name_bn = name_bn
                user.profile.full_name_en = name_en
                user.profile.save()
                self.stdout.write(f'  ✓ Created {role}: {email}')
            else:
                self.stdout.write(f'  – Exists: {email}')
