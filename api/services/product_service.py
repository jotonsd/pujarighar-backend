import logging
from decimal import Decimal
from django.db.models import Q
from api.models import Category, Product, ProductPackageItem, StockMovement

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

    def list_products(self, category=None, search='', is_package=None, min_price=None, max_price=None, include_inactive=False):
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
                     note_bn: str, note_en: str, user) -> StockMovement:
        movement = StockMovement(
            product=product, movement_type=movement_type,
            quantity=quantity, note_bn=note_bn, note_en=note_en, created_by=user,
        )
        movement.clean()
        movement.save()
        logger.info(f"Stock adjusted: {product.sku} {movement_type} {quantity}")
        return movement

    def list_package_items(self, product: Product):
        return ProductPackageItem.objects.filter(package=product).select_related('component')

    def add_package_item(self, package: Product, component_id: str, quantity: Decimal) -> ProductPackageItem:
        component = Product.objects.get(id=component_id, is_active=True)
        item = ProductPackageItem.objects.create(package=package, component=component, quantity=quantity)
        return item

    def delete_package_item(self, item: ProductPackageItem) -> None:
        item.delete()
