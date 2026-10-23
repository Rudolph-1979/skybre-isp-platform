"""Cancelling a service (#203, Rudolph 20 Oct 2026): "a service cancellation
option which will work out pro rata credit if required although one month
notice is always required except if the client cant be serviced anymore on
one service and needs a new service".

Two kinds, chosen by Admin / Management on the client's Services tab:

* NOTICE -- the client gave notice. The service ends ONE CALENDAR MONTH
  after the notice date (notice 20 Oct -> end date 20 Nov: last day of
  service 19 Nov, cut at 00:00 on the 20th). Staff may pick a later end
  date, never an earlier one.
* CANNOT_SERVICE -- the client can't be serviced on this line any more and
  moves to a NEW service of theirs (e.g. wireless -> Vuma fibre). No notice:
  the end date is any day from 31 days ago, and the new service is named.

Service.end_date is what already ends a service (billing.cancellations, the
nightly job: terminates it on that date, and marks the client Inactive when
it was their last). An end date of today or earlier ends it right away.

THE MONEY, both ways round:

* The period the end date falls in is NOT INVOICED YET (the usual notice
  case: notice in October, end 20 Nov, November not billed yet) -- the
  recurring run bills that last period pro rata, only up to the end date
  (billing.invoice_lines.plan_invoice_lines). No credit is needed.
* It IS INVOICED ALREADY (no-notice case, or a backdated notice) -- the
  unused days of each such invoice are worked out (amount incl. VAT x unused
  days / days in the period) and a CREDIT REQUEST is raised for Management
  to approve, aimed at that invoice, like any other credit.

A period is only known for a tariff line with From/Till (a hand-raised pro
rata invoice) or an invoice made by a recurring billing run (run date + the
client's payment period). Anything else is listed as not worked out.
"""
import calendar
import datetime
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

MAX_BACKDATE_DAYS = 31
CENT = Decimal("0.01")
_PERIOD_MONTHS = {"monthly": 1, "quarterly": 3, "biannually": 6, "annually": 12}


class CancelError(Exception):
    pass


def add_months(day, months):
    """Same day N months later, clamped to the month's last day (31 Jan + 1
    month = 28/29 Feb)."""
    m = day.month - 1 + months
    y, m = day.year + m // 12, m % 12 + 1
    return datetime.date(y, m, min(day.day, calendar.monthrange(y, m)[1]))


def notice_end_date(notice_date):
    return add_months(notice_date, 1)


def period_months(customer):
    from .models import CustomerBillingConfig as BillingConfig

    return _PERIOD_MONTHS.get(BillingConfig.for_customer(customer).payment_period, 1)


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def dmy(day):
    return day.strftime("%d/%m/%Y")


def _item_incl_vat(item):
    excl = Decimal(item.quantity) * Decimal(item.unit_price)
    return excl * (1 + Decimal(item.tax_rate_pct) / 100)


def invoiced_periods(service):
    """[(invoice, period_start, period_end_exclusive, amount_incl_vat)] for
    every real invoice that charged this service, plus [invoice] for those
    whose period isn't known."""
    from .models import Invoice, InvoiceItem

    items = (
        InvoiceItem.objects.filter(service=service)
        .exclude(invoice__status__in=list(Invoice.PRE_INVOICE_STATUSES) + [Invoice.Status.CANCELLED, Invoice.Status.DRAFT])
        .filter(invoice__credit_request__isnull=True)
        .select_related("invoice", "invoice__created_by_run")
        .order_by("invoice__date_created", "invoice_id", "pk")
    )
    by_invoice = {}
    for item in items:
        by_invoice.setdefault(item.invoice_id, (item.invoice, []))[1].append(item)

    months = None
    known, unknown = [], []
    for invoice, lines in by_invoice.values():
        dated = [i for i in lines if i.period_start and i.period_end]
        if dated:
            start = min(i.period_start for i in dated)
            end = max(i.period_end for i in dated) + datetime.timedelta(days=1)
        elif invoice.created_by_run_id:
            if months is None:
                months = period_months(service.customer)
            start = invoice.created_by_run.run_date
            end = add_months(start, months)
        else:
            unknown.append(invoice)
            continue
        known.append((invoice, start, end, sum((_item_incl_vat(i) for i in lines), Decimal("0"))))
    return known, unknown


def credit_lines(service, end_date):
    """The pro rata credit owed if this service ends on `end_date` (the first
    day without service): one dict per invoice that charged days from
    `end_date` on."""
    known, unknown = invoiced_periods(service)
    lines = []
    for invoice, start, end, amount in known:
        unused_from = max(end_date, start)
        unused = (end - unused_from).days
        total = (end - start).days
        if unused <= 0 or total <= 0 or amount <= 0:
            continue
        credit = money(amount * unused / total)
        if credit <= 0:
            continue
        lines.append({
            "invoice": invoice,
            "invoice_number": invoice.number,
            "from": unused_from,
            "to": end - datetime.timedelta(days=1),
            "days": unused,
            "period_days": total,
            "invoice_amount": money(amount),
            "amount": credit,
        })
    return lines, unknown


def run_period_end(customer, run_date):
    """The day after the period a recurring invoice raised for `run_date`
    covers: run_date + one payment period, the same as billing.recurring's
    _add_period and the Finance preview's period."""
    return add_months(run_date, period_months(customer))


def final_period_fraction(service, run_date):
    """(days, period_days, last_day) when this service's end date falls
    inside the period a run on `run_date` bills, else None. The run then
    charges only days / period_days of it."""
    end_date = service.end_date
    if not end_date or end_date <= run_date:
        return None
    period_end = run_period_end(service.customer, run_date)
    if end_date >= period_end:
        return None
    return (end_date - run_date).days, (period_end - run_date).days, end_date - datetime.timedelta(days=1)


def last_period_pro_rata(service, end_date):
    """If the period `end_date` falls in is not invoiced yet, what the
    recurring run will bill for it: {"from", "to", "days", "period_days"} or
    None (already invoiced, or ends on a billing date)."""
    from .models import CustomerBillingConfig as BillingConfig

    known, _unknown = invoiced_periods(service)
    for _inv, start, end, _amount in known:
        if start < end_date <= end:
            return None
    config = BillingConfig.for_customer(service.customer)
    if _PERIOD_MONTHS.get(config.payment_period, 1) != 1:
        return None  # quarterly etc.: still billed pro rata by the run, just not predicted here
    last_day = end_date - datetime.timedelta(days=1)
    if config.use_date_of_customer_creation and service.customer.created_at:
        due_day = timezone.localtime(service.customer.created_at).day
    else:
        due_day = config.billing_day or 1

    def billing_date(year, month):
        return datetime.date(year, month, min(due_day, calendar.monthrange(year, month)[1]))

    start = billing_date(last_day.year, last_day.month)
    if start > last_day:
        prev = add_months(datetime.date(last_day.year, last_day.month, 1), -1)
        start = billing_date(prev.year, prev.month)
    end = add_months(start, 1)
    if end_date >= end or end_date <= start:
        return None
    return {"from": start, "to": last_day, "days": (end_date - start).days, "period_days": (end - start).days}


def plan(service, kind, *, notice_date=None, end_date=None, replacement=None, today=None):
    """Validate and work out a cancellation without saving anything."""
    from .models import Service

    today = today or timezone.localdate()
    if service.status == Service.Status.TERMINATED:
        raise CancelError("This service is already terminated.")
    earliest = today - datetime.timedelta(days=MAX_BACKDATE_DAYS)
    if kind == "notice":
        notice_date = notice_date or today
        if notice_date > today:
            raise CancelError("The notice date can't be in the future.")
        if notice_date < earliest:
            raise CancelError(f"The notice date can't be more than {MAX_BACKDATE_DAYS} days ago.")
        minimum = notice_end_date(notice_date)
        end_date = end_date or minimum
        if end_date < minimum:
            raise CancelError(
                f"One month's notice is required: the earliest end date for notice given on {dmy(notice_date)} "
                f"is {dmy(minimum)}."
            )
        replacement = None
    elif kind == "cannot_service":
        notice_date = None
        minimum = None
        if replacement is None:
            raise CancelError("Choose the client's new service this line is moving to.")
        if replacement.pk == service.pk or replacement.customer_id != service.customer_id:
            raise CancelError("The new service must be another service of this same client.")
        if replacement.status == Service.Status.TERMINATED:
            raise CancelError("The new service is terminated.")
        if end_date is None:
            start = replacement.start_date
            end_date = start if start and earliest <= start else today
        if end_date < earliest:
            raise CancelError(f"The end date can't be more than {MAX_BACKDATE_DAYS} days ago.")
    else:
        raise CancelError("Choose why the service is being cancelled.")
    if service.start_date and end_date <= service.start_date:
        raise CancelError(f"The end date must be after the service's start date ({dmy(service.start_date)}).")

    credits, unknown = credit_lines(service, end_date)
    return {
        "kind": kind,
        "notice_date": notice_date,
        "minimum_end_date": minimum,
        "end_date": end_date,
        "last_day": end_date - datetime.timedelta(days=1),
        "ends_now": end_date <= today,
        "replacement": replacement,
        "credits": credits,
        "credit_total": sum((c["amount"] for c in credits), Decimal("0")),
        "unknown_invoices": [i.number for i in unknown],
        "last_period": last_period_pro_rata(service, end_date),
    }


def end_service_now(service):
    """What the nightly job does for a due service, for this one now:
    terminate it (signals cut the line), then mark the client Inactive if
    nothing of theirs is left running."""
    from customers.models import Customer

    from .cancellations import customers_fully_ended
    from .models import Service

    service.status = Service.Status.TERMINATED
    service.save()
    for customer in customers_fully_ended([service.customer_id]):
        customer.status = Customer.Status.INACTIVE
        customer.save()


@transaction.atomic
def commit(service, result, reason, user):
    from .models import CreditRequest, ServiceCancellation

    reason = (reason or "").strip()
    if not reason:
        raise CancelError("Give the reason for the cancellation.")
    if ServiceCancellation.objects.filter(service=service, status=ServiceCancellation.Status.ACTIVE).exists():
        raise CancelError("This service already has a cancellation booked. Withdraw it first.")
    cancellation = ServiceCancellation.objects.create(
        service=service,
        customer_id=service.customer_id,
        kind=result["kind"],
        notice_date=result["notice_date"],
        end_date=result["end_date"],
        previous_end_date=service.end_date,
        replacement_service=result["replacement"],
        reason=reason[:500],
        created_by=user,
    )
    kind_text = "notice given %s" % dmy(result["notice_date"]) if result["kind"] == "notice" else (
        "can't be serviced, moved to %s" % result["replacement"].tariff.name
    )
    for line in result["credits"]:
        credit = CreditRequest.objects.create(
            customer_id=service.customer_id,
            invoice=line["invoice"],
            amount=line["amount"],
            reason=(
                f"Pro rata credit — {service.tariff.name} cancelled ({kind_text}), last day {dmy(result['last_day'])}. "
                f"{line['invoice_number']}: {line['days']} of {line['period_days']} days unused "
                f"({dmy(line['from'])} to {dmy(line['to'])}). {reason}"
            )[:2000],
            requested_by=user,
        )
        cancellation.credit_requests.add(credit)
    service.end_date = result["end_date"]
    service.save(update_fields=["end_date"])
    if result["ends_now"]:
        end_service_now(service)
    return cancellation


@transaction.atomic
def withdraw(cancellation, user):
    """Undo a booked cancellation before its end date: the old end date comes
    back and its credit requests still pending are rejected."""
    from .models import CreditRequest, Service, ServiceCancellation

    if cancellation.status != ServiceCancellation.Status.ACTIVE:
        raise CancelError("This cancellation was already withdrawn.")
    service = cancellation.service
    if service.status == Service.Status.TERMINATED:
        raise CancelError("The service has already ended — add a new service instead.")
    now = timezone.now()
    for credit in cancellation.credit_requests.filter(status=CreditRequest.Status.PENDING):
        credit.status = CreditRequest.Status.REJECTED
        credit.decision_note = "Cancellation withdrawn"
        credit.decided_by = user
        credit.decided_at = now
        credit.save(update_fields=["status", "decision_note", "decided_by", "decided_at"])
    if service.end_date == cancellation.end_date:
        service.end_date = cancellation.previous_end_date
        service.save(update_fields=["end_date"])
    cancellation.status = ServiceCancellation.Status.WITHDRAWN
    cancellation.withdrawn_by = user
    cancellation.withdrawn_at = now
    cancellation.save(update_fields=["status", "withdrawn_by", "withdrawn_at"])
    return cancellation


def result_json(result, service):
    rep = result["replacement"]
    return {
        "service": service.pk,
        "kind": result["kind"],
        "notice_date": result["notice_date"].isoformat() if result["notice_date"] else None,
        "minimum_end_date": result["minimum_end_date"].isoformat() if result["minimum_end_date"] else None,
        "end_date": result["end_date"].isoformat(),
        "last_day": result["last_day"].isoformat(),
        "ends_now": result["ends_now"],
        "replacement_service": rep.pk if rep else None,
        "replacement_name": rep.tariff.name if rep else "",
        "credits": [
            {k: (v.isoformat() if isinstance(v, datetime.date) else str(v) if isinstance(v, Decimal) else v)
             for k, v in c.items() if k != "invoice"}
            for c in result["credits"]
        ],
        "credit_total": str(result["credit_total"]),
        "unknown_invoices": result["unknown_invoices"],
        "last_period": (
            {k: (v.isoformat() if isinstance(v, datetime.date) else v) for k, v in result["last_period"].items()}
            if result["last_period"] else None
        ),
    }
