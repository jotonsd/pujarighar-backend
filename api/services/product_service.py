import logging
from decimal import Decimal
from django.db import transaction
from django.db.models import Avg, Case, Count, DecimalField, ExpressionWrapper, F, FloatField, IntegerField, Q, Subquery, OuterRef, Value, When
from django.db.models.functions import Greatest
from django.utils import timezone
from api.models import Account, Brand, Category, Discount, JournalEntry, JournalLine, Product, ProductPackageItem, StockMovement, Supplier
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
        return qs

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

    def list_products(self, category=None, brand=None, search='', is_package=None, min_price=None, max_price=None, include_inactive=False, ordering=None, has_discount=False):
        qs = Product.objects.select_related('category', 'brand').prefetch_related('images', 'package_items')
        qs = self._with_ratings(qs)
        if not include_inactive:
            qs = qs.filter(is_active=True)
        if category:
            ids = [c.strip() for c in category.split(',') if c.strip()]
            qs = qs.filter(category_id__in=ids) if ids else qs
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
        if ordering == 'newest':
            qs = qs.order_by('-created_at')
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
        return qs

    def get_product(self, pk: str) -> Product:
        return self._with_ratings(
            Product.objects.select_related('category', 'brand').prefetch_related('images', 'package_items')
        ).get(pk=pk)

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

    def _get_movement_report(self, movement_type: str, supplier_id: str = '', product_id: str = '',
                              from_date: str = '', to_date: str = '') -> dict:
        qs = StockMovement.objects.filter(movement_type=movement_type).select_related('product', 'supplier')

        if supplier_id:
            qs = qs.filter(supplier_id=supplier_id)
        if product_id:
            qs = qs.filter(product_id=product_id)
        if from_date:
            qs = qs.filter(created_at__gte=local_day_start(from_date))
        if to_date:
            qs = qs.filter(created_at__lt=local_day_end_exclusive(to_date))

        qs = qs.order_by('-created_at')

        rows = [
            {
                'id':              str(m.id),
                'date':            m.created_at.isoformat(),
                'product_id':      str(m.product_id),
                'product_name_bn': m.product.name_bn,
                'product_name_en': m.product.name_en,
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

    def get_purchase_report(self, supplier_id: str = '', product_id: str = '',
                             from_date: str = '', to_date: str = '') -> dict:
        return self._get_movement_report('PURCHASE', supplier_id, product_id, from_date, to_date)

    def get_supplier_return_report(self, supplier_id: str = '', product_id: str = '',
                                    from_date: str = '', to_date: str = '') -> dict:
        return self._get_movement_report('SUPPLIER_RETURN', supplier_id, product_id, from_date, to_date)

    def list_package_items(self, product: Product):
        return ProductPackageItem.objects.filter(package=product).select_related('component')

    def add_package_item(self, package: Product, component_id: str, quantity: Decimal) -> ProductPackageItem:
        component = Product.objects.get(id=component_id, is_active=True)
        item = ProductPackageItem.objects.create(package=package, component=component, quantity=quantity)
        return item

    def delete_package_item(self, item: ProductPackageItem) -> None:
        item.delete()
