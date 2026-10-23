"""Where a customer stands on paying, in one word, for the Customers list.

Rudolph asked for a Payment status column on Customers. It reuses the
labels invoices already show (InvoiceStatusBadge), so the list and a
customer's own invoices say the same thing:

    overdue_partial  "Partially Paid - Overdue"  an owed invoice past due with some money on it
    overdue          "Overdue"                   an owed invoice past its due date
    partial          "Partially Paid"            an owed invoice, not yet due, with some money on it
    unpaid           "Unpaid"                    owed, not yet due (or a balance with no open invoice)
    paid             "Paid up"                   nothing owed
    credit           "In credit"                 nothing owed and the balance is in their favour

"Owed" and "overdue" come from billing.overdue, the definition the ageing
report, the Invoices page filters and the Overdue customer filter already
share. Annotated onto the queryset as three EXISTS subqueries, so a page of
customers costs one query rather than one per row. PAYMENT_RANK orders
worst first for the column's sort.
"""
from decimal import Decimal

from django.db.models import Case, Exists, IntegerField, OuterRef, Q, Value, When

LABELS = {
    "overdue_partial": "Partially Paid - Overdue",
    "overdue": "Overdue",
    "partial": "Partially Paid",
    "unpaid": "Unpaid",
    "paid": "Paid up",
    "credit": "In credit",
}

PAYMENT_RANK = {
    "overdue_partial": 0,
    "overdue": 1,
    "partial": 2,
    "unpaid": 3,
    "paid": 4,
    "credit": 5,
}


def _invoice_q():
    from billing.overdue import overdue_q, owed_q

    return owed_q(), overdue_q()


def annotate_payment_status(queryset):
    from billing.models import Invoice

    owed, overdue = _invoice_q()
    mine = Invoice.objects.filter(customer=OuterRef("pk"))
    partly = Q(paid_amount__gt=0)
    queryset = queryset.annotate(
        _pay_overdue=Exists(mine.filter(overdue)),
        _pay_overdue_partial=Exists(mine.filter(overdue & partly)),
        _pay_partial=Exists(mine.filter(owed & partly)),
        _pay_owed=Exists(mine.filter(owed)),
    )
    return queryset.annotate(
        payment_rank=Case(
            When(_pay_overdue_partial=True, then=Value(0)),
            When(_pay_overdue=True, then=Value(1)),
            When(_pay_partial=True, then=Value(2)),
            When(_pay_owed=True, then=Value(3)),
            When(balance__gt=0, then=Value(3)),
            When(balance__lt=0, then=Value(5)),
            default=Value(4),
            output_field=IntegerField(),
        )
    )


def _flags(customer):
    """The four facts, from the annotation when present, else queried."""
    if hasattr(customer, "_pay_owed"):
        return (
            customer._pay_overdue_partial,
            customer._pay_overdue,
            customer._pay_partial,
            customer._pay_owed,
        )
    owed, overdue = _invoice_q()
    invoices = customer.invoices.all()
    partly = Q(paid_amount__gt=0)
    return (
        invoices.filter(overdue & partly).exists(),
        invoices.filter(overdue).exists(),
        invoices.filter(owed & partly).exists(),
        invoices.filter(owed).exists(),
    )


def payment_status(customer):
    overdue_partial, overdue, partial, owed = _flags(customer)
    if overdue_partial:
        return "overdue_partial"
    if overdue:
        return "overdue"
    if partial:
        return "partial"
    balance = customer.balance or Decimal("0")
    if owed or balance > 0:
        return "unpaid"
    if balance < 0:
        return "credit"
    return "paid"
