"""What "overdue" means, in one place.

Two screens ask this question and they must not answer it differently:
Finance -> Invoices has "0-30 / 0-60 / 0-90 days overdue" quick filters,
and Finance -> Dashboard shows the ageing table. The filters are
CUMULATIVE ranges (0-60 includes everything 0-30 would) because that is
how quick buttons read; the dashboard's buckets are EXCLUSIVE (30-60
excludes 0-30) because that is how an ageing table has to add up. Those
are two different shapes of the same rule, which is exactly the situation
where two copies quietly drift apart -- so both are built here, from one
definition of what makes an invoice overdue at all.

An invoice is overdue when it is still owed (unpaid or explicitly marked
overdue) and its due date has passed. Quotes, pro formas, drafts,
cancelled and paid invoices are none of anyone's business here.

"Days overdue" is counted from the due date, so an invoice due yesterday
is 1 day overdue and lands in the 0-30 bucket. Boundaries are (lo, hi]:
an invoice exactly 30 days overdue belongs to 0-30, not to 30-60. Without
that convention an invoice can fall in two buckets or none, and an ageing
table whose parts don't sum to its total is worse than no ageing table.
"""
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from .models import Invoice

#: Statuses that still represent money owed to us.
OWED_STATUSES = (Invoice.Status.UNPAID, Invoice.Status.OVERDUE)

#: (label, lower_exclusive, upper_inclusive) in days past the due date.
#: None as the upper bound means open-ended.
AGE_BUCKETS = (
    ("0-30 days overdue", 0, 30),
    ("30-60 days overdue", 30, 60),
    ("60-90 days overdue", 60, 90),
    ("90+ days overdue", 90, None),
)


def today():
    return timezone.localdate()


def owed_q():
    """Invoices that still represent money owed, regardless of age."""
    return Q(status__in=OWED_STATUSES)


def overdue_q(as_of=None):
    """Owed AND past its due date."""
    as_of = as_of or today()
    return owed_q() & Q(date_due__lt=as_of)


def bucket_q(lower_days, upper_days, as_of=None):
    """One exclusive ageing bucket: (lower_days, upper_days] past due.

    lower_days=0 with upper_days=30 gives "1 to 30 days overdue".
    upper_days=None gives "more than lower_days overdue".
    """
    as_of = as_of or today()
    q = owed_q() & Q(date_due__lt=as_of - timedelta(days=lower_days))
    if upper_days is not None:
        q &= Q(date_due__gte=as_of - timedelta(days=upper_days))
    return q


def cumulative_q(within_days, as_of=None):
    """Everything from 1 up to `within_days` days overdue.

    This is what the Invoices page's quick buttons mean, and what
    InvoiceFilter.filter_overdue_within_days uses.
    """
    return bucket_q(0, within_days, as_of=as_of)
