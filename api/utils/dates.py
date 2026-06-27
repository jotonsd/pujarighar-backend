"""
Local-timezone date-range helpers for filtering/grouping by Asia/Dhaka calendar
days, weeks, and months — without relying on the database's CONVERT_TZ (which
requires MySQL's named-timezone tables to be loaded; not available on most
shared hosting). All comparisons are done against the raw UTC-stored
`created_at` using plain __gte/__lt, after converting the boundary in Python.
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings

LOCAL_TZ = ZoneInfo(settings.TIME_ZONE)


def parse_date(value) -> date | None:
    """Accepts a 'YYYY-MM-DD' string, a date, or a datetime; returns a date or None."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(value, '%Y-%m-%d').date()


def local_day_start(d) -> datetime:
    """UTC-aware datetime for local midnight of the given local date."""
    d = parse_date(d)
    return datetime(d.year, d.month, d.day, tzinfo=LOCAL_TZ)


def local_day_end_exclusive(d) -> datetime:
    """UTC-aware datetime for local midnight of the day AFTER the given local date —
    use with __lt to make an inclusive 'through end of day' upper bound."""
    return local_day_start(d) + timedelta(days=1)


def to_local(dt: datetime) -> datetime:
    """Convert a UTC-aware datetime to Asia/Dhaka local time."""
    return dt.astimezone(LOCAL_TZ)


def local_period_bucket(dt: datetime, group_by: str) -> date:
    """Bucket a UTC-aware datetime into its local day/week/month start date."""
    local = to_local(dt).date()
    if group_by == 'month':
        return local.replace(day=1)
    if group_by == 'week':
        return local - timedelta(days=local.weekday())
    return local
