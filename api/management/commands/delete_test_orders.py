"""One-off cleanup: permanently delete specific CANCELLED test orders together
with everything that only exists because of them (items, status logs, payment
transaction, delivery/courier rows cascade automatically; this command also
removes the order's journal entries, stock movements and notifications so the
books and stock stay consistent).

Safe by default: prints what it would delete and changes nothing unless
--confirm is passed. Refuses any order that isn't CANCELLED, that has exchanges
linked, or whose stock movements don't net to zero (i.e. stock wasn't fully
restored), since deleting those would silently change stock levels.

    python manage.py delete_test_orders ORD-123 ORD-456            # dry run
    python manage.py delete_test_orders ORD-123 ORD-456 --confirm  # delete
"""
from collections import defaultdict
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from api.models import JournalEntry, Notification, SalesOrder, StockMovement


class Command(BaseCommand):
    help = 'Delete specific CANCELLED test orders and their ledger/stock/notification rows.'

    def add_arguments(self, parser):
        parser.add_argument('order_numbers', nargs='+')
        parser.add_argument('--confirm', action='store_true', help='Actually delete (default is a dry run).')

    def handle(self, *args, **opts):
        orders = list(SalesOrder.objects.filter(order_number__in=opts['order_numbers']))
        missing = set(opts['order_numbers']) - {o.order_number for o in orders}
        if missing:
            raise CommandError(f'Not found: {", ".join(sorted(missing))}')

        plan = []
        for o in orders:
            if o.status != 'CANCELLED':
                raise CommandError(f'{o.order_number} is {o.status}, not CANCELLED — refusing.')
            if o.exchanges.exists() or hasattr(o, 'exchange_source'):
                raise CommandError(f'{o.order_number} is linked to an exchange — refusing.')

            moves = StockMovement.objects.filter(reference_id=o.id)
            net = defaultdict(lambda: Decimal('0'))
            for m in moves:
                if m.movement_type not in ('SALE', 'RETURN'):
                    raise CommandError(f'{o.order_number} has an unexpected {m.movement_type} stock movement — refusing.')
                net[m.product_id] += m.quantity   # quantities are stored signed (SALE < 0, RETURN > 0)
            unbalanced = {str(p): q for p, q in net.items() if q != 0}
            if unbalanced:
                raise CommandError(f'{o.order_number} stock movements do not net to zero {unbalanced} — refusing.')

            entries = JournalEntry.objects.filter(reference_id=o.id)
            notes = Notification.objects.filter(reference_id=o.id)
            plan.append((o, moves, entries, notes))
            self.stdout.write(
                f'{o.order_number} ({o.status}, {o.payment_status}, ৳{o.grand_total}): '
                f'{o.items.count()} items, {entries.count()} journal entries, '
                f'{moves.count()} stock movements, {notes.count()} notifications'
            )

        if not opts['confirm']:
            self.stdout.write(self.style.WARNING('Dry run only — re-run with --confirm to delete.'))
            return

        with transaction.atomic():
            for o, moves, entries, notes in plan:
                entries.delete()   # journal lines cascade
                moves.delete()
                notes.delete()
                number = o.order_number
                o.delete()         # items, status logs, payment txn, delivery, courier cascade
                self.stdout.write(self.style.SUCCESS(f'Deleted {number}'))
