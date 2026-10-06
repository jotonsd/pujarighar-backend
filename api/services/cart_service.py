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

    def add_item(self, user, product: Product, quantity: Decimal, color_bn: str = '', color_en: str = '', variant=None) -> Cart:
        cart = self.get_or_create_cart(user)
        # The storefront resolves and passes `variant` directly once a
        # product has variants; color_bn/color_en is the older bridge for
        # callers (AI chatbot) still sending a plain color string.
        if variant is None:
            variant = product.resolve_color_variant(color_bn, color_en)
        # A variant now carries its own real stock — no more pooling across
        # colors. A non-variant product (variant is None) still pools across
        # its (now historical) cart lines of different colors, same as before.
        existing = cart.items.filter(product=product, variant=variant).first()
        total_quantity = (existing.quantity if existing else Decimal('0')) + quantity
        if variant is None:
            other_quantity = sum(
                (i.quantity for i in cart.items.filter(product=product, variant__isnull=True).exclude(pk=existing.pk if existing else None)),
                Decimal('0'),
            )
            self._validate_stock(product, total_quantity + other_quantity, variant=None)
        else:
            self._validate_stock(product, total_quantity, variant=variant)
        if existing:
            existing.quantity = total_quantity
            existing.save(update_fields=['quantity'])
        else:
            CartItem.objects.create(
                cart=cart, product=product, quantity=quantity, variant=variant,
                variant_label_bn=variant.label(True) if variant else '',
                variant_label_en=variant.label(False) if variant else '',
            )
        logger.info(f"Cart item added: user={user.email} product={product.sku} variant={variant.id if variant else None} qty={quantity}")
        return cart

    def update_item(self, cart: Cart, item_id: str, quantity: Decimal) -> Cart:
        item = cart.items.get(pk=item_id)
        if item.variant_id:
            self._validate_stock(item.product, quantity, variant=item.variant)
        else:
            other_quantity = sum(
                (i.quantity for i in cart.items.filter(product=item.product, variant__isnull=True).exclude(pk=item.pk)),
                Decimal('0'),
            )
            self._validate_stock(item.product, quantity + other_quantity, variant=None)
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

    def _validate_stock(self, product: Product, quantity: Decimal, variant=None) -> None:
        if product.is_package:
            for pi in ProductPackageItem.objects.filter(package=product).select_related('component', 'component_variant'):
                needed = pi.quantity * quantity
                target = pi.component_variant if pi.component_variant else pi.component
                if target.stock_on_hand < needed:
                    raise ValidationError({
                        'message_bn': f'{pi.component.name_bn}: পর্যাপ্ত স্টক নেই',
                        'message_en': f'{pi.component.name_en}: Insufficient stock',
                    })
        else:
            target = variant if variant else product
            if target.stock_on_hand < quantity:
                raise ValidationError({
                    'message_bn': 'পর্যাপ্ত স্টক নেই',
                    'message_en': 'Insufficient stock',
                })
