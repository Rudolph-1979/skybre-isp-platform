"""Which documents a VAT period is built from. One definition, two readers.

The VAT return is summed on screen and exported to a spreadsheet, and
those are separate bits of code. The quiet way that goes wrong is each
growing its own copy of the filter -- "invoices issued in the period,
excluding quotes, pro-formas, drafts and cancellations" is four
conditions and a date boundary, and it only takes one of them drifting
for the spreadsheet and the return to disagree. Nothing on either screen
would say which one was right.

So the filters live here, once. views.py sums them; vat_export.py lists
them; test_vat_export.py asserts the listed rows total to exactly what
the summed figures say.

What each function deliberately does NOT do is choose columns. The
summary reads four fields per row and wants nothing else loaded; the
export needs the supplier, the invoice number and the customer. Each
caller narrows or widens for itself -- it is the FILTER that must not
drift, not the projection.
"""
from django.db.models.functions import Coalesce


def output_vat_items(period_start, period_end):
    """Invoice lines whose Output VAT falls in this period, on the
    invoice/accrual basis.

    Bucketing by rate is the caller's job; what is fixed here is which
    lines count at all. Quotes, pro-formas, drafts and cancelled invoices
    are excluded -- none of those is a tax invoice, so no VAT ever became
    due on them.
    """
    from billing.models import Invoice, InvoiceItem

    exclude_statuses = [
        Invoice.Status.QUOTE, Invoice.Status.PROFORMA,
        Invoice.Status.DRAFT, Invoice.Status.CANCELLED,
    ]
    # date_issued, not date_created. date_created is auto_now_add, so a
    # quote raised in March and converted to a real invoice in June still
    # says March -- and the output VAT on it was declared in neither
    # return: March's excluded it for being a quote, June's never saw it
    # because of the date. Invoice.date_issued is stamped the day the
    # document actually becomes a tax invoice, which is the day the VAT
    # becomes due, and is what the accrual basis turns on.
    # Fall back to date_created when date_issued is NULL, the same way
    # notifications.pdf dates a document. A NULL matches neither >= nor
    # <=, so a countable invoice with no issue date is invisible to its own
    # period AND to every other one -- a full-year return does not turn it
    # up either, so nothing would ever find it. That is not a theoretical
    # row: deploy.sh applies migrations while the old containers are
    # deliberately left serving, so anything invoiced in that gap was
    # written by code that had never heard of this column. date_created is
    # the day it was raised, which for such a row is the day it was issued.
    issued_on = Coalesce("invoice__date_issued", "invoice__date_created")
    return (
        InvoiceItem.objects
        .annotate(issued_on=issued_on)
        .filter(issued_on__gte=period_start, issued_on__lte=period_end)
        .exclude(invoice__status__in=exclude_statuses)
    )


def input_vat_expenses(period_start, period_end):
    """Expenses whose Input VAT falls in this period.

    Dated by Expense.date -- the date on the SUPPLIER's own invoice, not
    when the record was created -- which is what determines the VAT period
    on the invoice/accrual basis Skybre files under.
    """
    from .models import Expense

    return Expense.objects.filter(date__gte=period_start, date__lte=period_end)


def input_vat_stock_receipts(period_start, period_end):
    """Stock receipts whose Input VAT falls in this period, dated by
    StockReceipt.invoice_date for the same reason as above.

    prefetch_related("lines") is part of the definition, not an
    optimisation one caller bolted on: every VAT figure on a receipt is
    derived from its lines, so a caller that forgets it pays one query per
    line and gets the same answer slowly. Keeping it here means neither
    caller can forget.
    """
    from inventory.models import StockReceipt

    return StockReceipt.objects.filter(
        invoice_date__gte=period_start, invoice_date__lte=period_end
    ).prefetch_related("lines")
