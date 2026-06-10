"""
Management command: seed_stock

Creates PURCHASE stock movements for every non-package product that currently
has zero stock.  Alternates payment method — odd index → CASH, even index → CREDIT
— so both sides of the ledger get tested.

For each movement the existing product_service._create_purchase_journal() fires
automatically, posting:
    Dr Inventory (1300)          ← total_cost
    Cr Cash (1000) or AP (2000)  ← total_cost

Usage:
    python manage.py seed_stock
    python manage.py seed_stock --qty 50        # custom quantity per product
    python manage.py seed_stock --dry-run       # preview only
"""

from decimal import Decimal
from django.core.management.base import BaseCommand
from django.db import transaction
from api.models import Product, Supplier, User
from api.services.product_service import StockService


class Command(BaseCommand):
    help = 'Seed PURCHASE stock for all zero-stock products with journal entries'

    def add_arguments(self, parser):
        parser.add_argument('--qty',     type=int, default=100, help='Quantity per product (default 100)')
        parser.add_argument('--dry-run', action='store_true',   help='Preview without writing')

    @transaction.atomic
    def handle(self, *args, **options):
        qty     = Decimal(str(options['qty']))
        dry     = options['dry_run']
        svc     = StockService()

        admin    = User.objects.filter(role='ADMIN', is_active=True).first()
        supplier = Supplier.objects.first()

        if not admin:
            self.stderr.write('No active admin user found. Aborting.')
            return

        products = [
            p for p in Product.objects.filter(is_active=True, is_package=False)
            if p.stock_on_hand == 0
        ]

        if not products:
            self.stdout.write(self.style.WARNING('All products already have stock. Nothing to do.'))
            return

        self.stdout.write(f'Found {len(products)} products with zero stock.\n')

        for i, product in enumerate(products):
            payment = 'CASH' if i % 2 == 0 else 'CREDIT'
            unit_cost = product.cost_price if product.cost_price > 0 else Decimal('10')
            total = unit_cost * qty

            self.stdout.write(
                f'  [{i+1:02d}] {product.sku} | {product.name_bn} | '
                f'qty={qty} | cost={unit_cost} | total={total} | {payment}'
            )

            if not dry:
                svc.adjust_stock(
                    product=product,
                    movement_type='PURCHASE',
                    quantity=qty,
                    note_bn=f'প্রাথমিক স্টক যোগ — {product.name_bn}',
                    note_en=f'Initial stock entry — {product.name_en}',
                    user=admin,
                    unit_cost=unit_cost,
                    supplier_id=str(supplier.id) if supplier else None,
                    payment_method=payment,
                )

        if dry:
            self.stdout.write(self.style.WARNING('\nDRY RUN — no changes written.'))
            transaction.set_rollback(True)
        else:
            self.stdout.write(self.style.SUCCESS(
                f'\nDone. Added stock + journal entries for {len(products)} products.'
            ))
