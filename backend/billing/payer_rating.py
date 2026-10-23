"""How a client pays: early, on time, late or chronically late (21 Oct 2026,
Rudolph: "add a notification on the customer list to evaluate customer and
show if the customer is a constant late payer or a constant early or on time
payer" -- last 12 invoices, on time = paid on or before the due date, shown
as a column + filter on the Customers list).

Worked out from the records each time; nothing stored. For each of the
client's last 12 real invoices (not quotes, pro formas, drafts, cancelled or
credit notes), oldest first:

  * the day it was fully paid = the date of the payment / credit that
    completed it (a payment by its bank statement date when it came through
    Bank Feeds, else the day it was captured; credits by the day they were
    allocated);
  * days late = that day - the due date (negative = early);
  * still unpaid and past due: late by however long it is overdue so far;
  * still unpaid and not due yet, or marked paid with no record of what paid
    it (imported history): not counted.

Rating, once at least 3 invoices count:
  * more than half late            -> Late payer, or Chronic late when half
    or more were paid later than the client's blocking period (14 days if
    they have none);
  * otherwise more than half early -> Early payer;
  * otherwise                      -> On time.
Fewer than 3: "New" (not enough history).
"""
import datetime
from collections import defaultdict
from decimal import Decimal

from django.utils import timezone

LAST_N = 12
MIN_COUNTED = 3
DEFAULT_LATE_LIMIT = 14

LABELS = {
    "early": "Early payer",
    "on_time": "On time",
    "late": "Late payer",
    "chronic": "Chronic late",
    "new": "Not enough history",
}


def _local(d):
    if d is None:
        return None
    if isinstance(d, datetime.datetime):
        return timezone.localtime(d).date() if timezone.is_aware(d) else d.date()
    return d


def ratings(customer_ids, today=None):
    """{customer_id: {"code", "label", "detail", "counted", "early", "on_time",
    "late", "avg_late"}} for every id given."""
    from .models import CreditAllocation, CustomerBillingConfig, Invoice, PaymentAllocation
    try:
        from .models import CreditNoteAllocation
    except ImportError:  # pragma: no cover
        CreditNoteAllocation = None

    today = today or timezone.localdate()
    ids = list(customer_ids)
    if not ids:
        return {}

    real = (
        Invoice.objects.filter(customer_id__in=ids, total__gt=0)
        .exclude(status__in=list(Invoice.PRE_INVOICE_STATUSES) + [Invoice.Status.DRAFT, Invoice.Status.CANCELLED])
        .order_by("customer_id", "-date_due", "-pk")
        .values_list("pk", "customer_id", "date_due", "total", "paid_amount", "status")
    )
    per_customer = defaultdict(list)
    for pk, cid, due, total, paid, status in real:
        if len(per_customer[cid]) < LAST_N and due is not None:
            per_customer[cid].append((pk, due, Decimal(total or 0), Decimal(paid or 0), status))
    invoice_ids = [row[0] for rows in per_customer.values() for row in rows]

    # What paid each invoice, and when.
    events = defaultdict(list)
    # The day the money arrived: the bank statement's date when the payment
    # came through Bank Feeds (Payment.date is when it was CAPTURED), else
    # the payment's own date.
    for inv, captured, banked, amount in PaymentAllocation.objects.filter(invoice_id__in=invoice_ids).values_list(
        "invoice_id", "payment__date", "payment__bank_transaction__date", "amount"
    ):
        events[inv].append((_local(banked) or _local(captured), Decimal(amount or 0)))
    for inv, when, amount in CreditAllocation.objects.filter(invoice_id__in=invoice_ids).values_list(
        "invoice_id", "created_at", "amount"
    ):
        events[inv].append((_local(when), Decimal(amount or 0)))
    if CreditNoteAllocation is not None:
        for inv, when, amount in CreditNoteAllocation.objects.filter(invoice_id__in=invoice_ids).values_list(
            "invoice_id", "created_at", "amount"
        ):
            events[inv].append((_local(when), Decimal(amount or 0)))

    limits = dict(
        CustomerBillingConfig.objects.filter(customer_id__in=ids).values_list("customer_id", "blocking_period_days")
    )

    out = {}
    for cid in ids:
        late_limit = limits.get(cid)
        late_limit = DEFAULT_LATE_LIMIT if late_limit is None else late_limit
        early = on_time = late = very_late = 0
        late_days = []
        for pk, due, total, paid, status in per_customer.get(cid, []):
            settled = None
            running = Decimal("0")
            for when, amount in sorted((e for e in events.get(pk, []) if e[0] is not None), key=lambda e: e[0]):
                running += amount
                if running >= total:
                    settled = when
                    break
            if settled is None:
                if paid >= total or status == Invoice.Status.PAID:
                    continue          # paid, but nothing says when
                if due >= today:
                    continue          # not due yet
                days = (today - due).days
            else:
                days = (settled - due).days
            if days < 0:
                early += 1
            elif days == 0:
                on_time += 1
            else:
                late += 1
                late_days.append(days)
                if days > late_limit:
                    very_late += 1
        counted = early + on_time + late
        if counted < MIN_COUNTED:
            code = "new"
        elif late * 2 > counted:
            code = "chronic" if very_late * 2 >= counted else "late"
        elif early * 2 > counted:
            code = "early"
        else:
            code = "on_time"
        avg_late = round(sum(late_days) / len(late_days)) if late_days else 0
        if counted:
            detail = f"Last {counted} invoice{'s' if counted != 1 else ''}: {early} early, {on_time} on the day, {late} late"
            if late:
                detail += f" (on average {avg_late} day{'s' if avg_late != 1 else ''} late)"
        else:
            detail = "No invoices with a known payment date yet"
        out[cid] = {
            "code": code, "label": LABELS[code], "detail": detail, "counted": counted,
            "early": early, "on_time": on_time, "late": late, "avg_late": avg_late,
        }
    return out
