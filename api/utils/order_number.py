import secrets


def generate_order_number() -> str:
    """'ORD-' plus 8 random digits, unique among existing orders.

    Random rather than sequential/date-based on purpose — a predictable
    counter (the old 'PG-YYYYMMDD-NNNN' format) leaks daily order volume to
    anyone who sees a couple of order numbers.
    """
    from api.models import SalesOrder

    while True:
        number = f'ORD-{secrets.randbelow(10**8):08d}'
        if not SalesOrder.objects.filter(order_number=number).exists():
            return number
