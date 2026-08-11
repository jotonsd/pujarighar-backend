import logging
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from uuid import UUID
from django.db import transaction
from django.db.models import Avg, Case, Count, DecimalField, ExpressionWrapper, F, FloatField, IntegerField, Q, Subquery, OuterRef, Value, When
from django.db.models.functions import Greatest
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from api.models import Account, Brand, Category, Discount, JournalEntry, JournalLine, Product, ProductPackageItem, ProductView, StockMovement, Supplier, PRODUCT_BADGES
from api.utils.dates import local_day_start, local_day_end_exclusive

logger = logging.getLogger(__name__)


class BrandService:

    def list_brands(self, include_inactive=False):
        qs = Brand.objects.all()
        if not include_inactive:
            qs = qs.filter(is_active=True)
        return qs

    def get_brand(self, pk: str) -> Brand:
        return Brand.objects.get(pk=pk)

    def create_brand(self, validated_data: dict) -> Brand:
        return Brand.objects.create(**validated_data)

    def update_brand(self, brand: Brand, validated_data: dict) -> Brand:
        for attr, value in validated_data.items():
            setattr(brand, attr, value)
        brand.save()
        return brand

    def delete_brand(self, brand: Brand) -> None:
        brand.is_active = False
        brand.save(update_fields=['is_active'])


class CategoryService:

    def list_categories(self, parent=None, include_inactive=False):
        qs = Category.objects.all()
        if parent:
            qs = qs.filter(parent_id=parent)
        if not include_inactive:
            qs = qs.filter(is_active=True)
        # Category has an explicit admin-managed `order` field for exactly
        # this — display sequence — rather than any derived ranking.
        return qs.order_by('order', 'name_bn')

    def get_category(self, pk: str) -> Category:
        return Category.objects.get(pk=pk)

    def create_category(self, validated_data: dict) -> Category:
        return Category.objects.create(**validated_data)

    def update_category(self, category: Category, validated_data: dict) -> Category:
        for attr, value in validated_data.items():
            setattr(category, attr, value)
        category.save()
        return category

    def delete_category(self, category: Category) -> None:
        category.is_active = False
        category.save(update_fields=['is_active'])


class ProductService:

    def _with_ratings(self, qs):
        from api.models import Review
        avg_sq = (
            Review.objects.filter(product=OuterRef('pk'), is_approved=True)
            .values('product')
            .annotate(v=Avg('rating'))
            .values('v')
        )
        cnt_sq = (
            Review.objects.filter(product=OuterRef('pk'), is_approved=True)
            .values('product')
            .annotate(v=Count('id'))
            .values('v')
        )
        return qs.annotate(
            average_rating=Subquery(avg_sq, output_field=FloatField()),
            review_count=Subquery(cnt_sq, output_field=IntegerField()),
        )

    def list_products(self, category=None, brand=None, search='', is_package=None, min_price=None, max_price=None, include_inactive=False, ordering=None, has_discount=False, is_active=None, badges=None, personalize_user=None, personalize_guest_id=''):
        qs = Product.objects.select_related('category', 'brand').prefetch_related('images', 'package_items')
        qs = self._with_ratings(qs)
        if is_active is not None:
            qs = qs.filter(is_active=str(is_active).lower() == 'true')
        elif not include_inactive:
            qs = qs.filter(is_active=True)
        if category:
            tokens = [c.strip() for c in category.split(',') if c.strip()]
            # Accepts either category UUIDs (admin product list still filters
            # this way) or slugs (the public storefront links to categories
            # by slug for readable/shareable URLs) in the same param.
            ids, slugs = [], []
            for token in tokens:
                try:
                    UUID(token)
                    ids.append(token)
                except ValueError:
                    slugs.append(token)
            if ids or slugs:
                cond = Q()
                if ids:   cond |= Q(category_id__in=ids)
                if slugs: cond |= Q(category__slug__in=slugs)
                qs = qs.filter(cond)
        if brand:
            ids = [b.strip() for b in brand.split(',') if b.strip()]
            qs = qs.filter(brand_id__in=ids) if ids else qs
        if search:
            qs = qs.filter(Q(name_bn__icontains=search) | Q(name_en__icontains=search) | Q(sku__icontains=search))
        if is_package is not None:
            qs = qs.filter(is_package=str(is_package).lower() == 'true')
        if min_price is not None:
            qs = qs.filter(unit_price__gte=min_price)
        if max_price is not None:
            qs = qs.filter(unit_price__lte=max_price)
        if has_discount:
            today = timezone.now().date()
            qs = qs.filter(
                discounts__is_active=True,
            ).filter(
                Q(discounts__start_date__isnull=True) | Q(discounts__start_date__lte=today),
                Q(discounts__end_date__isnull=True)   | Q(discounts__end_date__gte=today),
            ).distinct()
        if badges:
            wanted = [b.strip() for b in badges.split(',') if b.strip() in PRODUCT_BADGES]
            if wanted:
                cond = Q()
                for b in wanted:
                    cond |= Q(badges__contains=[b])
                qs = qs.filter(cond)
        if ordering == 'newest':
            # "New Released" is the New badge, not just recency — only
            # products the admin has actually tagged 'new' show up here,
            # ordered by creation date among themselves.
            qs = qs.filter(badges__contains=['new']).order_by('-created_at')
        elif ordering in ('price_asc', 'price_desc'):
            disc_type = Subquery(
                Discount.objects.filter(product=OuterRef('pk'), is_active=True)
                .values('discount_type')[:1]
            )
            disc_val = Subquery(
                Discount.objects.filter(product=OuterRef('pk'), is_active=True)
                .values('discount_value')[:1]
            )
            qs = qs.annotate(_disc_type=disc_type, _disc_val=disc_val).annotate(
                _effective_price=Case(
                    When(_disc_type='PERCENTAGE', then=ExpressionWrapper(
                        F('unit_price') - F('unit_price') * F('_disc_val') / Value(Decimal('100')),
                        output_field=DecimalField(max_digits=12, decimal_places=2),
                    )),
                    When(_disc_type='FLAT', then=ExpressionWrapper(
                        Greatest(Value(Decimal('0')), F('unit_price') - F('_disc_val')),
                        output_field=DecimalField(max_digits=12, decimal_places=2),
                    )),
                    default=F('unit_price'),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                )
            )
            qs = qs.order_by('_effective_price' if ordering == 'price_asc' else '-_effective_price')
        elif ordering in ('discount_asc', 'discount_desc'):
            disc_type = Subquery(
                Discount.objects.filter(product=OuterRef('pk'), is_active=True)
                .values('discount_type')[:1]
            )
            disc_val = Subquery(
                Discount.objects.filter(product=OuterRef('pk'), is_active=True)
                .values('discount_value')[:1]
            )
            qs = qs.annotate(_disc_type=disc_type, _disc_val=disc_val).annotate(
                _disc_amount=Case(
                    When(_disc_type='PERCENTAGE', then=ExpressionWrapper(
                        F('unit_price') * F('_disc_val') / Value(Decimal('100')),
                        output_field=DecimalField(max_digits=12, decimal_places=2),
                    )),
                    When(_disc_type='FLAT', then=ExpressionWrapper(
                        F('_disc_val'),
                        output_field=DecimalField(max_digits=12, decimal_places=2),
                    )),
                    default=Value(Decimal('0')),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                )
            ).order_by(
                '_disc_amount' if ordering == 'discount_asc' else '-_disc_amount',
            )
        elif personalize_user or personalize_guest_id:
            # No explicit sort chosen. Logged-in customers: rank by their own
            # "most visited" category affinity first (same signal behind
            # "Recommended for You" — see get_recommended_products), then by
            # badge priority (new > flash_sale > trendy). Guests skip straight
            # to the badge-priority ranking. Either way, falls back to the
            # model's default ordering (-created_at) as the final tiebreak.
            qs = self._personalize_ordering(qs, personalize_user)
        return qs

    def _personalize_ordering(self, qs, user):
        order_by = []

        if user:
            since = timezone.now() - timedelta(days=90)
            cat_weight = defaultdict(int)
            views = ProductView.objects.filter(user=user, created_at__gte=since).select_related('product')
            for v in views:
                if v.product and v.product.category_id:
                    cat_weight[v.product.category_id] += 1
            if cat_weight:
                affinity = Case(
                    *[When(category_id=cid, then=Value(weight)) for cid, weight in cat_weight.items()],
                    default=Value(0),
                    output_field=IntegerField(),
                )
                qs = qs.annotate(_affinity=affinity)
                order_by.append('-_affinity')

        badge_priority = Case(
            When(badges__contains=['new'], then=Value(3)),
            When(badges__contains=['flash_sale'], then=Value(2)),
            When(badges__contains=['trendy'], then=Value(1)),
            default=Value(0),
            output_field=IntegerField(),
        )
        qs = qs.annotate(_badge_priority=badge_priority)
        order_by.append('-_badge_priority')

        return qs.order_by(*order_by, '-created_at')

    def get_recommended_products(self, user, guest_id: str, limit: int = 12):
        """Personalized picks for this visitor: find every category they've
        personally viewed a product in, then pull the top 3 globally
        most-visited products from each of those categories. Categories are
        ordered by how often this visitor viewed products in them (their
        most-visited category first); products within a category are ranked
        by site-wide view count, not just this visitor's own. Returns an
        empty queryset for visitors with no view history yet — the frontend
        simply hides the section in that case.
        """
        if not user and not guest_id:
            return Product.objects.none()

        PER_CATEGORY = 3

        identity = Q(user=user) if user else Q(guest_id=guest_id)
        since = timezone.now() - timedelta(days=90)

        cat_own_weight = defaultdict(int)
        views = ProductView.objects.filter(identity, created_at__gte=since).select_related('product')
        for v in views:
            if v.product and v.product.category_id:
                cat_own_weight[v.product.category_id] += 1

        if not cat_own_weight:
            return Product.objects.none()

        ordered_category_ids = [cid for cid, _ in sorted(cat_own_weight.items(), key=lambda kv: -kv[1])]

        result_ids = []
        for cid in ordered_category_ids:
            top_ids = (
                Product.objects.filter(is_active=True, is_package=False, category_id=cid)
                .annotate(_global_views=Count('view_logs'))
                .order_by('-_global_views', '-created_at')
                .values_list('id', flat=True)[:PER_CATEGORY]
            )
            result_ids.extend(top_ids)
            if len(result_ids) >= limit:
                break
        result_ids = result_ids[:limit]

        if not result_ids:
            return Product.objects.none()

        qs = Product.objects.filter(id__in=result_ids).select_related('category', 'brand').prefetch_related('images', 'package_items')
        qs = self._with_ratings(qs)

        # Preserve the category-then-popularity order computed above.
        preserve = Case(
            *[When(id=pid, then=Value(i)) for i, pid in enumerate(result_ids)],
            output_field=IntegerField(),
        )
        return qs.order_by(preserve)

    def get_product(self, pk: str) -> Product:
        return self._with_ratings(
            Product.objects.select_related('category', 'brand').prefetch_related('images', 'package_items__component__images')
        ).get(pk=pk)

    def get_product_by_slug(self, slug: str) -> Product:
        return self._with_ratings(
            Product.objects.select_related('category', 'brand').prefetch_related('images', 'package_items__component__images')
        ).get(slug=slug, is_active=True)

    def create_product(self, validated_data: dict) -> Product:
        product = Product.objects.create(**validated_data)
        logger.info(f"Product created: {product.sku}")
        return product

    def update_product(self, product: Product, validated_data: dict) -> Product:
        for attr, value in validated_data.items():
            setattr(product, attr, value)
        product.save()
        return product

    def delete_product(self, product: Product) -> None:
        product.is_active = False
        product.save(update_fields=['is_active'])


class StockService:

    def get_stock_detail(self, product: Product) -> dict:
        movements = StockMovement.objects.filter(product=product).select_related('created_by')[:20]
        return {
            'stock_on_hand': str(product.stock_on_hand),
            'movements':     movements,
        }

    def adjust_stock(self, product: Product, movement_type: str, quantity: Decimal,
                     note_bn: str, note_en: str, user,
                     unit_cost: Decimal = Decimal('0'),
                     unit_price: Decimal = None,
                     supplier_id: str = None,
                     supplier_name: str = '',
                     payment_method: str = 'CASH') -> StockMovement:
        supplier = None
        if supplier_id:
            try:
                supplier = Supplier.objects.get(pk=supplier_id)
            except Supplier.DoesNotExist:
                pass

        # Stock going back to a supplier always reduces stock on hand, regardless
        # of the sign the caller sent — the form only asks "how much".
        if movement_type == 'SUPPLIER_RETURN':
            quantity = -abs(quantity)

        movement = StockMovement(
            product=product, movement_type=movement_type,
            quantity=quantity, unit_cost=unit_cost,
            supplier=supplier,
            supplier_name=supplier_name if not supplier else (supplier.name_bn or supplier.name_en),
            payment_method=payment_method if movement_type in ('PURCHASE', 'SUPPLIER_RETURN') else 'CASH',
            note_bn=note_bn, note_en=note_en, created_by=user,
        )
        movement.clean()
        movement.save()

        if movement_type == 'PURCHASE' and unit_cost > 0:
            product.cost_price = unit_cost
            if unit_price is not None and unit_price > 0:
                product.unit_price = unit_price
                product.save(update_fields=['cost_price', 'unit_price'])
            else:
                product.save(update_fields=['cost_price'])
            self._create_purchase_journal(product, quantity, unit_cost, movement, user, payment_method)
        elif movement_type == 'SUPPLIER_RETURN' and unit_cost > 0:
            self._create_supplier_return_journal(product, abs(quantity), unit_cost, movement, user, payment_method)

        logger.info(f"Stock adjusted: {product.sku} {movement_type} {quantity}")
        return movement

    @transaction.atomic
    def update_stock_movement(self, movement: StockMovement, data: dict) -> StockMovement:
        """Correct a mistaken purchase/supplier-return entry in place — updates
        the movement AND, if one was posted, the linked JournalEntry's lines
        (re-pointing the cash/payable side if payment_method changed too), so
        the ledger stays in sync with the corrected numbers. Deliberately
        in-place (no reversal/audit-trail entry) per explicit product
        decision — scoped to PURCHASE/SUPPLIER_RETURN only, since those are
        the only movement types that ever post a 1:1-linked journal entry
        (see _create_purchase_journal/_create_supplier_return_journal); SALE
        and RETURN movements come from orders and have no such link to fix.
        """
        if movement.movement_type not in ('PURCHASE', 'SUPPLIER_RETURN'):
            raise ValidationError({
                'message_bn': 'শুধুমাত্র ক্রয় বা সরবরাহকারীকে ফেরত এন্ট্রি সম্পাদনা করা যায়',
                'message_en': 'Only purchase or supplier-return entries can be edited',
            })

        product = movement.product
        old_quantity = movement.quantity

        new_quantity = data.get('quantity', movement.quantity)
        if movement.movement_type == 'SUPPLIER_RETURN':
            new_quantity = -abs(new_quantity)

        # Edit-safe stock check — movement.clean()'s own check assumes a
        # not-yet-saved row (stock_on_hand doesn't include it yet), but here
        # the OLD quantity is already counted in stock_on_hand, so we swap it
        # for the new one rather than just adding the new one on top.
        projected = product.stock_on_hand - old_quantity + new_quantity
        if projected < 0:
            raise ValidationError({
                'message_bn': 'পর্যাপ্ত স্টক নেই',
                'message_en': 'Insufficient stock',
            })

        movement.quantity = new_quantity
        if 'unit_cost' in data:
            movement.unit_cost = data['unit_cost']
        if 'payment_method' in data:
            movement.payment_method = data['payment_method']
        if 'supplier_id' in data:
            movement.supplier = Supplier.objects.filter(pk=data['supplier_id']).first() if data['supplier_id'] else None
        if 'supplier_name' in data:
            movement.supplier_name = data['supplier_name']
        if 'note_bn' in data:
            movement.note_bn = data['note_bn']
        if 'note_en' in data:
            movement.note_en = data['note_en']
        movement.save()

        self._sync_movement_journal(movement)

        # Only the most recent PURCHASE for this product drives its current
        # cost/price — an older one being corrected shouldn't overwrite a
        # price a newer purchase already superseded.
        if movement.movement_type == 'PURCHASE':
            latest = product.stock_movements.filter(movement_type='PURCHASE').order_by('-created_at').first()
            if latest and latest.id == movement.id:
                product.cost_price = movement.unit_cost
                if data.get('unit_price'):
                    product.unit_price = data['unit_price']
                    product.save(update_fields=['cost_price', 'unit_price'])
                else:
                    product.save(update_fields=['cost_price'])

        logger.info(f"Stock movement corrected: {movement.id} ({product.sku} {movement.movement_type})")
        return movement

    def _sync_movement_journal(self, movement: StockMovement) -> None:
        entry = JournalEntry.objects.filter(
            reference_type=movement.movement_type, reference_id=movement.id,
        ).prefetch_related('lines__account').first()
        if not entry:
            return
        lines = list(entry.lines.all())
        if len(lines) != 2:
            logger.warning(f"Skipped journal sync for movement {movement.id}: expected 2 lines, found {len(lines)}")
            return

        total = movement.unit_cost * abs(movement.quantity)
        variable_code = '1000' if movement.payment_method == 'CASH' else '2000'
        variable_acct = Account.objects.filter(code=variable_code).first()
        inventory_is_debit = movement.movement_type == 'PURCHASE'

        for line in lines:
            if line.account.code == '1300':
                line.debit  = total if inventory_is_debit else Decimal('0')
                line.credit = Decimal('0') if inventory_is_debit else total
            else:
                if variable_acct:
                    line.account = variable_acct
                line.debit  = Decimal('0') if inventory_is_debit else total
                line.credit = total if inventory_is_debit else Decimal('0')
            line.save()

    def _create_purchase_journal(self, product: Product, quantity: Decimal,
                                  unit_cost: Decimal, movement: StockMovement, user,
                                  payment_method: str = 'CASH') -> None:
        today        = timezone.now().date()
        prefix       = f'JE-{today:%Y%m%d}-'
        last         = JournalEntry.objects.filter(entry_number__startswith=prefix).count()
        entry_number = f'{prefix}{last + 1:04d}'
        total_cost   = unit_cost * quantity
        credit_acct  = '1000' if payment_method == 'CASH' else '2000'

        entry = JournalEntry.objects.create(
            entry_number=entry_number, reference_type='PURCHASE',
            reference_id=movement.id,
            description_bn=f'স্টক ক্রয় — {product.name_bn}',
            description_en=f'Stock Purchase — {product.name_en}',
            created_by=user, is_posted=True,
        )

        def _acct(code):
            try:
                return Account.objects.get(code=code)
            except Account.DoesNotExist:
                return None

        for code, debit, credit in [
            ('1300',       total_cost,   Decimal('0')),  # Dr Inventory
            (credit_acct,  Decimal('0'), total_cost),    # Cr Cash or Accounts Payable
        ]:
            acct = _acct(code)
            if acct and (debit or credit):
                JournalLine.objects.create(
                    journal_entry=entry, account=acct, debit=debit, credit=credit,
                )

    def _create_supplier_return_journal(self, product: Product, quantity: Decimal,
                                         unit_cost: Decimal, movement: StockMovement, user,
                                         payment_method: str = 'CASH') -> None:
        """Mirror image of the purchase journal: stock leaves inventory, and we
        either get cash back or owe the supplier less (Accounts Payable shrinks).
        """
        today        = timezone.now().date()
        prefix       = f'JE-{today:%Y%m%d}-'
        last         = JournalEntry.objects.filter(entry_number__startswith=prefix).count()
        entry_number = f'{prefix}{last + 1:04d}'
        total_value  = unit_cost * quantity
        debit_acct   = '1000' if payment_method == 'CASH' else '2000'

        entry = JournalEntry.objects.create(
            entry_number=entry_number, reference_type='SUPPLIER_RETURN',
            reference_id=movement.id,
            description_bn=f'সরবরাহকারীকে স্টক ফেরত — {product.name_bn}',
            description_en=f'Stock Returned to Supplier — {product.name_en}',
            created_by=user, is_posted=True,
        )

        def _acct(code):
            try:
                return Account.objects.get(code=code)
            except Account.DoesNotExist:
                return None

        for code, debit, credit in [
            (debit_acct, total_value,   Decimal('0')),  # Dr Cash or Accounts Payable
            ('1300',     Decimal('0'), total_value),    # Cr Inventory
        ]:
            acct = _acct(code)
            if acct and (debit or credit):
                JournalLine.objects.create(
                    journal_entry=entry, account=acct, debit=debit, credit=credit,
                )

    def _get_movement_report(self, movement_type: str, request=None, supplier_id: str = '', product_id: str = '',
                              from_date: str = '', to_date: str = '') -> dict:
        from django.db.models import Prefetch
        from api.models import ProductImage

        qs = StockMovement.objects.filter(movement_type=movement_type).select_related('product', 'supplier').prefetch_related(
            Prefetch('product__images', queryset=ProductImage.objects.order_by('order')),
        )

        if supplier_id:
            qs = qs.filter(supplier_id=supplier_id)
        if product_id:
            qs = qs.filter(product_id=product_id)
        if from_date:
            qs = qs.filter(created_at__gte=local_day_start(from_date))
        if to_date:
            qs = qs.filter(created_at__lt=local_day_end_exclusive(to_date))

        qs = qs.order_by('-created_at')

        def _image_url(product):
            images = list(product.images.all())
            if not images:
                return None
            url = images[0].image.url
            return request.build_absolute_uri(url) if request else url

        rows = [
            {
                'id':              str(m.id),
                'date':            m.created_at.isoformat(),
                'product_id':      str(m.product_id),
                'product_name_bn': m.product.name_bn,
                'product_name_en': m.product.name_en,
                'product_image':   _image_url(m.product),
                'sku':             m.product.sku,
                'quantity':        str(abs(m.quantity)),
                'unit_cost':       str(m.unit_cost),
                'line_total':      str(m.unit_cost * abs(m.quantity)),
                'supplier_name':   (m.supplier.name_bn or m.supplier.name_en) if m.supplier else (m.supplier_name or ''),
                'payment_method':  m.payment_method,
            }
            for m in qs
        ]

        total_quantity = sum((abs(m.quantity) for m in qs), Decimal('0'))
        total_amount   = sum((m.unit_cost * abs(m.quantity) for m in qs), Decimal('0'))

        return {
            'rows':           rows,
            'total_quantity': str(total_quantity),
            'total_amount':   str(total_amount),
        }

    def get_purchase_report(self, request=None, supplier_id: str = '', product_id: str = '',
                             from_date: str = '', to_date: str = '') -> dict:
        return self._get_movement_report('PURCHASE', request, supplier_id, product_id, from_date, to_date)

    def get_supplier_return_report(self, request=None, supplier_id: str = '', product_id: str = '',
                                    from_date: str = '', to_date: str = '') -> dict:
        return self._get_movement_report('SUPPLIER_RETURN', request, supplier_id, product_id, from_date, to_date)

    def list_package_items(self, product: Product):
        return ProductPackageItem.objects.filter(package=product).select_related('component')

    def add_package_item(self, package: Product, component_id: str, quantity: Decimal) -> ProductPackageItem:
        component = Product.objects.get(id=component_id, is_active=True)
        item = ProductPackageItem.objects.create(package=package, component=component, quantity=quantity)
        return item

    def delete_package_item(self, item: ProductPackageItem) -> None:
        item.delete()
