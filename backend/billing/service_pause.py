"""Pausing a service (#211, Rudolph 21 Oct 2026): "create me a option to pause
a service" / "This will pause billing as well".

Decided with Rudolph:
  * while paused the line is OFF completely -- the login is refused like a
    pending or terminated line (no walled garden), and the live session is
    dropped the moment it pauses;
  * it is NOT billed for the paused days, pro rata;
  * the resume date is optional: with one, the line comes back by itself
    that morning (the 04:15 scheduled-changes job); without one it stays
    paused until somebody presses Resume;
  * Admin / Management only, for pausing and resuming.

HOW IT IS KEPT. Service.status becomes "paused" while the pause is on (so
RADIUS, the router block list, the online/offline reports, the dashboard
counts and the blocking pass all treat it as off, with no special code), and
a ServicePause row records the dates. Billing reads the ServicePause rows,
not the status: a pause booked for later in a month, or a resume date inside
the month being billed, both have to be known before they happen.

  pause_from  first day OFF (today, or a later date = a booked pause);
  resume_on   planned first day back (blank = until resumed by hand);
  resumed_on  the day it actually came back.

THE MONEY.
  * A period not invoiced yet: the recurring run bills only the days the
    line is not paused (billing.invoice_lines) -- nothing at all for a
    period that is paused throughout, pro rata for a period the pause
    starts or ends in.
  * Days already invoiced when the pause is booked: a CREDIT REQUEST per
    invoice for Management to approve, worked out like a cancellation's
    (#203): amount incl. VAT x paused days / days in the period. An open
    pause is credited to the end of each invoiced period.
  * Resumed EARLY by hand: credit requests still pending are rejected and
    raised again for the days actually paused. One already APPROVED is left
    as it is (the History says so) -- adjust by hand if it matters.
    Days from the resume to the end of a period the run has already billed
    (or skipped) while the line was paused are not invoiced automatically,
    the same rule as a service starting mid-month: the resume tells you the
    days and roughly what they cost, to invoice pro rata by hand
    (Finance -> New invoice -> Pro rata).
  * Not worked out automatically: two pauses, or a pause and a
    cancellation, inside the same already-invoiced period -- check the
    credit by hand.

THE CLIENT. Pausing a line never changes the client's own status (a client
whose only line is paused stays Active/Online), and paused lines are not
"ended" -- the client is not made Inactive. A client Suspended / Bad Debt /
Inactive while a line is paused: the pause ends as usual, but the line comes
back SUSPENDED with the client (restored when the client is), never Active.
Only an Active service can be paused (a suspended one: sort the suspension
out first).
"""
import calendar
import datetime
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .service_cancel import add_months, dmy, invoiced_periods, money, run_period_end

ONE_DAY = datetime.timedelta(days=1)


class PauseError(Exception):
    pass


# ---------------------------------------------------------------------------
# The paused days
# ---------------------------------------------------------------------------

def _interval(status, pause_from, resume_on, resumed_on):
    from .models import ServicePause

    end = resumed_on if status == ServicePause.Status.ENDED else resume_on
    return pause_from, end


def intervals(service):
    """[(first day off, first day back or None)] for every pause of this
    service that counts (withdrawn ones don't)."""
    cached = getattr(service, "_pause_intervals", None)
    if cached is not None:
        return cached
    from .models import ServicePause

    rows = (
        ServicePause.objects.filter(service=service)
        .exclude(status=ServicePause.Status.WITHDRAWN)
        .values_list("status", "pause_from", "resume_on", "resumed_on")
    )
    result = [_interval(*row) for row in rows]
    service._pause_intervals = result
    return result


def attach_intervals(services):
    """Load every service's pauses in ONE query (a billing run asks for each
    service of each client)."""
    from .models import ServicePause

    services = list(services)
    by_service = {s.pk: [] for s in services}
    rows = (
        ServicePause.objects.filter(service_id__in=list(by_service))
        .exclude(status=ServicePause.Status.WITHDRAWN)
        .values_list("service_id", "status", "pause_from", "resume_on", "resumed_on")
    )
    for service_id, *rest in rows:
        by_service[service_id].append(_interval(*rest))
    for s in services:
        s._pause_intervals = by_service[s.pk]
    return services


def paused_ranges(service, start, end):
    """The paused stretches inside [start, end), as [(first off, first back)]."""
    out = []
    for p_from, p_end in intervals(service):
        a = max(start, p_from)
        b = end if p_end is None else min(end, p_end)
        if b > a:
            out.append((a, b))
    out.sort()
    merged = []
    for a, b in out:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def paused_days(service, start, end):
    return sum((b - a).days for a, b in paused_ranges(service, start, end))


def ranges_text(ranges):
    return ", ".join(f"{dmy(a)} to {dmy(b - ONE_DAY)}" for a, b in ranges)


def charge_fraction(service, run_date):
    """What a recurring invoice raised on `run_date` charges for this
    service, when it is NOT the whole period: {"days", "period_days",
    "last_day", "paused"} -- days 0 = nothing to bill. None = full period.

    Combines the two things that shorten a period: the service ending inside
    it (#203, Service.end_date) and pauses (#211)."""
    if not service.end_date and not intervals(service):
        return None  # the usual case, without working out the period
    period_end = run_period_end(service.customer, run_date)
    window_end = period_end
    if service.end_date and run_date < service.end_date < period_end:
        window_end = service.end_date
    ranges = paused_ranges(service, run_date, window_end)
    period_days = (period_end - run_date).days
    days = (window_end - run_date).days - sum((b - a).days for a, b in ranges)
    if days >= period_days:
        return None
    return {"days": max(days, 0), "period_days": period_days, "last_day": window_end - ONE_DAY, "paused": ranges}


# ---------------------------------------------------------------------------
# Credit for days already invoiced
# ---------------------------------------------------------------------------

def credit_lines(service, start, end, only_invoice_ids=None):
    """Pro rata credit for the paused days [start, end) (end None = to the end
    of each period) that are on invoices already raised: one dict per
    invoice. Days after the service's end date are left out (a cancellation
    credits those)."""
    known, unknown = invoiced_periods(service)
    lines = []
    for invoice, p_start, p_end, amount in known:
        if only_invoice_ids is not None and invoice.pk not in only_invoice_ids:
            continue
        a = max(start, p_start)
        b = p_end if end is None else min(end, p_end)
        if service.end_date:
            b = min(b, service.end_date)
        days, total = (b - a).days, (p_end - p_start).days
        if days <= 0 or total <= 0 or amount <= 0:
            continue
        credit = money(amount * days / total)
        if credit <= 0:
            continue
        lines.append({
            "invoice": invoice,
            "invoice_number": invoice.number,
            "from": a,
            "to": b - ONE_DAY,
            "days": days,
            "period_days": total,
            "invoice_amount": money(amount),
            "amount": credit,
        })
    if only_invoice_ids is not None:
        unknown = [i for i in unknown if i.pk in only_invoice_ids]
    return lines, unknown


def _raise_credits(pause, service, lines, reason, user):
    from .models import CreditRequest

    until = f"until {dmy(pause.resume_on - ONE_DAY)}" if pause.resume_on else "until resumed"
    made = []
    for line in lines:
        credit = CreditRequest.objects.create(
            customer_id=service.customer_id,
            invoice=line["invoice"],
            amount=line["amount"],
            reason=(
                f"Pause credit — {service.tariff.name} paused from {dmy(pause.pause_from)} {until}. "
                f"{line['invoice_number']}: {line['days']} of {line['period_days']} days paused "
                f"({dmy(line['from'])} to {dmy(line['to'])}). {reason}"
            )[:2000],
            requested_by=user,
        )
        pause.credit_requests.add(credit)
        made.append(credit)
    return made


def _reject_pending(pause, user, note):
    from .models import CreditRequest

    now = timezone.now()
    rejected = []
    for credit in pause.credit_requests.filter(status=CreditRequest.Status.PENDING):
        credit.status = CreditRequest.Status.REJECTED
        credit.decision_note = note
        credit.decided_by = user
        credit.decided_at = now
        credit.save(update_fields=["status", "decision_note", "decided_by", "decided_at"])
        rejected.append(credit)
    return rejected


# ---------------------------------------------------------------------------
# Pause
# ---------------------------------------------------------------------------

def current_pause(service):
    from .models import ServicePause

    return (
        ServicePause.objects.filter(
            service=service, status__in=[ServicePause.Status.SCHEDULED, ServicePause.Status.ACTIVE]
        )
        .order_by("-created_at")
        .first()
    )


def plan(service, *, pause_from=None, resume_on=None, today=None):
    """Validate and work out a pause without saving anything."""
    from .models import Service

    today = today or timezone.localdate()
    pause_from = pause_from or today
    if service.status != Service.Status.ACTIVE:
        if service.status == Service.Status.PAUSED:
            raise PauseError("This service is already paused.")
        if service.status == Service.Status.SUSPENDED:
            raise PauseError("Only an active service can be paused — this one is suspended. Sort the suspension out first.")
        raise PauseError(f"Only an active service can be paused — this one is {service.get_status_display()}.")
    if current_pause(service) is not None:
        raise PauseError("This service already has a pause booked. Cancel that one first (Resume).")
    if pause_from < today:
        raise PauseError("The pause can't start in the past.")
    if resume_on is not None and resume_on <= pause_from:
        raise PauseError("The resume date must be after the first paused day.")
    if service.end_date and pause_from >= service.end_date:
        raise PauseError(f"This service ends on {dmy(service.end_date)} — the pause must start before that.")
    if service.start_date and pause_from < service.start_date:
        raise PauseError(f"The service only starts on {dmy(service.start_date)}.")

    credits, unknown = credit_lines(service, pause_from, resume_on)
    return {
        "pause_from": pause_from,
        "resume_on": resume_on,
        "last_day_on": pause_from - ONE_DAY,
        "starts_now": pause_from <= today,
        "credits": credits,
        "credit_total": sum((c["amount"] for c in credits), Decimal("0")),
        "unknown_invoices": [i.number for i in unknown],
    }


def start_pause(pause):
    """Switch the line off: status Paused, through .save() so the signals
    rewrite RADIUS to a reject, block it on the router and drop the live
    session."""
    from .models import Service, ServicePause

    service = pause.service
    if service.status in (Service.Status.TERMINATED, Service.Status.PENDING):
        pause.status = ServicePause.Status.WITHDRAWN
        pause.ended_at = timezone.now()
        pause.save(update_fields=["status", "ended_at"])
        return False
    service.status = Service.Status.PAUSED
    service.save()
    pause.status = ServicePause.Status.ACTIVE
    pause.save(update_fields=["status"])
    return True


@transaction.atomic
def commit(service, result, reason, user):
    from .models import ServicePause

    reason = (reason or "").strip()
    if not reason:
        raise PauseError("Give the reason for the pause.")
    if current_pause(service) is not None:
        raise PauseError("This service already has a pause booked. Cancel that one first (Resume).")
    pause = ServicePause.objects.create(
        service=service,
        customer_id=service.customer_id,
        status=ServicePause.Status.SCHEDULED,
        pause_from=result["pause_from"],
        resume_on=result["resume_on"],
        reason=reason[:500],
        created_by=user,
    )
    _raise_credits(pause, service, result["credits"], reason, user)
    if result["starts_now"]:
        start_pause(pause)
    service._pause_intervals = None
    return pause


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------

def _billing_period(customer, day):
    """[start, end) of the MONTHLY billing period `day` falls in, or None for
    another payment period (worked out by hand then)."""
    from .models import CustomerBillingConfig as BillingConfig

    config = BillingConfig.for_customer(customer)
    if (config.payment_period or "monthly") != "monthly":
        return None
    if config.use_date_of_customer_creation and customer.created_at:
        due_day = timezone.localtime(customer.created_at).day
    else:
        due_day = config.billing_day or 1

    def billing_date(year, month):
        return datetime.date(year, month, min(due_day, calendar.monthrange(year, month)[1]))

    start = billing_date(day.year, day.month)
    if start > day:
        prev = add_months(datetime.date(day.year, day.month, 1), -1)
        start = billing_date(prev.year, prev.month)
    return start, add_months(start, 1)


def unbilled_after_resume(service, pause, back_on):
    """Days from `back_on` to the end of its billing period that no invoice
    will charge, because the run for that period happened while the line was
    still paused: {"from", "to", "days", "period_days", "amount_incl_vat"} or
    None. A period invoiced BEFORE the pause was booked is covered by the
    (recalculated) credit instead."""
    from .models import RecurringBillingRun

    period = _billing_period(service.customer, back_on)
    if period is None:
        return None
    p_start, p_end = period
    credited = set(pause.credit_requests.values_list("invoice_id", flat=True))
    known, _unknown = invoiced_periods(service)
    this_period = [inv for inv, s, e, _a in known if s <= back_on < e]
    if any(inv.pk in credited for inv in this_period):
        return None
    if this_period:
        # Billed pro rata while paused: charged from the PLANNED resume date.
        stop = min(pause.resume_on, p_end) if pause.resume_on else p_end
    else:
        ran = RecurringBillingRun.objects.filter(
            run_date__gte=p_start, run_date__lte=back_on, status=RecurringBillingRun.Status.PROCESSED,
        )
        if p_start == back_on or not ran.exists():
            return None
        stop = p_end
    if service.end_date:
        stop = min(stop, service.end_date)
    days, period_days = (stop - back_on).days, (p_end - p_start).days
    if days <= 0:
        return None
    tariff = service.tariff
    excl = service.billed_price(back_on, base_price=tariff.price)
    incl = Decimal(excl) * (1 + Decimal(tariff.tax_rate_pct) / 100)
    return {
        "from": back_on,
        "to": stop - ONE_DAY,
        "days": days,
        "period_days": period_days,
        "amount_incl_vat": money(incl * days / period_days),
    }


def _line_back_on(service):
    """Back on: Active, or Suspended together with the client when the client
    is Suspended / Bad Debt / Inactive (restored when the client is)."""
    from customers.models import Customer

    from .models import Service

    owner = Customer.objects.filter(pk=service.customer_id).only("status").first()
    if owner is not None and owner.status in Customer.OFF_STATUSES:
        service.status = Service.Status.SUSPENDED
        service.auto_suspended_with_customer = True
        service._customer_cascade = True
    else:
        service.status = Service.Status.ACTIVE
    service.save()


@transaction.atomic
def resume(pause, user, *, today=None, scheduled=False):
    """End a pause. A booked pause that hasn't started is withdrawn (its
    pending credit requests rejected). A running one ends today -- or on its
    resume date when the morning job does it (`scheduled`)."""
    from .models import CreditRequest, Service, ServicePause

    today = today or timezone.localdate()
    service = pause.service
    now = timezone.now()
    result = {"withdrawn": False, "back_on": None, "credits": [], "kept_credits": [], "unbilled": None,
              "line_status": service.status}

    if pause.status == ServicePause.Status.SCHEDULED:
        rejected = _reject_pending(pause, user, "Pause withdrawn before it started")
        pause.status = ServicePause.Status.WITHDRAWN
        pause.ended_by, pause.ended_at = user, now
        pause.save(update_fields=["status", "ended_by", "ended_at"])
        result["withdrawn"] = True
        result["rejected"] = [c.pk for c in rejected]
        result["kept_credits"] = list(
            pause.credit_requests.filter(status=CreditRequest.Status.APPROVED).values_list("pk", flat=True)
        )
        service._pause_intervals = None
        return result
    if pause.status != ServicePause.Status.ACTIVE:
        raise PauseError("This pause has already ended.")

    back_on = pause.resume_on if scheduled and pause.resume_on else today
    if back_on < pause.pause_from:
        back_on = pause.pause_from
    early = not scheduled and (pause.resume_on is None or back_on < pause.resume_on)

    if early:
        # Credits worked out on the planned (or open) end: redo them for the
        # days actually paused, on the same invoices.
        invoice_ids = set(pause.credit_requests.values_list("invoice_id", flat=True))
        approved = set(
            pause.credit_requests.filter(status=CreditRequest.Status.APPROVED).values_list("invoice_id", flat=True)
        )
        _reject_pending(pause, user, f"Pause ended early on {dmy(back_on)} — recalculated")
        lines, _unknown = credit_lines(service, pause.pause_from, back_on, only_invoice_ids=invoice_ids - approved)
        result["credits"] = _raise_credits(pause, service, lines, f"Resumed early on {dmy(back_on)}.", user)
        result["kept_credits"] = list(
            pause.credit_requests.filter(status=CreditRequest.Status.APPROVED).values_list("pk", flat=True)
        )

    pause.status = ServicePause.Status.ENDED
    pause.resumed_on = back_on
    pause.ended_by, pause.ended_at = user, now
    pause.save(update_fields=["status", "resumed_on", "ended_by", "ended_at"])
    service._pause_intervals = None

    if service.status == Service.Status.PAUSED:
        _line_back_on(service)
    result["line_status"] = service.status
    result["back_on"] = back_on
    if early:
        result["unbilled"] = unbilled_after_resume(service, pause, back_on)
    return result


# ---------------------------------------------------------------------------
# The morning job (billing.scheduled_changes)
# ---------------------------------------------------------------------------

def due_pause_starts(as_of, partner_ids=None):
    from .models import ServicePause

    qs = ServicePause.objects.filter(status=ServicePause.Status.SCHEDULED, pause_from__lte=as_of)
    if partner_ids:
        qs = qs.filter(service__customer__partner_id__in=partner_ids)
    return qs.select_related("service", "service__customer").order_by("pause_from", "pk")


def due_resumes(as_of, partner_ids=None):
    from .models import ServicePause

    qs = ServicePause.objects.filter(
        status=ServicePause.Status.ACTIVE, resume_on__isnull=False, resume_on__lte=as_of,
    )
    if partner_ids:
        qs = qs.filter(service__customer__partner_id__in=partner_ids)
    return qs.select_related("service", "service__customer").order_by("resume_on", "pk")


# ---------------------------------------------------------------------------
# API shapes
# ---------------------------------------------------------------------------

def _jsonable(value):
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def result_json(result, service):
    return {
        "service": service.pk,
        "pause_from": result["pause_from"].isoformat(),
        "resume_on": result["resume_on"].isoformat() if result["resume_on"] else None,
        "last_day_on": result["last_day_on"].isoformat(),
        "starts_now": result["starts_now"],
        "credits": [{k: _jsonable(v) for k, v in c.items() if k != "invoice"} for c in result["credits"]],
        "credit_total": str(result["credit_total"]),
        "unknown_invoices": result["unknown_invoices"],
    }


def resume_json(result):
    return {
        "withdrawn": result["withdrawn"],
        "back_on": result["back_on"].isoformat() if result["back_on"] else None,
        "line_status": result["line_status"],
        "credits": [
            {"id": c.pk, "amount": str(c.amount), "invoice_number": c.invoice.number if c.invoice_id else None}
            for c in result["credits"]
        ],
        "kept_credits": result["kept_credits"],
        "unbilled": {k: _jsonable(v) for k, v in result["unbilled"].items()} if result["unbilled"] else None,
    }
