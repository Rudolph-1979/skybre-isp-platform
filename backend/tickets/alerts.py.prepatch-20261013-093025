"""Who counts as a "high alert" customer.

One definition, in one place, because two things now ask the question: the
dashboard tile's count, and the Customers page's high_alert filter that the
tile clicks through to. A tile reading 3 that lands on a list of 5 is worse
than no tile at all, and that is exactly what happens when the same rule is
written out twice.

The rule: more than `min_tickets - 1` tickets logged in a single CALENDAR
month, at any point in the last `months` months. Every department counts
(support, billing, sales, abuse) -- three contacts of any kind in one month is
a customer wanting attention, which is the question being asked.
"""
import datetime

from django.db.models import Count
from django.db.models.functions import TruncMonth
from django.utils import timezone

# The dashboard's defaults, and what the Customers page filter uses when it
# isn't given anything more specific.
DEFAULT_MONTHS = 6
DEFAULT_MIN_TICKETS = 3


def window_start(months, today=None):
    """First day of the calendar month `months - 1` back from today.

    Calendar months, not `today - 30 * months` days: the counting is per
    calendar month, so a window that starts mid-month would cut a month's
    tickets in half and undercount whoever's bad month it was.
    """
    today = today or timezone.localdate()
    year, month = today.year, today.month - (months - 1)
    while month <= 0:
        month += 12
        year -= 1
    return datetime.date(year, month, 1)


def high_alert_stats(customers, months=DEFAULT_MONTHS, min_tickets=DEFAULT_MIN_TICKETS, today=None):
    """{customer_id: {peak_count, peak_month, months_breached, total_tickets}}
    for the customers in `customers` (a queryset) who breached at least once.

    `customers` is passed in rather than queried here so the caller supplies
    its own partner-visibility scoping -- a reseller-scoped staff member must
    not learn the names of customers outside their partners just because those
    customers show up in an aggregate.
    """
    from tickets.models import Ticket

    start = window_start(months, today)
    rows = (
        Ticket.objects.filter(created_at__date__gte=start, customer__in=customers)
        .annotate(month=TruncMonth("created_at"))
        .values("customer_id", "month")
        .annotate(count=Count("id"))
    )

    per_customer = {}
    for row in rows:
        entry = per_customer.setdefault(
            row["customer_id"],
            {"peak_count": 0, "peak_month": None, "months_breached": 0, "total_tickets": 0},
        )
        entry["total_tickets"] += row["count"]
        if row["count"] >= min_tickets:
            entry["months_breached"] += 1
        if row["count"] > entry["peak_count"]:
            entry["peak_count"] = row["count"]
            entry["peak_month"] = row["month"]

    return {cid: e for cid, e in per_customer.items() if e["months_breached"] > 0}


def high_alert_customer_ids(customers, months=DEFAULT_MONTHS, min_tickets=DEFAULT_MIN_TICKETS, today=None):
    return list(high_alert_stats(customers, months, min_tickets, today).keys())
