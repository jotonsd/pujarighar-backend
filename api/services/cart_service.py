import logging
from decimal import Decimal
from rest_framework.exceptions import ValidationError
from api.models import Cart, CartItem, Product, ProductPackageItem

logger = logging.getLogger(__name__)


class CartService:

    def get_or_create_cart(self, user) -> Cart:
        cart, _ = Cart.objects.get_or_create(user=user)
        return cart

    def add_item(self, user, product: Product, quantity: Decimal) -> Cart:
        self._validate_stock(product, quantity)
        cart = self.get_or_create_cart(user)
        item, created = CartItem.objects.get_or_create(
            cart=cart, product=product, defaults={'quantity': quantity}
        )
        if not created:
            item.quantity += quantity
            item.save(update_fields=['quantity'])
        logger.info(f"Cart item added: user={user.email} product={product.sku} qty={quantity}")
        return cart

    def update_item(self, cart: Cart, item_id: str, quantity: Decimal) -> Cart:
        item = cart.items.get(pk=item_id)
        self._validate_stock(item.product, quantity)
        item.quantity = quantity
        item.save(update_fields=['quantity'])
        return cart

    def remove_item(self, item_id: str) -> None:
        CartItem.objects.filter(pk=item_id).delete()

    def clear_cart(self, cart: Cart) -> None:
        cart.items.all().delete()

    def _validate_stock(self, product: Product, quantity: Decimal) -> None:
        if product.is_package:
            for pi in ProductPackageItem.objects.filter(package=product).select_related('component'):
                needed = pi.quantity * quantity
                if pi.component.stock_on_hand < needed:
                    raise ValidationError({
                        'message_bn': f'{pi.component.name_bn}: পর্যাপ্ত স্টক নেই',
                        'message_en': f'{pi.component.name_en}: Insufficient stock',
                    })
        else:
            if product.stock_on_hand < quantity:
                raise ValidationError({
                    'message_bn': 'পর্যাপ্ত স্টক নেই',
                    'message_en': 'Insufficient stock',
                })
