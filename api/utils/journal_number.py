from django.utils import timezone


def next_entry_number() -> str:
    """'JE-YYYYMMDD-NNNN', unique among existing JournalEntry rows.

    Parses the highest existing suffix for today's date rather than
    counting rows — a row-count-based generator (`.count() + 1`) collides
    the moment any same-day entry is deleted (supplier/partner/loan payment
    deletion all do this), since the surviving count drops below the
    highest number already issued and the next entry_number generated
    reuses one that still exists, hitting the unique constraint.
    """
    from api.models import JournalEntry

    today  = timezone.now().date()
    prefix = f'JE-{today:%Y%m%d}-'
    last   = (
        JournalEntry.objects.filter(entry_number__startswith=prefix)
        .order_by('-entry_number')
        .values_list('entry_number', flat=True)
        .first()
    )
    seq = int(last.rsplit('-', 1)[1]) if last else 0
    return f'{prefix}{seq + 1:04d}'
