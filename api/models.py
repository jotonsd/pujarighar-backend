from decimal import Decimal
from uuid import uuid4
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


# ─── Base ─────────────────────────────────────────────────────────────────────

class BaseModel(models.Model):
    id         = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


# ─── Accounts ─────────────────────────────────────────────────────────────────

ROLE_CHOICES = [
    ('ADMIN',     'অ্যাডমিন'),
    ('WAREHOUSE', 'গুদামঘর কর্মী'),
    ('DELIVERY',  'ডেলিভারিম্যান'),
    ('CUSTOMER',  'গ্রাহক'),
]


class UserManager(BaseUserManager):
    def create_user(self, email, phone, password=None, **extra):
        if not email:
            raise ValueError('Email is required')
        user = self.model(email=self.normalize_email(email), phone=phone, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, phone, password=None, **extra):
        extra.setdefault('role', 'ADMIN')
        extra.setdefault('is_staff', True)
        extra.setdefault('is_superuser', True)
        return self.create_user(email, phone, password, **extra)


class User(AbstractUser):
    id                 = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    username           = None
    email              = models.EmailField(unique=True)
    phone              = models.CharField(max_length=15, unique=True)
    role               = models.CharField(max_length=20, choices=ROLE_CHOICES, default='CUSTOMER')
    preferred_language = models.CharField(
        max_length=5,
        choices=[('bn', 'বাংলা'), ('en', 'English')],
        default='bn',
    )
    is_active   = models.BooleanField(default=True)
    date_joined = models.DateTimeField(auto_now_add=True)

    USERNAME_FIELD  = 'email'
    REQUIRED_FIELDS = ['phone']
    objects         = UserManager()

    def __str__(self):
        return self.email


class Profile(models.Model):
    user         = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    full_name_bn = models.CharField(max_length=200, blank=True)
    full_name_en = models.CharField(max_length=200, blank=True)
    avatar       = models.ImageField(upload_to='avatars/', null=True, blank=True)
    address_bn   = models.TextField(blank=True)
    address_en   = models.TextField(blank=True)
    district     = models.CharField(max_length=100, blank=True)
    thana        = models.CharField(max_length=100, blank=True)
    post_code         = models.CharField(max_length=10, blank=True)
    cashback_balance  = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    created_at        = models.DateTimeField(auto_now_add=True)
    updated_at        = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.full_name_bn or self.full_name_en}'


# ─── Products ─────────────────────────────────────────────────────────────────

class Category(BaseModel):
    name_bn   = models.CharField(max_length=200)
    name_en   = models.CharField(max_length=200)
    slug      = models.SlugField(unique=True)
    parent    = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='children')
    icon      = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name_bn']

    def __str__(self):
        return self.name_bn or self.name_en


class Brand(BaseModel):
    name_bn   = models.CharField(max_length=200)
    name_en   = models.CharField(max_length=200)
    slug      = models.SlugField(unique=True)
    logo      = models.ImageField(upload_to='brands/', blank=True, null=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name_bn']

    def __str__(self):
        return self.name_bn or self.name_en


class Supplier(BaseModel):
    name_bn   = models.CharField(max_length=200)
    name_en   = models.CharField(max_length=200, blank=True)
    phone     = models.CharField(max_length=15, blank=True)
    address   = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name_bn']

    def __str__(self):
        return self.name_bn or self.name_en


class Product(BaseModel):
    name_bn        = models.CharField(max_length=300)
    name_en        = models.CharField(max_length=300)
    description_bn = models.TextField(blank=True)
    description_en = models.TextField(blank=True)
    sku            = models.CharField(max_length=100, unique=True)
    category       = models.ForeignKey(Category, on_delete=models.PROTECT, related_name='products')
    brand          = models.ForeignKey('Brand', null=True, blank=True, on_delete=models.SET_NULL, related_name='products')
    unit_price     = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cost_price     = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    unit_bn        = models.CharField(max_length=50, default='পিস')
    unit_en        = models.CharField(max_length=50, default='piece')
    is_package       = models.BooleanField(default=False)
    discount_type    = models.CharField(
        max_length=12,
        choices=[('NONE', 'None'), ('PERCENTAGE', 'Percentage'), ('FLAT', 'Flat')],
        default='NONE',
    )
    discount_value   = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_active        = models.BooleanField(default=True)

    class Meta:
        ordering = ['name_bn']

    def __str__(self):
        return f'{self.name_bn} ({self.sku})'

    @property
    def effective_price(self) -> Decimal:
        today = timezone.now().date()
        active = (
            self.discounts
            .filter(
                is_active=True,
            )
            .filter(
                models.Q(start_date__isnull=True) | models.Q(start_date__lte=today)
            )
            .filter(
                models.Q(end_date__isnull=True) | models.Q(end_date__gte=today)
            )
            .order_by('-created_at')
            .first()
        )
        if active:
            if active.discount_type == 'PERCENTAGE':
                return (self.unit_price * (1 - active.discount_value / 100)).quantize(Decimal('0.01'))
            if active.discount_type == 'FLAT':
                return max(Decimal('0'), self.unit_price - active.discount_value)
        return self.unit_price

    @property
    def stock_on_hand(self) -> Decimal:
        if self.is_package:
            # Package stock = max whole packages assembable from component stock
            items = self.package_items.select_related('component').all()
            if not items.exists():
                return Decimal('0')
            available = []
            for item in items:
                if item.quantity > 0:
                    available.append(item.component.stock_on_hand // item.quantity)
            return min(available) if available else Decimal('0')

        from django.db.models import Sum
        result = self.stock_movements.aggregate(total=Sum('quantity'))
        return result['total'] or Decimal('0')


class ProductImage(BaseModel):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='images')
    image   = models.ImageField(upload_to='products/')
    alt_bn  = models.CharField(max_length=200, blank=True)
    alt_en  = models.CharField(max_length=200, blank=True)
    order   = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order']


class ProductPackageItem(models.Model):
    id        = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    package   = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='package_items')
    component = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='used_in_packages')
    quantity  = models.DecimalField(max_digits=10, decimal_places=3)

    class Meta:
        unique_together = [['package', 'component']]

    def clean(self):
        if not self.package.is_package:
            raise ValidationError('package must have is_package=True')
        if self.component.is_package:
            raise ValidationError('Nested packages are not allowed')


class StockMovement(models.Model):
    MOVEMENT_TYPES = [
        ('PURCHASE',   'ক্রয়'),
        ('SALE',       'বিক্রয়'),
        ('RETURN',     'ফেরত'),
        ('ADJUSTMENT', 'সমন্বয়'),
    ]
    PAYMENT_METHODS = [
        ('CASH',   'নগদ'),
        ('CREDIT', 'বাকিতে'),
    ]

    id             = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    product        = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='stock_movements')
    movement_type  = models.CharField(max_length=20, choices=MOVEMENT_TYPES)
    quantity       = models.DecimalField(max_digits=12, decimal_places=3)
    unit_cost      = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    supplier       = models.ForeignKey('Supplier', null=True, blank=True, on_delete=models.SET_NULL)
    supplier_name  = models.CharField(max_length=200, blank=True)
    payment_method = models.CharField(max_length=10, choices=PAYMENT_METHODS, default='CASH')
    reference_id   = models.UUIDField(null=True, blank=True)
    note_bn        = models.TextField(blank=True)
    note_en        = models.TextField(blank=True)
    created_by     = models.ForeignKey(User, on_delete=models.PROTECT)
    created_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def clean(self):
        if self.quantity < 0:
            if self.product.stock_on_hand + self.quantity < 0:
                raise ValidationError({
                    'message_bn': 'পর্যাপ্ত স্টক নেই',
                    'message_en': 'Insufficient stock',
                })


# ─── Supplier Payments ───────────────────────────────────────────────────────

class SupplierPayment(BaseModel):
    supplier   = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name='payments')
    amount     = models.DecimalField(max_digits=12, decimal_places=2)
    paid_date  = models.DateField()
    note       = models.TextField(blank=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='supplier_payments')

    class Meta:
        ordering = ['-paid_date', '-created_at']

    def __str__(self):
        return f'{self.supplier.name_bn} — {self.amount} ({self.paid_date})'


# ─── Shipping Addresses ───────────────────────────────────────────────────────

class ShippingAddress(BaseModel):
    user         = models.ForeignKey(User, on_delete=models.CASCADE, related_name='shipping_addresses')
    label        = models.CharField(max_length=100, blank=True)
    full_name_bn = models.CharField(max_length=200)
    full_name_en = models.CharField(max_length=200, blank=True)
    phone        = models.CharField(max_length=15)
    address_bn   = models.TextField()
    address_en   = models.TextField(blank=True)
    district     = models.CharField(max_length=100, blank=True)
    thana        = models.CharField(max_length=100, blank=True)
    post_code    = models.CharField(max_length=10, blank=True)
    is_default   = models.BooleanField(default=False)

    class Meta:
        ordering = ['-is_default', '-created_at']

    def __str__(self):
        return f'{self.full_name_bn} — {self.address_bn[:40]}'

    def set_as_default(self):
        ShippingAddress.objects.filter(user=self.user, is_default=True).update(is_default=False)
        self.is_default = True
        self.save(update_fields=['is_default', 'updated_at'])


# ─── Cart ─────────────────────────────────────────────────────────────────────

class Cart(BaseModel):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='cart')

    def __str__(self):
        return f'Cart — {self.user.email}'


class CartItem(BaseModel):
    cart     = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name='items')
    product  = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.DecimalField(max_digits=10, decimal_places=3)

    class Meta:
        unique_together = [['cart', 'product']]
        ordering        = ['created_at']

    def __str__(self):
        return f'{self.product.name_bn} × {self.quantity}'


# ─── Orders ───────────────────────────────────────────────────────────────────

ORDER_STATUS = [
    ('PENDING',    'পেন্ডিং'),
    ('CONFIRMED',  'নিশ্চিত'),
    ('PACKED',     'প্যাক হয়েছে'),
    ('ASSIGNED',   'ডেলিভারিম্যান নির্ধারিত'),
    ('ON_THE_WAY', 'পথে আছে'),
    ('DELIVERED',  'ডেলিভারি হয়েছে'),
    ('RETURNED',   'ফেরত'),
    ('CANCELLED',  'বাতিল'),
]

PAYMENT_METHOD_CHOICES = [
    ('COD',    'ক্যাশ অন ডেলিভারি'),
    ('ONLINE', 'অনলাইন পেমেন্ট'),
]

PAYMENT_STATUS_CHOICES = [
    ('UNPAID', 'অপরিশোধিত'),
    ('PAID',   'পরিশোধিত'),
]

ALLOWED_TRANSITIONS = {
    'PENDING':    ['CONFIRMED', 'CANCELLED'],
    'CONFIRMED':  ['PACKED',    'CANCELLED'],
    'PACKED':     ['ASSIGNED',  'CANCELLED'],
    'ASSIGNED':   ['ON_THE_WAY'],
    'ON_THE_WAY': ['DELIVERED'],
    'DELIVERED':  ['RETURNED'],
}


class SalesOrder(BaseModel):
    order_number        = models.CharField(max_length=30, unique=True)
    customer            = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT, related_name='orders')
    is_guest            = models.BooleanField(default=False)
    guest_email         = models.EmailField(blank=True)
    status              = models.CharField(max_length=20, choices=ORDER_STATUS, default='PENDING')
    shipping_name_bn    = models.CharField(max_length=200)
    shipping_name_en    = models.CharField(max_length=200, blank=True)
    shipping_phone      = models.CharField(max_length=15)
    shipping_address_bn = models.TextField()
    shipping_address_en = models.TextField(blank=True)
    shipping_district   = models.CharField(max_length=100)
    shipping_thana      = models.CharField(max_length=100)
    shipping_post_code  = models.CharField(max_length=10)
    payment_method      = models.CharField(max_length=10, choices=PAYMENT_METHOD_CHOICES, default='COD')
    payment_status      = models.CharField(max_length=10, choices=PAYMENT_STATUS_CHOICES, default='UNPAID')
    subtotal            = models.DecimalField(max_digits=12, decimal_places=2)
    discount_amount     = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_amount          = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    delivery_charge     = models.DecimalField(max_digits=8,  decimal_places=2, default=0)
    grand_total         = models.DecimalField(max_digits=12, decimal_places=2)
    cashback_amount     = models.DecimalField(max_digits=8,  decimal_places=2, default=0)
    cashback_used       = models.DecimalField(max_digits=8,  decimal_places=2, default=0)
    notes_bn            = models.TextField(blank=True)
    notes_en            = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.order_number

    def can_transition_to(self, new_status: str) -> bool:
        return new_status in ALLOWED_TRANSITIONS.get(self.status, [])


class SalesOrderItem(models.Model):
    id              = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    order           = models.ForeignKey(SalesOrder, on_delete=models.CASCADE, related_name='items')
    product         = models.ForeignKey(Product, on_delete=models.PROTECT)
    product_name_bn = models.CharField(max_length=300)
    product_name_en = models.CharField(max_length=300)
    unit_price      = models.DecimalField(max_digits=12, decimal_places=2)
    quantity        = models.DecimalField(max_digits=10, decimal_places=3)
    line_total      = models.DecimalField(max_digits=12, decimal_places=2)


class OrderStatusLog(models.Model):
    id          = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    order       = models.ForeignKey(SalesOrder, on_delete=models.CASCADE, related_name='status_logs')
    from_status = models.CharField(max_length=20, blank=True)
    to_status   = models.CharField(max_length=20)
    changed_by  = models.ForeignKey(User, on_delete=models.PROTECT)
    changed_at  = models.DateTimeField(auto_now_add=True)
    note_bn     = models.TextField(blank=True)
    note_en     = models.TextField(blank=True)

    class Meta:
        ordering = ['changed_at']


class PaymentTransaction(models.Model):
    PAYMENT_TXN_STATUS = [
        ('INITIATED', 'শুরু হয়েছে'),
        ('PAID',      'পরিশোধিত'),
        ('FAILED',    'ব্যর্থ'),
        ('CANCELLED', 'বাতিল'),
    ]

    id           = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    order        = models.OneToOneField(SalesOrder, on_delete=models.CASCADE, related_name='payment_transaction')
    tran_id      = models.CharField(max_length=100, unique=True)
    session_key  = models.CharField(max_length=200, blank=True)
    val_id       = models.CharField(max_length=200, blank=True)
    bank_tran_id = models.CharField(max_length=100, blank=True)
    card_type    = models.CharField(max_length=50, blank=True)
    amount       = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    status       = models.CharField(max_length=20, choices=PAYMENT_TXN_STATUS, default='INITIATED')
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.tran_id} — {self.status}'


class DeliveryAssignment(BaseModel):
    order           = models.OneToOneField(SalesOrder, on_delete=models.CASCADE, related_name='delivery')
    delivery_person = models.ForeignKey(
        User, on_delete=models.PROTECT,
        limit_choices_to={'role': 'DELIVERY'},
        related_name='deliveries',
    )
    assigned_at   = models.DateTimeField(auto_now_add=True)
    picked_up_at  = models.DateTimeField(null=True, blank=True)
    delivered_at  = models.DateTimeField(null=True, blank=True)
    tracking_note = models.TextField(blank=True)


# ─── Accounting ───────────────────────────────────────────────────────────────

ACCOUNT_TYPES = [
    ('ASSET',     'সম্পদ'),
    ('LIABILITY', 'দায়'),
    ('EQUITY',    'মূলধন'),
    ('REVENUE',   'আয়'),
    ('EXPENSE',   'খরচ'),
]


class Account(models.Model):
    id           = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    code         = models.CharField(max_length=10, unique=True)
    name_bn      = models.CharField(max_length=200)
    name_en      = models.CharField(max_length=200)
    account_type = models.CharField(max_length=20, choices=ACCOUNT_TYPES)
    parent       = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL)
    is_active    = models.BooleanField(default=True)

    class Meta:
        ordering = ['code']

    def __str__(self):
        return f'{self.code} — {self.name_bn}'


class JournalEntry(models.Model):
    id             = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    entry_number   = models.CharField(max_length=30, unique=True)
    reference_type = models.CharField(max_length=30, choices=[
        ('PURCHASE',         'ক্রয়'),
        ('SALE',             'বিক্রয়'),
        ('PAYMENT',          'পেমেন্ট'),
        ('RETURN',           'ফেরত'),
        ('ADJUSTMENT',       'সমন্বয়'),
        ('EXPENSE',          'খরচ'),
        ('EQUITY',           'ইক্যুইটি'),
        ('SUPPLIER_PAYMENT', 'সরবরাহকারী পেমেন্ট'),
        ('CAPITAL',          'মূলধন বিনিয়োগ'),
        ('LOAN_RECEIVED',    'ঋণ গ্রহণ'),
        ('LOAN_INTEREST',    'সুদ পরিশোধ'),
        ('LOAN_PRINCIPAL',   'ঋণ পরিশোধ'),
    ])
    reference_id   = models.UUIDField(null=True, blank=True)
    description_bn = models.TextField(blank=True)
    description_en = models.TextField(blank=True)
    created_by     = models.ForeignKey(User, on_delete=models.PROTECT)
    created_at     = models.DateTimeField(auto_now_add=True)
    is_posted      = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.entry_number


class JournalLine(models.Model):
    id            = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    journal_entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name='lines')
    account       = models.ForeignKey(Account, on_delete=models.PROTECT)
    debit         = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    credit        = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    memo_bn       = models.TextField(blank=True)
    memo_en       = models.TextField(blank=True)


class Partner(BaseModel):
    name_bn           = models.CharField(max_length=200)
    name_en           = models.CharField(max_length=200, blank=True)
    equity_percentage = models.DecimalField(max_digits=5, decimal_places=2)
    invested_amount   = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    is_active         = models.BooleanField(default=True)

    class Meta:
        ordering = ['name_bn']

    def __str__(self):
        return f'{self.name_bn} ({self.equity_percentage}%)'


class PartnerProfitPayment(BaseModel):
    partner      = models.ForeignKey(Partner, on_delete=models.CASCADE, related_name='profit_payments')
    year         = models.IntegerField()
    month        = models.IntegerField()
    total_profit = models.DecimalField(max_digits=14, decimal_places=2)
    share_amount = models.DecimalField(max_digits=14, decimal_places=2)
    paid_amount  = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    paid_date    = models.DateField(null=True, blank=True)
    note         = models.TextField(blank=True)

    class Meta:
        ordering = ['-year', '-month', 'partner']
        unique_together = [['partner', 'year', 'month']]

    def __str__(self):
        return f'{self.partner.name_bn} — {self.year}/{self.month:02d}'


class LoanInvestor(BaseModel):
    name_bn       = models.CharField(max_length=200)
    name_en       = models.CharField(max_length=200, blank=True)
    phone         = models.CharField(max_length=20, blank=True)
    principal     = models.DecimalField(max_digits=14, decimal_places=2, help_text='Original loan amount')
    interest_rate = models.DecimalField(max_digits=5, decimal_places=2, help_text='Annual interest rate %')
    loan_date     = models.DateField()
    due_date      = models.DateField(null=True, blank=True)
    is_active     = models.BooleanField(default=True)
    note          = models.TextField(blank=True)

    class Meta:
        ordering = ['-loan_date']

    def __str__(self):
        return f'{self.name_bn} — ৳{self.principal} @ {self.interest_rate}%'


class LoanPayment(BaseModel):
    PAYMENT_TYPES = [
        ('INTEREST',  'সুদ পরিশোধ'),
        ('PRINCIPAL', 'আসল পরিশোধ'),
    ]
    loan         = models.ForeignKey(LoanInvestor, on_delete=models.PROTECT, related_name='payments')
    payment_type = models.CharField(max_length=10, choices=PAYMENT_TYPES)
    amount       = models.DecimalField(max_digits=12, decimal_places=2)
    paid_date    = models.DateField()
    note         = models.TextField(blank=True)
    created_by   = models.ForeignKey('User', on_delete=models.PROTECT, related_name='loan_payments')

    class Meta:
        ordering = ['-paid_date']

    def __str__(self):
        return f'{self.loan.name_bn} — {self.payment_type} ৳{self.amount}'


class Banner(BaseModel):
    title_bn    = models.CharField(max_length=200)
    title_en    = models.CharField(max_length=200)
    subtitle_bn = models.CharField(max_length=300, blank=True)
    subtitle_en = models.CharField(max_length=300, blank=True)
    badge_text  = models.CharField(max_length=50, blank=True)
    image       = models.ImageField(upload_to='banners/', blank=True, null=True)
    bg_color    = models.CharField(max_length=20, default='#FFF7ED')
    link        = models.CharField(max_length=300, blank=True)
    order       = models.PositiveIntegerField(default=0)
    is_active   = models.BooleanField(default=True)

    class Meta:
        ordering = ['order', 'created_at']

    def __str__(self):
        return self.title_en


class HeroSlide(BaseModel):
    title_bn      = models.CharField(max_length=200, blank=True)
    title_en      = models.CharField(max_length=200, blank=True)
    subtitle_bn   = models.CharField(max_length=300, blank=True)
    subtitle_en   = models.CharField(max_length=300, blank=True)
    cta_label_bn  = models.CharField(max_length=100, blank=True)
    cta_label_en  = models.CharField(max_length=100, blank=True)
    cta_link      = models.CharField(max_length=500, blank=True)
    image         = models.ImageField(upload_to='hero_slides/', blank=True, null=True)
    bg_color      = models.CharField(max_length=20, default='#FFF7ED', blank=True)
    order         = models.PositiveIntegerField(default=0)
    is_active     = models.BooleanField(default=True)

    class Meta:
        ordering = ['order', 'created_at']

    def __str__(self):
        return self.title_en or self.title_bn or f'Slide {self.order}'


# ─── Discounts ───────────────────────────────────────────────────────────────

class Discount(models.Model):
    TYPES = [('PERCENTAGE', 'শতাংশ (%)'), ('FLAT', 'নির্দিষ্ট পরিমাণ (৳)')]

    id             = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    product        = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='discounts')
    discount_type  = models.CharField(max_length=12, choices=TYPES)
    discount_value = models.DecimalField(max_digits=10, decimal_places=2)
    note           = models.CharField(max_length=200, blank=True)
    is_active      = models.BooleanField(default=True)
    start_date     = models.DateField(null=True, blank=True)
    end_date       = models.DateField(null=True, blank=True)
    created_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.product.name_bn} — {self.discount_type} {self.discount_value}'


# ─── Delivery Charge Settings ─────────────────────────────────────────────────

class DeliveryCharge(models.Model):
    inside_dhaka  = models.DecimalField(max_digits=8, decimal_places=2, default=60)
    outside_dhaka = models.DecimalField(max_digits=8, decimal_places=2, default=120)
    updated_at    = models.DateTimeField(auto_now=True)
    updated_by    = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        verbose_name = 'Delivery Charge Settings'

    @classmethod
    def get(cls) -> 'DeliveryCharge':
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return f'ডেলিভারি চার্জ — ঢাকা: ৳{self.inside_dhaka}, বাইরে: ৳{self.outside_dhaka}'


class CashbackTier(models.Model):
    TYPES = [('FIXED', 'Fixed Amount'), ('PERCENTAGE', 'Percentage')]
    min_order_amount = models.DecimalField(max_digits=10, decimal_places=2)
    cashback_type    = models.CharField(max_length=10, choices=TYPES, default='FIXED')
    cashback_value   = models.DecimalField(max_digits=8, decimal_places=2)
    max_cashback     = models.DecimalField(max_digits=8, decimal_places=2, default=0)  # 0 = no cap
    is_active        = models.BooleanField(default=True)
    created_at       = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['min_order_amount']
        verbose_name = 'Cashback Tier'

    @classmethod
    def calculate(cls, order_amount: Decimal) -> Decimal:
        """Return cashback for the highest qualifying active tier."""
        tier = (
            cls.objects.filter(is_active=True, min_order_amount__lte=order_amount)
                       .order_by('-min_order_amount')
                       .first()
        )
        if not tier:
            return Decimal('0')
        if tier.cashback_type == 'FIXED':
            amount = tier.cashback_value
        else:
            amount = (order_amount * tier.cashback_value / 100).quantize(Decimal('0.01'))
        if tier.max_cashback > 0:
            amount = min(amount, tier.max_cashback)
        return amount

    def __str__(self):
        return f'৳{self.min_order_amount}+ → {self.cashback_type} {self.cashback_value}'


# ─── Notifications ────────────────────────────────────────────────────────────

class Notification(models.Model):
    id             = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    user           = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    title_bn       = models.CharField(max_length=200)
    title_en       = models.CharField(max_length=200)
    body_bn        = models.TextField(blank=True)
    body_en        = models.TextField(blank=True)
    is_read        = models.BooleanField(default=False)
    reference_type = models.CharField(max_length=30, blank=True)   # ORDER_CREATED, STATUS_CHANGED
    reference_id   = models.UUIDField(null=True, blank=True)
    created_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.title_en} → {self.user.email}'


# ─── Reviews ──────────────────────────────────────────────────────────────────

class Review(BaseModel):
    product     = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='reviews')
    order       = models.ForeignKey(SalesOrder, on_delete=models.SET_NULL, null=True, related_name='reviews')
    user        = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reviews')
    rating      = models.PositiveSmallIntegerField(choices=[(i, i) for i in range(1, 6)])
    comment     = models.TextField(blank=True)
    is_approved = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']
        unique_together = [('product', 'order', 'user')]

    def __str__(self):
        return f'{self.user.email} → {self.product.name_en} ({self.rating}★)'
