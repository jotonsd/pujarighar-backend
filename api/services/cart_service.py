import logging
from decimal import Decimal
from django.db.models import Count, Max, Q
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

    def get_cart_report(self, params: dict) -> dict:
        """Admin visibility into registered customers who've added items to
        their cart but haven't checked out yet — one row per customer, with
        their current cart contents and value. Useful for follow-up (e.g. a
        reminder via the Bulk SMS feature)."""
        carts = (
            Cart.objects.filter(items__isnull=False)
            .select_related('user__profile')
            .prefetch_related('items__product')
            .annotate(item_count=Count('items', distinct=True), last_activity=Max('items__updated_at'))
            .distinct()
        )

        search = params.get('search', '')
        if search:
            carts = carts.filter(
                Q(user__phone__icontains=search)
                | Q(user__email__icontains=search)
                | Q(user__profile__full_name_bn__icontains=search)
                | Q(user__profile__full_name_en__icontains=search)
            )

        rows = []
        total_value = Decimal('0')
        for cart in carts.order_by('-last_activity'):
            items = list(cart.items.all())
            cart_value = sum((i.product.effective_price * i.quantity for i in items), Decimal('0'))
            total_value += cart_value
            rows.append({
                'customer_id':   str(cart.user_id),
                'name_bn':       cart.user.profile.full_name_bn,
                'name_en':       cart.user.profile.full_name_en,
                'phone':         cart.user.phone,
                'email':         cart.user.email,
                'item_count':    len(items),
                'total_quantity': str(sum((i.quantity for i in items), Decimal('0'))),
                'cart_value':    str(cart_value),
                'last_activity': cart.last_activity.isoformat() if cart.last_activity else None,
                'items': [{
                    'product_name_bn': i.product.name_bn,
                    'product_name_en': i.product.name_en,
                    'quantity':        str(i.quantity),
                    'unit_price':      str(i.product.effective_price),
                } for i in items],
            })

        return {
            'rows': rows,
            'total_carts': len(rows),
            'total_value': str(total_value),
        }

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
