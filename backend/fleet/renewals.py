"""The one definition of what an expiry date means.

Two different documents expire on this platform -- a vehicle's licence
disc and a driver's licence -- and they are read off the same list, by the
same person, on the same morning. If "due soon" meant one thing in fleet
and something slightly different in payroll, the Renewals tab would be
sorting apples against oranges and nobody would be able to tell which row
was which. So the arithmetic lives here once and both models call it.

What is deliberately NOT shared is the notice period. A licence disc is a
form and a payment and you can do it the same week; a driver's licence is
a booking at a testing centre that is routinely weeks out, plus an eye
test, plus a temporary licence while the card is printed. Warning about
both 30 days ahead would make one list item useless and the other
panic-inducing. Each model passes its own window, and that number travels
with the row so the screen can say which window it is talking about
instead of implying there is only one.
"""
import datetime

# The four states a renewal can be in. UNKNOWN is a state in its own right
# and is never folded into OK: a vehicle nobody has captured a date for is
# not compliant, it is unaccounted for, and the two need different actions
# -- one is a diary entry, the other is somebody walking out to the bakkie
# to read the disc.
UNKNOWN = "unknown"
EXPIRED = "expired"
DUE_SOON = "due_soon"
OK = "ok"

# How the list is ordered when several things are pending. Expired first
# (somebody is driving illegally today), then what is about to expire,
# then the ones nobody has captured, and only then the ones that are fine.
#
# UNKNOWN sits ABOVE OK, not at the bottom. It is tempting to bury a row
# with no date because there is nothing to count down -- but a missing
# date is the only row on the list that could already be expired without
# anybody knowing. It outranks a disc that is good for another eight
# months.
STATUS_ORDER = {EXPIRED: 0, DUE_SOON: 1, UNKNOWN: 2, OK: 3}

# The statuses that mean somebody has to do something. Used for the
# dashboard count, so the tile and the tab can never disagree about what
# they are counting.
ACTION_NEEDED = (EXPIRED, DUE_SOON, UNKNOWN)


def _today(today=None):
    if today is not None:
        return today
    from django.utils import timezone

    return timezone.localdate()


def days_until(expiry, today=None):
    """Days from today to the expiry date. Negative once it has passed,
    0 on the day itself, None when there is no date.

    0 counts as still valid, not expired: a disc is valid through the last
    day printed on it. Getting this off by one would put a vehicle on the
    illegal list for a day it is perfectly legal, which is the kind of
    error that teaches people to ignore the list."""
    if not expiry:
        return None
    return (expiry - _today(today)).days


def renewal_status(expiry, notice_days, today=None):
    """One of UNKNOWN / EXPIRED / DUE_SOON / OK."""
    days = days_until(expiry, today)
    if days is None:
        return UNKNOWN
    if days < 0:
        return EXPIRED
    if days <= notice_days:
        return DUE_SOON
    return OK


def sort_key(status, expiry, days):
    """Sort a renewal row: worst state first, and within a state the one
    that runs out soonest first. A row with no date sorts last inside its
    own band since there is nothing to compare."""
    return (STATUS_ORDER.get(status, 9), days if days is not None else 10**6)


def add_years(date, years):
    """The same date `years` later, handling 29 February by falling back
    to the 28th -- the convention every South African renewal follows, and
    the one thing date arithmetic gets wrong once every four years if it
    is written out by hand each time."""
    try:
        return date.replace(year=date.year + years)
    except ValueError:
        return date.replace(year=date.year + years, day=28)


def next_expiry(current, years, today=None):
    """What a renewal should set the expiry to.

    Counted from the CURRENT expiry, not from today, so renewing a month
    early does not throw that month away -- which is exactly when people
    do renew, and quietly losing four weeks every cycle is the sort of
    thing nobody notices until a disc comes up short.

    Unless the current date has already passed (or there isn't one), in
    which case counting from it would produce a date that is still in the
    past. Then it counts from today, because that is what actually
    happened at the counter.
    """
    today = _today(today)
    base = current if (current and current > today) else today
    return add_years(base, years)


def expiry_is_sane(expiry, years, today=None):
    """A plausibility check on a date somebody typed, returning an error
    string or None.

    The real failure this catches is a slipped year -- 2036 for 2026 --
    which produces a date that is perfectly valid, sorts to the bottom of
    the list, and means the vehicle never appears on it again. Nothing
    else on the screen would ever contradict it.

    A date in the PAST is allowed: capturing a disc that has already
    lapsed is a legitimate thing to do, and the row honestly reading
    "expired" is the whole point of capturing it.
    """
    if not expiry:
        return None
    today = _today(today)
    ceiling = add_years(today, years + 1)
    if expiry > ceiling:
        return (
            f"{expiry:%Y-%m-%d} is more than {years + 1} years away, which no licence runs for. "
            "Check the year."
        )
    floor = add_years(today, -20)
    if expiry < floor:
        return f"{expiry:%Y-%m-%d} is over 20 years ago. Check the year."
    return None


def isoparse(value):
    """Parse a YYYY-MM-DD out of request data, returning (date, error)."""
    if value in (None, ""):
        return None, None
    if isinstance(value, datetime.date):
        return value, None
    try:
        return datetime.date.fromisoformat(str(value)), None
    except (TypeError, ValueError):
        return None, f"{value!r} is not a date (YYYY-MM-DD)."
