import logging
from decimal import Decimal
from django.db import transaction
from django.db.models import Case, DecimalField, ExpressionWrapper, F, Q, Subquery, OuterRef, Value, When
from django.db.models.functions import Greatest
from django.utils import timezone
from api.models import Account, Category, Discount, JournalEntry, JournalLine, Product, ProductPackageItem, StockMovement

logger = logging.getLogger(__name__)


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

    def list_products(self, category=None, search='', is_package=None, min_price=None, max_price=None, include_inactive=False, ordering=None, has_discount=False):
        qs = Product.objects.select_related('category').prefetch_related('images', 'package_items')
        if not include_inactive:
            qs = qs.filter(is_active=True)
        if category:
            ids = [c.strip() for c in category.split(',') if c.strip()]
            qs = qs.filter(category_id__in=ids) if ids else qs
        if search:
            qs = qs.filter(Q(name_bn__icontains=search) | Q(name_en__icontains=search) | Q(sku__icontains=search))
        if is_package is not None:
            qs = qs.filter(is_package=str(is_package).lower() == 'true')
        if min_price is not None:
            qs = qs.filter(unit_price__gte=min_price)
        if max_price is not None:
            qs = qs.filter(unit_price__lte=max_price)
        if has_discount:
            qs = qs.filter(discounts__is_active=True).distinct()
        if ordering in ('price_asc', 'price_desc'):
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
        return qs

    def get_product(self, pk: str) -> Product:
        return Product.objects.select_related('category').prefetch_related('images', 'package_items').get(pk=pk)

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
                     unit_price: Decimal = None) -> StockMovement:
        movement = StockMovement(
            product=product, movement_type=movement_type,
            quantity=quantity, unit_cost=unit_cost,
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
            self._create_purchase_journal(product, quantity, unit_cost, movement, user)

        logger.info(f"Stock adjusted: {product.sku} {movement_type} {quantity}")
        return movement

    def _create_purchase_journal(self, product: Product, quantity: Decimal,
                                  unit_cost: Decimal, movement: StockMovement, user) -> None:
        today        = timezone.now().date()
        prefix       = f'JE-{today:%Y%m%d}-'
        last         = JournalEntry.objects.filter(entry_number__startswith=prefix).count()
        entry_number = f'{prefix}{last + 1:04d}'
        total_cost   = unit_cost * quantity

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
            ('1300', total_cost,        Decimal('0')),   # DR Inventory
            ('1000', Decimal('0'),      total_cost),     # CR Cash
        ]:
            acct = _acct(code)
            if acct and (debit or credit):
                JournalLine.objects.create(
                    journal_entry=entry, account=acct, debit=debit, credit=credit,
                )

    def list_package_items(self, product: Product):
        return ProductPackageItem.objects.filter(package=product).select_related('component')

    def add_package_item(self, package: Product, component_id: str, quantity: Decimal) -> ProductPackageItem:
        component = Product.objects.get(id=component_id, is_active=True)
        item = ProductPackageItem.objects.create(package=package, component=component, quantity=quantity)
        return item

    def delete_package_item(self, item: ProductPackageItem) -> None:
        item.delete()
