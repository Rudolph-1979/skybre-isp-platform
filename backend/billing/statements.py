"""A customer's account movements, and what their balance was on a date.

WHY THIS EXISTS

`Customer.balance` is a STORED running total, not a derived one. Five
places in the codebase move it -- an invoice entering or leaving a debited
status, an invoice being deleted, a payment, a historical payment from
the ledger import, and an approved credit request -- and each one writes
the new number without leaving a ledger entry behind.

That is fine for "what do they owe today", which is the only question the
platform asked of it until now. It is not enough for a statement covering
one month, which needs three numbers the stored balance cannot give:
what they owed on the 1st, what moved during the month, and what they
owed at the end.

So this module derives a balance from the dated records themselves. Every
movement above has a date on it:

  * an invoice in a debited status  -- date_issued, falling back to
    date_created for anything issued before that field existed;
  * a payment                       -- Payment.date, which the ledger
    import deliberately back-dates so imported history is not one flat
    stack on the day of the import;
  * an approved credit request      -- decided_at.

ONE DEFINITION OF "EXPECTED", NOT TWO

`billing_drift`'s command -- manage.py balance_drift -- already answers
"does the stored balance agree with the records", in bulk and in one
query: invoiced minus paid minus credited, over the same three relations
and the same statuses. This module does NOT re-decide that. `expected` and
`drift` below mean exactly what they mean there, sign included, so a
customer listed by that command is the same customer this module refuses
to produce a statement for. A second, subtly different definition of what
somebody owes is how two reports end up disagreeing in front of a
customer.

What this module adds is the DATE dimension, which balance_drift has no
need of: totals to a point in time, and the movements between two points.

THE PART THAT MATTERS MORE THAN THE ARITHMETIC

A derived balance and the stored one can disagree, and where they do, the
derivation is missing something. The cause is known, documented, and not
hypothetical: migration 0020 set `balance_debited` to NULL on every
invoice that existed at the time, precisely because what the old code had
already done to the balance was not knowable. balance_drift's own
docstring describes the same damage from the other end.

So `reconciliation()` is not a diagnostic afterthought. It is the gate. A
statement whose closing balance disagrees with what the customer is
actually being chased for is worse than no statement at all -- it is an
invitation to a dispute we would lose. Nothing in this module decides what
to do about that; it reports the drift and lets the caller refuse.

Nothing here writes anything.
"""
import calendar
import datetime
from decimal import Decimal

from django.utils import timezone

ZERO = Decimal("0.00")


def _local_date(value):
    """A date from either a date or an aware datetime."""
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return timezone.localtime(value).date()
    return value


def month_range(year, month):
    """(first day, first day of the next month). End is exclusive."""
    start = datetime.date(year, month, 1)
    last = calendar.monthrange(year, month)[1]
    return start, start + datetime.timedelta(days=last)


#: What billing.ledger_import writes into Payment.note for every payment it
#: brings across. READ here, never written: that exact wording is the
#: import's idempotency key -- "the source reference lives here because
#: Payment has no reference column, and it is what makes a re-run
#: idempotent, so the wording must not drift" -- so this matches it rather
#: than changing it.
IMPORT_NOTE_PREFIX = "Imported from previous system: "
# Every importer's payments start with this -- the ledger import writes
# IMPORT_NOTE_PREFIX, the transaction-report import writes "Imported from
# previous system (transaction report): ...". Anything deciding "is this
# historical money?" must test this, not IMPORT_NOTE_PREFIX (5 Oct 2026:
# the narrower test let transaction-report payments be auto-allocated and
# reconnect customers as if they had just been received).
IMPORTED_NOTE_STEM = "Imported from previous system"


def is_imported_note(note):
    return (note or "").startswith(IMPORTED_NOTE_STEM)


def _payment_reference(payment):
    """The receipt number, when the note is carrying one.

    Payment has no reference column, so the ledger import puts the source
    receipt in `note`. On a statement that landed in the DESCRIPTION
    column, and a customer read "Imported from previous system:
    RCP2026-12-01726" about their own payment -- our migration showing
    through onto their document, with the Reference column empty beside it.

    ONLY the import's own prefix is recognised. A note somebody typed is
    deliberately NOT put on the statement: notes are staff-facing, they say
    things like who chased whom and what was agreed on the phone, and a
    customer's statement is the wrong place to find that out.
    """
    note = (payment.note or "").strip()
    if note.startswith(IMPORT_NOTE_PREFIX):
        return note[len(IMPORT_NOTE_PREFIX):].strip()
    return ""


def movements(customer, until=None, since=None):
    """Every dated thing that has moved this customer's balance.

    Ordered oldest first, and stably: two movements on the same day sort by
    kind and then by primary key, so a statement regenerated tomorrow puts
    its lines in the same order as the one the customer already has.

    `since` and `until` are dates; `until` is exclusive.

    A CREDIT NOTE arrives here as what it is in the data -- an Invoice row
    with a negative total -- and is turned into a credit line rather than a
    negative debit. The arithmetic is identical either way; a statement
    with a minus sign in the debit column is not.
    """
    from .models import CreditRequest, Invoice, Payment

    rows = []

    invoices = (
        Invoice.objects.filter(customer=customer, status__in=Invoice.DEBITED_STATUSES)
        .only("pk", "number", "total", "date_issued", "date_created", "status")
    )
    for invoice in invoices:
        when = _local_date(invoice.date_issued) or _local_date(invoice.date_created)
        if when is None:
            continue
        total = invoice.total or ZERO
        credit_note = total < 0
        rows.append({
            "date": when,
            "kind": "credit_note" if credit_note else "invoice",
            "sort": 0,
            "pk": invoice.pk,
            "reference": invoice.number or "",
            "description": "Credit note" if credit_note else "Invoice",
            "debit": ZERO if credit_note else total,
            "credit": -total if credit_note else ZERO,
        })

    for payment in Payment.objects.filter(customer=customer).only(
        "pk", "amount", "date", "method", "note"
    ):
        when = _local_date(payment.date)
        if when is None:
            continue
        rows.append({
            "date": when,
            "kind": "payment",
            "sort": 1,
            "pk": payment.pk,
            # The receipt goes in the reference column, where a reference
            # belongs, and the description says what happened. See
            # _payment_reference for what is deliberately left off.
            "reference": _payment_reference(payment),
            "description": f"Payment received - {payment.get_method_display()}",
            "debit": ZERO,
            "credit": payment.amount or ZERO,
        })

    # credit_note__isnull=True: a credit that already has its own invoice
    # (see CreditRequest.create_credit_note_invoice) is carried by the
    # "invoices" loop above instead, as a credit_note row -- so it is not
    # listed twice on the same statement. Only a credit with no invoice
    # counterpart (one decided before that feature existed and not yet
    # backfilled) still needs this fallback row.
    credits = CreditRequest.objects.filter(
        customer=customer, status=CreditRequest.Status.APPROVED, decided_at__isnull=False,
        credit_note__isnull=True,
    ).only("pk", "amount", "decided_at", "reason")
    for credit in credits:
        rows.append({
            "date": _local_date(credit.decided_at),
            "kind": "credit",
            "sort": 2,
            "pk": credit.pk,
            "reference": "",
            "description": f"Credit: {credit.reason}"[:120],
            "debit": ZERO,
            "credit": credit.amount or ZERO,
        })

    if since is not None:
        rows = [r for r in rows if r["date"] >= since]
    if until is not None:
        rows = [r for r in rows if r["date"] < until]

    rows.sort(key=lambda r: (r["date"], r["sort"], r["pk"]))
    return rows


def balance_as_at(customer, when):
    """What this customer owed at the start of `when` (a date).

    Positive means they owe us, matching the sign convention of the stored
    balance: a payment reduces it, an invoice raises it.
    """
    total = ZERO
    for row in movements(customer, until=when):
        total += row["debit"] - row["credit"]
    return total


def _stored_balance(customer):
    """The stored balance, read back from the database.

    Not `customer.balance`, deliberately. Every write to that column in
    this codebase goes through `.update(balance=F("balance") + ...)` --
    Invoice.apply_balance_debit, Payment.reverse_ledger_effect, the credit
    approval, the ledger import -- because it is the field two concurrent
    finance actions race on. An UPDATE like that changes the row and
    leaves any Python object holding the old value untouched.

    So a caller who loaded a Customer, raised an invoice, and then asked
    whether the account reconciles is comparing this month's records
    against a balance from before the invoice existed.
    """
    from customers.models import Customer

    value = (
        Customer.objects.filter(pk=customer.pk)
        .values_list("balance", flat=True)
        .first()
    )
    return value if value is not None else ZERO


def reconciliation(customer):
    """Does the derivation agree with the stored balance?

    THE GATE, not a statistic. See this module's docstring.

    `expected` and `drift` carry the same meaning and the same SIGN as
    `manage.py balance_drift`: drift is stored minus expected, so a
    positive figure is a customer owing more than their invoices account
    for and a negative one is a customer showing credit they never had.
    Matching that command rather than inventing a second convention is
    deliberate -- two reports that disagree about the sign of a customer's
    problem are worse than one report.

    A non-zero drift means the records cannot account for the stored
    figure, most often because of the pre-0020 invoices whose debits were
    applied by code that left nothing behind.

    One difference from balance_drift, and it only ever shows up in
    testing: this walks DATED movements up to and including today, so an
    invoice issue-dated in the future is not counted here and is counted
    there. Early invoicing (package #19) does: from an early run until the
    invoice date those customers show a drift here. The monthly statements
    go out on the 2nd, after it.
    """
    tomorrow = timezone.localdate() + datetime.timedelta(days=1)
    expected = balance_as_at(customer, tomorrow)
    # READ BACK rather than trusting the instance we were handed. This is a
    # GATE -- it decides whether a document with a closing balance on it
    # goes to a customer -- and a stale in-memory copy produces a drift
    # that is nothing but staleness. The failure mode is a statement
    # withheld from somebody whose account is perfectly fine, or, with the
    # sign the other way, one sent that should have been held back.
    stored = _stored_balance(customer) or ZERO
    drift = stored - expected
    return {
        "expected": expected,
        "stored": stored,
        "drift": drift,
        "reconciles": drift == ZERO,
    }


def _ledger(customer, start, end):
    """The running balance between two dates. `end` is exclusive.

    Shared by both statements below so that the two documents cannot drift
    apart. They differ only in the window they ask for -- everything about
    how a movement is classified and how the balance runs is decided here,
    once.
    """
    opening = balance_as_at(customer, start)

    lines, running = [], opening
    invoiced = paid = credited = ZERO
    for row in movements(customer, since=start, until=end):
        running += row["debit"] - row["credit"]
        if row["kind"] == "invoice":
            invoiced += row["debit"]
        elif row["kind"] == "payment":
            paid += row["credit"]
        else:
            credited += row["credit"]
        lines.append({**row, "balance": running})

    return {
        "customer": customer,
        "start": start,
        "opening_balance": opening,
        "closing_balance": running,
        "invoiced": invoiced,
        "paid": paid,
        "credited": credited,
        "lines": lines,
        "reconciliation": reconciliation(customer),
    }


def period_statement(customer, year, month):
    """One named calendar month of account activity.

    NOT what the monthly job sends -- see rolling_statement for why. This
    is the on-demand "what happened in August" document, and the shape the
    preview command prints when asked for a specific month.

    Opening and closing balances are derived from the movements
    themselves rather than from the stored balance, plus the
    reconciliation above so a caller can refuse to publish a statement
    that disagrees with what the customer is being chased for.
    """
    start, end = month_range(year, month)
    return {
        **_ledger(customer, start, end),
        "heading": f"Statement for {start.strftime('%B %Y')}",
        "period": f"{year:04d}-{month:02d}",
        "period_label": start.strftime("%B %Y"),
        # Inclusive, for display. `end` above is the exclusive bound used
        # for filtering, and showing it would date the statement to the 1st
        # of the following month.
        "end": end - datetime.timedelta(days=1),
    }


#: How many months of movements a rolling statement prints in full.
#: Everything older is collapsed into the brought-forward line.
ROLLING_MONTHS = 12


def window_start(as_at, months=ROLLING_MONTHS):
    """First day of the month `months - 1` months before `as_at`'s.

    months=12 with as_at in October 2026 gives 1 November 2025: twelve
    calendar months of movements, the last of them the month in progress.
    Arithmetic on a month index rather than on days, so it does not care
    about month lengths or leap years.
    """
    if months < 1:
        raise ValueError("a rolling statement covers at least one month")
    index = as_at.year * 12 + (as_at.month - 1) - (months - 1)
    return datetime.date(index // 12, index % 12 + 1, 1)


def rolling_statement(customer, as_at=None, months=ROLLING_MONTHS):
    """The account as it stands TODAY, with the last `months` of movements.

    THIS IS WHAT THE MONTHLY JOB SENDS.

    WHY NOT A CALENDAR MONTH

    A calendar-month statement closes at the month end. Sent on the 2nd it
    reports what was owed on the last day of the previous month -- and by
    the time it lands, the 1st has already invoiced, so the figure on the
    page is not the figure on the account. The customer's question is
    "what do I owe", and a month-end closing balance does not answer it.
    That gap is one phone call per customer per month.

    This closes at `as_at` INCLUSIVE, so the closing balance is the same
    derived figure the reconciliation gate checks against the stored
    balance. Where an account reconciles, the number at the foot of the
    page and the number we are chasing them for are the same number by
    construction rather than by coincidence.

    WHY NOT THE WHOLE LEDGER

    Because it never stops growing. The account here with thirty invoices
    and twenty-two payments is already two pages; in five years that
    document is unreadable and nobody checks page six. Everything before
    the window is collapsed into the brought-forward line, which carries
    exactly the same information in one row -- the balance is unaffected,
    only the detail is. The full history remains a button away as the
    Account Statement, which is a different document answering a
    different question.

    `as_at` is injectable so tests can pin it. Nothing in production
    passes it.
    """
    as_at = as_at or timezone.localdate()
    start = window_start(as_at, months)
    # Inclusive of `as_at` itself: a payment captured this morning belongs
    # on a statement sent this afternoon.
    end = as_at + datetime.timedelta(days=1)
    return {
        **_ledger(customer, start, end),
        "heading": "Statement of Account",
        "period": as_at.strftime("%Y-%m"),
        "period_label": as_at.strftime("%d/%m/%Y"),
        "end": as_at,
        "months": months,
    }
