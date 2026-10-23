"""Money that happens once, and money that happens every month.

WHY THIS EXISTS

A quote for a R3 500 router plus a R899-a-month service printed a single
total. Adding a once-off charge to a monthly one produces a figure that is
true of nothing: the customer is not paying it once, and they are not
paying it monthly. It is the sort of number that gets argued about on the
phone after the first invoice arrives.

So a quote reports two figures, each labelled, and this module works out
which lines belong to which.

WHAT DECIDES WHICH A LINE IS

`InvoiceItem.billing_kind` when it is set, and the line's type when it is
not. A tariff-plan line becomes a real Service on acceptance -- see
Invoice.activate_tariff_services -- and a Service is what the recurring
engine bills every month, so a tariff line is recurring by nature. A stock
item is a thing that is handed over once. A custom line could be either,
which is exactly why the explicit field exists.

Deriving the default rather than back-filling a column is deliberate: no
data migration, no existing row carrying a guess someone made once, and a
tariff line added next year is classified correctly without anybody
remembering a flag.

WHY THE TWO TOTALS ARE NOT ADDED UP ANYWHERE

Because they cannot honestly be. Each is rounded to cents in its own
right, the way Invoice.recalc_totals rounds the document, so the two are
each correct. Their sum is not a quantity the customer owes -- and where
rounding makes it differ by a cent from the document's stored total, a
blended figure printed beside them would be a visible contradiction on a
document with our name on it. The caller shows both and no blend.

Read-only. Nothing here writes or recomputes what the customer is charged.
"""
from decimal import ROUND_HALF_UP, Decimal

CENTS = Decimal("0.01")
ZERO = Decimal("0.00")

ONCE_OFF = "once_off"
RECURRING = "recurring"


def to_cents(value):
    """ROUND_HALF_UP, matching Invoice.recalc_totals.

    Not Python's default half-even: the stored totals were rounded by
    Postgres to numeric(12,2), and a figure on a quote that rounds
    differently from the invoice it becomes is a cent nobody can explain.
    """
    return Decimal(value).quantize(CENTS, rounding=ROUND_HALF_UP)


def kind_of(item):
    """Which pile this line belongs in."""
    from .models import InvoiceItem

    explicit = (getattr(item, "billing_kind", "") or "").strip()
    if explicit in (ONCE_OFF, RECURRING):
        return explicit
    # A Recurring charge line (IP rental, static IP, ...) is monthly by
    # definition -- it is picked from the recurring-charge catalogue.
    # Before 16 Oct 2026 only TARIFF was listed here, because the
    # Recurring charge line type was added after this function was
    # written, so every IP rental on a quote printed under "Once-off".
    return (
        RECURRING
        if item.item_type in (
            InvoiceItem.ItemType.TARIFF,
            InvoiceItem.ItemType.RECURRING_CHARGE,
        )
        else ONCE_OFF
    )


def monthly_price_of(item):
    """The line's full monthly price, excl. VAT, per unit.

    A pro-rata line stores the part-month amount in unit_price (that is
    what the first invoice charges) and the full monthly price in
    monthly_price. Every other line has no monthly_price and its
    unit_price already is the monthly figure.
    """
    mp = getattr(item, "monthly_price", None)
    return item.unit_price if mp is None else mp


def is_pro_rata(item):
    mp = getattr(item, "monthly_price", None)
    return mp is not None and mp != item.unit_price


def _group(items, price=lambda i: i.unit_price):
    """Subtotal, tax and inclusive total for one pile of lines.

    Each component is rounded once over the whole pile and the inclusive
    figure is derived from the two, for the reason Invoice.recalc_totals
    spells out at length: rounding three numbers independently lets a
    document print an exclusive amount and a VAT amount that do not add up
    to the total beside them.
    """
    subtotal = to_cents(
        sum((i.quantity * price(i) for i in items), start=Decimal("0"))
    )
    tax = to_cents(
        sum(
            (i.quantity * price(i) * (i.tax_rate_pct / 100) for i in items),
            start=Decimal("0"),
        )
    )
    return {
        "count": len(items),
        "excl": subtotal,
        "tax": tax,
        "incl": subtotal + tax,
    }


def split_totals(invoice):
    """The piles, or None when there is nothing to split.

    Returns None unless the document carries BOTH kinds, or carries a
    pro-rata monthly line. A quote that is all equipment, or all full-month
    service, already has one honest total and does not need a second way
    of saying it.

    A pro-rata line is the reason for the second condition (16 Oct 2026):
    its stored amount is only the part month, so a single "Total" -- or a
    "Monthly thereafter" built from it -- understated what the customer
    pays every month after the first. O-000012 printed R306.48 a month for
    a 30 Mbps line that is about R594 a month.

      once_off     -- lines charged once.
      first_month  -- the monthly lines as the first invoice charges them
                      (part-month amounts). Only when has_pro_rata.
      recurring    -- the monthly lines at their FULL monthly price: what
                      "Monthly thereafter" means.
    """
    items = list(invoice.items.all())
    once_off = [i for i in items if kind_of(i) == ONCE_OFF]
    recurring = [i for i in items if kind_of(i) == RECURRING]
    has_pro_rata = any(is_pro_rata(i) for i in recurring)
    if not recurring or (not once_off and not has_pro_rata):
        return None
    return {
        "once_off": _group(once_off),
        "first_month": _group(recurring) if has_pro_rata else None,
        "recurring": _group(recurring, price=monthly_price_of),
        "has_pro_rata": has_pro_rata,
    }
