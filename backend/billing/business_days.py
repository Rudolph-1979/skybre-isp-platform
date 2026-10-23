"""Which days the platform is willing to disconnect somebody on.

WHY THIS EXISTS

Suspending a line on a Saturday is a decision nobody is around to reverse.
The customer cannot make an EFT that clears, cannot reach anybody here,
and sits without internet until Monday over an invoice they might have
settled in ten minutes on a working day. The debt is real; the timing is
just cruel and it generates a support call rather than a payment.

So a cut-off date that lands on a weekend or a public holiday MOVES
FORWARD to the next working day. It is deferred, never skipped -- the
customer still owes the money and still gets disconnected, one or three
days later.

WHAT IS AND IS NOT A WORKING DAY

Monday to Friday, minus whatever is in the PublicHoliday table. That table
is deliberately data rather than code: South Africa's statutory list is
stable but not fixed -- election days get declared at short notice, and a
business may want its own December shutdown in there too. Something
declared three weeks before it happens cannot be something that needs a
deploy.

FRIDAY IS A WORKING DAY HERE. Worth knowing rather than assuming: a
customer cut off on a Friday afternoon cannot pay in a way that clears
before Monday either. If that matters, it is a line in `WEEKEND` below,
not a redesign.

Nothing here writes anything.
"""
import datetime

#: Saturday and Sunday, as Python numbers them (Monday is 0).
WEEKEND = {5, 6}

#: How far forward to look for a working day before giving up. Eight days
#: covers a weekend plus the longest run of consecutive public holidays
#: South Africa produces. Bounded rather than a `while True`, because an
#: over-enthusiastic holiday table should not become an infinite loop
#: inside a billing run.
MAX_SHIFT_DAYS = 8


def holiday_set(start=None, end=None):
    """Public holidays as a set of dates, in one query.

    Callers that ask about many days should load this once and pass it in;
    a query per customer per date is how a nightly job becomes a nightly
    incident.
    """
    from .models import PublicHoliday

    rows = PublicHoliday.objects.all()
    if start is not None:
        rows = rows.filter(date__gte=start)
    if end is not None:
        rows = rows.filter(date__lte=end)
    return set(rows.values_list("date", flat=True))


def is_business_day(day, holidays=None):
    """Monday to Friday, and not a public holiday."""
    if day.weekday() in WEEKEND:
        return False
    if holidays is None:
        holidays = holiday_set(start=day, end=day)
    return day not in holidays


def next_business_day(day, holidays=None):
    """`day` itself if it is a working day, otherwise the next one.

    Returns `day` unchanged if no working day turns up within
    MAX_SHIFT_DAYS. That is the safe direction: refusing to move a date is
    visible and argued about, whereas silently pushing a disconnection a
    fortnight into the future is a customer who is never cut off and
    nobody noticing.
    """
    if holidays is None:
        holidays = holiday_set(start=day, end=day + datetime.timedelta(days=MAX_SHIFT_DAYS))
    candidate = day
    for _ in range(MAX_SHIFT_DAYS + 1):
        if is_business_day(candidate, holidays):
            return candidate
        candidate = candidate + datetime.timedelta(days=1)
    return day
