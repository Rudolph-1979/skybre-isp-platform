"""The sales flow Rudolph drew (6 Oct 2026):

    New -> Qualify -> Coverage check -> Quote -> Accept (client + install job)
                      Electronic LOS          \\-> Reject (Lost)
                      Scan LOS
                      Fibre check
    ... with Lost possible at every step, always with a reason.

Each step here does its own bookkeeping (timeline note, stage, linked
Scheduling job) so the screen, the API and a shell all behave alike.
"""
import datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from .models import Lead, LeadNote

RESULTS_FOR = {
    Lead.CoverageMethod.ELECTRONIC: (Lead.CoverageResult.PASS, Lead.CoverageResult.FAIL),
    Lead.CoverageMethod.SCAN: (Lead.CoverageResult.PASS, Lead.CoverageResult.FAIL),
    Lead.CoverageMethod.FIBRE: (Lead.CoverageResult.FIBRE_CONFIRMED, Lead.CoverageResult.FIBRE_UNAVAILABLE),
}

#: How long a quote stands by default.
DEFAULT_QUOTE_DAYS = 14


class FlowError(ValueError):
    """Something the user asked for that this step can't do -- the message
    is shown to them as is."""


def _note(lead, actor, body):
    LeadNote.objects.create(lead=lead, author=actor, kind=LeadNote.Kind.SYSTEM, body=body)


def _where(lead):
    bits = [b for b in (lead.address, lead.city) if b]
    if lead.latitude is not None and lead.longitude is not None:
        bits.append(f"GPS {lead.latitude}, {lead.longitude}")
    return ", ".join(bits)[:255]


def _who(user):
    if user is None:
        return ""
    return (user.first_name or user.username or "").strip()


# --- coverage ---------------------------------------------------------------


def book_scan(lead, actor, start=None, technician=None, hours=1):
    """Book a Site Visit job in Scheduling for a Scan LOS. With no time, it
    goes into the job pool for the office to place."""
    from scheduling.models import Job

    if lead.scan_job_id and lead.scan_job and lead.scan_job.deleted_at is None and lead.scan_job.status not in (
        Job.Status.COMPLETED, Job.Status.CANCELLED,
    ):
        job = lead.scan_job
    else:
        job = Job(job_type="site_visit", status=Job.Status.SCHEDULED)
    job.title = f"LOS scan — {lead.company_name or lead.full_name}"[:255]
    job.location = _where(lead)
    job.description = (
        f"Line-of-sight scan for sales lead #{lead.pk}.\n"
        f"Contact: {lead.full_name} {lead.phone}".strip()
        + (f"\nInterested in: {lead.interested_tariff.name}" if lead.interested_tariff_id else "")
    )
    if start is not None:
        job.start = start
        job.end = start + datetime.timedelta(hours=hours)
    job.save()
    if start is not None and technician is not None:
        job.assigned_technicians.set([technician])
    lead.scan_job = job
    return job


def record_coverage(lead, actor, method, result="", tower="", sector="", signal="", notes="",
                    partner=None, scan_start=None, scan_technician=None):
    """Choose how coverage is checked and, once known, what it found."""
    if lead.status not in (Lead.Status.NEW, Lead.Status.QUALIFIED, Lead.Status.COVERAGE, Lead.Status.QUOTED):
        raise FlowError("This lead is closed. Reopen it first.")
    if method not in RESULTS_FOR:
        raise FlowError("Choose Electronic LOS, Scan LOS or Fibre check.")
    if result and result not in RESULTS_FOR[method]:
        raise FlowError("That result doesn't go with that kind of check.")
    if result == Lead.CoverageResult.FIBRE_CONFIRMED and partner is None:
        raise FlowError("Fibre confirmed on which network? Pick the fibre partner.")

    with transaction.atomic():
        changed_method = lead.coverage_method != method
        result_before = (lead.coverage_method, lead.coverage_result)
        lead.coverage_method = method
        lead.coverage_result = result
        lead.coverage_tower = tower
        lead.coverage_sector = sector
        lead.coverage_signal = signal
        lead.coverage_notes = notes
        lead.coverage_partner = partner if method == Lead.CoverageMethod.FIBRE else None
        if result:
            lead.coverage_checked_at = timezone.now()
            lead.coverage_checked_by = actor
        else:
            lead.coverage_checked_at = None
            lead.coverage_checked_by = None
        booked = None
        if method == Lead.CoverageMethod.SCAN and not result:
            booked = book_scan(lead, actor, start=scan_start, technician=scan_technician)
        if lead.status in (Lead.Status.NEW, Lead.Status.QUALIFIED):
            lead.status = Lead.Status.COVERAGE
        lead.save()

        label = Lead.CoverageMethod(method).label
        if result and (method, result) != result_before:
            from .notify import scan_result

            scan_result(lead)
        if result:
            detail = ", ".join(b for b in (
                f"tower {tower}" if tower else "", f"sector {sector}" if sector else "",
                f"signal {signal}" if signal else "",
                f"on {partner.name}" if partner is not None and method == Lead.CoverageMethod.FIBRE else "",
            ) if b)
            _note(lead, actor, f"{label}: {Lead.CoverageResult(result).label}"
                  + (f" ({detail})" if detail else "") + (f" — {notes}" if notes else ""))
        elif booked is not None:
            when = (
                timezone.localtime(booked.start).strftime("%a %d %b %H:%M") if booked.start else "the job pool (no time yet)"
            )
            who = f" with {_who(scan_technician)}" if scan_technician else ""
            _note(lead, actor, f"Scan LOS booked: {when}{who}.")
        elif changed_method:
            _note(lead, actor, f"Coverage check: {label} chosen.")
    return lead


# --- quote ------------------------------------------------------------------


def _clean_items(items, link=None):
    """`link` keeps that key (a catalogue id) on each row when it's a number."""
    out = []
    for item in items or []:
        label = str((item or {}).get("label", "")).strip()
        if not label:
            continue
        try:
            amount = Decimal(str(item.get("amount", "0")).replace(",", "").strip() or "0").quantize(Decimal("0.01"))
        except (InvalidOperation, AttributeError):
            raise FlowError(f"“{label}”: the amount isn't a number.")
        if amount < 0:
            raise FlowError(f"“{label}”: the amount can't be negative.")
        row = {"label": label[:120], "amount": str(amount)}
        if link and str(item.get(link) or "").isdigit():
            row[link] = int(item[link])
        out.append(row)
    return out


def quote_totals(lead):
    """Monthly and once-off, excl. VAT, VAT, incl. VAT -- VAT at the plan's rate."""
    tariff = lead.interested_tariff
    rate = (tariff.tax_rate_pct if tariff else Decimal("0")) / Decimal("100")
    # The plan (less any discount) plus any recurring charges (IP rental...),
    # all monthly. Once-off less its discount.
    plan_gross = lead.plan_value
    monthly_off = lead.monthly_discount
    plan = plan_gross - monthly_off
    monthly = plan + lead.recurring_value
    once_off_gross = sum((Decimal(i["amount"]) for i in lead.quote_items or []), Decimal("0"))
    once_off_off = lead.once_off_discount
    once_off = once_off_gross - once_off_off
    spec = lead.quote_discount or {}
    months = (spec.get("monthly") or {}).get("months") if monthly_off else None

    def split(amount):
        vat = (amount * rate).quantize(Decimal("0.01"))
        return {"excl": amount.quantize(Decimal("0.01")), "vat": vat, "incl": (amount + vat).quantize(Decimal("0.01"))}

    pro = pro_rata(lead.quote_start_date, monthly)
    return {"monthly": split(monthly), "plan": split(plan), "plan_gross": split(plan_gross),
            "once_off": split(once_off), "once_off_gross": split(once_off_gross),
            "discount": {
                "monthly": split(monthly_off), "once_off": split(once_off_off),
                "months": months, "reason": spec.get("reason") or "",
                # What the client pays a month once a time-limited discount ends.
                "monthly_after": split(monthly + monthly_off) if months else None,
            },
            "pro_rata": split(pro["amount"]) if pro else split(Decimal("0")),
            "pro_rata_period": {k: v for k, v in (pro or {}).items() if k != "amount"} or None,
            "rate_pct": tariff.tax_rate_pct if tariff else 0}


def pro_rata(start, monthly):
    """The first, part month: from the start date to month end, inclusive,
    at the monthly price (excl. VAT) x days used / days in that month. From
    the 1st of the next month the client is on recurring billing. None when
    there's no start date, or it starts on the 1st (a whole month)."""
    import calendar
    from decimal import ROUND_HALF_UP

    if start is None or start.day == 1:
        return None
    month_days = calendar.monthrange(start.year, start.month)[1]
    days = month_days - start.day + 1
    amount = (Decimal(monthly) * days / month_days).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {
        "amount": amount,
        "from": start.isoformat(),
        "to": start.replace(day=month_days).isoformat(),
        "days": days,
        "month_days": month_days,
    }


def _clean_discount(data):
    """The discount block from the quote form, checked."""
    if not data:
        return {}
    if not isinstance(data, dict):
        raise FlowError("The discount isn't in the right shape.")
    out = {}
    for key, what in (("monthly", "Monthly"), ("once_off", "Once-off")):
        spec = data.get(key) or {}
        kind = spec.get("type") or "none"
        if kind == "none":
            continue
        if kind not in ("percentage", "amount"):
            raise FlowError(f"{what} discount: choose % or R.")
        try:
            value = Decimal(str(spec.get("value") or "0").replace(",", "").strip()).quantize(Decimal("0.01"))
        except InvalidOperation:
            raise FlowError(f"{what} discount: the amount isn't a number.")
        if value <= 0:
            continue
        if kind == "percentage" and value > 100:
            raise FlowError(f"{what} discount: a percentage can't be more than 100.")
        row = {"type": kind, "value": str(value)}
        if key == "monthly" and spec.get("months") not in (None, "", 0, "0"):
            try:
                months = int(spec["months"])
            except (TypeError, ValueError):
                raise FlowError("Monthly discount: for how many months?")
            if not 1 <= months <= 60:
                raise FlowError("Monthly discount: between 1 and 60 months, or blank for ongoing.")
            row["months"] = months
        out[key] = row
    if out and str(data.get("reason") or "").strip():
        out["reason"] = str(data["reason"]).strip()[:120]
    return out


def apply_quote(lead, tariff, items, valid_days=DEFAULT_QUOTE_DAYS, recurring=None, start_date=None, discount=None):
    """Put the quote's contents on `lead` WITHOUT saving -- shared by Save and
    by Preview, so the preview is exactly what would be saved."""
    if lead.status in Lead.CLOSED_STATUSES:
        raise FlowError("This lead is closed. Reopen it first.")
    if lead.coverage_result not in Lead.COVERAGE_OK:
        raise FlowError("Coverage isn't confirmed yet — record a passed LOS check or Fibre confirmed first.")
    if tariff is None:
        raise FlowError("Choose the plan you're quoting.")
    if lead.coverage_result == Lead.CoverageResult.FIBRE_CONFIRMED and lead.coverage_partner_id:
        if not tariff.available_to(lead.coverage_partner_id):
            raise FlowError(
                f"{tariff.name} isn't one of {lead.coverage_partner.name}'s plans. Quote one of theirs."
            )
    lead.interested_tariff = tariff
    # The quote prices the plan at the tariff's price. A "monthly value"
    # typed on the lead earlier is only a pipeline estimate, and it used to
    # win here -- a lead with 0 in it was quoted the plan at R 0.00
    # (Rudolph, 9 Oct 2026: "quotation plan pricing doesn't pull in"). A
    # cheaper or free deal is a discount on the quote, which says so.
    lead.estimated_monthly_value = None
    lead.quote_items = _clean_items(items)
    lead.quote_recurring = _clean_items(recurring, link="recurring_charge")
    lead.quote_start_date = start_date
    lead.quote_discount = _clean_discount(discount)
    # A rand discount bigger than what it comes off is a mistake, not a
    # credit: refuse it rather than quietly capping it.
    once_off_gross = sum((Decimal(i["amount"]) for i in lead.quote_items), Decimal("0"))
    for key, base, what in (("monthly", lead.plan_value, "the plan's price"),
                            ("once_off", once_off_gross, "the once-off items add up to")):
        spec = lead.quote_discount.get(key)
        if spec and spec["type"] == "amount" and Decimal(spec["value"]) > base:
            raise FlowError(f"{'Monthly' if key == 'monthly' else 'Once-off'} discount: more than {what}.")
    lead.quote_valid_until = timezone.localdate() + datetime.timedelta(days=max(1, int(valid_days or DEFAULT_QUOTE_DAYS)))
    return lead


def prepare_quote(lead, actor, tariff, items, valid_days=DEFAULT_QUOTE_DAYS, recurring=None, start_date=None,
                  discount=None):
    apply_quote(lead, tariff, items, valid_days, recurring, start_date, discount)
    lead.quote_sent_at = timezone.now()
    lead.status = Lead.Status.QUOTED
    lead.save()
    totals = quote_totals(lead)
    _note(lead, actor, f"Quote: {tariff.name}"
          + "".join(f" + {i['label']}" for i in lead.quote_recurring)
          + f", R {totals['monthly']['incl']} /month incl. VAT"
          + (f", once-off R {totals['once_off']['incl']} incl. VAT" if lead.quote_items else "")
          + (f", discount{' (' + lead.quote_discount['reason'] + ')' if lead.quote_discount.get('reason') else ''}"
             if lead.quote_discount else "")
          + (f", pro rata from {lead.quote_start_date:%d %b} R {totals['pro_rata']['incl']} incl. VAT"
             if totals["pro_rata_period"] else "")
          + f", valid until {lead.quote_valid_until:%d %b %Y}.")
    return lead


def quote_number(lead):
    return f"Q-L{lead.pk:05d}"


# --- the end of the road -----------------------------------------------------


def accept(lead, actor):
    """Accepted: create the client and put an Installation job in the pool."""
    from scheduling.models import Job

    if lead.status == Lead.Status.LOST:
        raise FlowError("This lead is lost. Reopen it first.")
    with transaction.atomic():
        customer = lead.convert_to_customer(actor=actor)
        lead.refresh_from_db()
        if lead.install_job_id is None:
            totals = quote_totals(lead)
            job = Job.objects.create(
                job_type="installation",
                customer=customer,
                title=f"Installation — {customer.full_name}"[:255],
                location=_where(lead),
                description="\n".join(b for b in (
                    f"From sales lead #{lead.pk} ({quote_number(lead)}).",
                    f"Plan: {lead.interested_tariff.name}" if lead.interested_tariff_id else "",
                    "Monthly extras: " + ", ".join(f"{i['label']} R {i['amount']}" for i in lead.quote_recurring)
                    if lead.quote_recurring else "",
                    f"Service starts {lead.quote_start_date:%d %b %Y}: pro rata R {totals['pro_rata']['incl']} incl. VAT "
                    f"to month end, then monthly billing" if totals["pro_rata_period"] else (
                        f"Service starts {lead.quote_start_date:%d %b %Y}" if lead.quote_start_date else ""),
                    "Once-off: " + ", ".join(f"{i['label']} R {i['amount']}" for i in lead.quote_items)
                    if lead.quote_items else "",
                    f"Coverage: {lead.get_coverage_method_display()} — {lead.get_coverage_result_display()}"
                    + (f", tower {lead.coverage_tower}" if lead.coverage_tower else "")
                    + (f", sector {lead.coverage_sector}" if lead.coverage_sector else "")
                    if lead.coverage_method else "",
                    f"Monthly R {totals['monthly']['incl']} incl. VAT",
                ) if b),
            )
            lead.install_job = job
            lead.save(update_fields=["install_job", "updated_at"])
            _note(lead, actor, "Accepted — installation job added to the job pool.")
            from .notify import lead_accepted

            lead_accepted(lead)
            # The contract to sign, when Configs -> Contracts says so.
            from contracts.services import schedule_auto_send

            schedule_auto_send(customer, "lead_accepted", actor, lead)
    return customer, lead.install_job


def reject(lead, actor, reason=Lead.LostReason.QUOTE_REJECTED, note=""):
    if lead.status in Lead.CLOSED_STATUSES:
        raise FlowError("This lead is already closed.")
    lead.status = Lead.Status.LOST
    lead.lost_reason = reason or Lead.LostReason.QUOTE_REJECTED
    lead.next_follow_up = None
    lead.save()
    _note(lead, actor, f"Lost at {Lead.Status(lead.lost_at_stage).label if lead.lost_at_stage else 'New'}: "
          f"{lead.get_lost_reason_display()}" + (f" — {note}" if note else ""))
    return lead


# --- what to do next ----------------------------------------------------------


def next_action(lead, today=None):
    """{"text", "detail", "late"} for the leads list's Next action column."""
    today = today or timezone.localdate()

    def age(dt):
        return (today - timezone.localtime(dt).date()).days if dt else 0

    if lead.status == Lead.Status.NEW:
        days = age(lead.created_at)
        return {"text": "Qualify, or mark lost", "detail": f"new {days} day{'s' if days != 1 else ''} ago" if days else "new today",
                "late": days >= 1}
    if lead.status == Lead.Status.QUALIFIED:
        days = age(lead.updated_at)
        return {"text": "Do a coverage check", "detail": f"qualified {days} day{'s' if days != 1 else ''} ago" if days else "qualified today",
                "late": days >= 2}
    if lead.status == Lead.Status.COVERAGE:
        if lead.coverage_result in Lead.COVERAGE_OK:
            days = age(lead.coverage_checked_at)
            return {"text": "Send quote", "detail": "today" if not days else f"coverage confirmed {days} days ago",
                    "late": days >= 2}
        if lead.coverage_result in (Lead.CoverageResult.FAIL, Lead.CoverageResult.FIBRE_UNAVAILABLE):
            other = "a scan may still find a path" if lead.coverage_method != Lead.CoverageMethod.SCAN else "or check for fibre"
            return {"text": "Try another check, or mark lost", "detail": other, "late": False}
        if lead.coverage_method == Lead.CoverageMethod.SCAN:
            job = lead.scan_job
            if job is None or job.deleted_at is not None:
                return {"text": "Book the scan", "detail": "no scan job", "late": True}
            if job.start is None:
                return {"text": "Schedule the scan", "detail": "in the job pool", "late": False}
            if timezone.localtime(job.end or job.start).date() < today or job.status == "completed":
                return {"text": "Record scan result", "detail": f"scan was {timezone.localtime(job.start):%a %d %b}",
                        "late": True}
            return {"text": "Wait for scan", "detail": f"{timezone.localtime(job.start):%a %d %b %H:%M}", "late": False}
        label = {"electronic": "Do electronic LOS", "fibre": "Check for fibre"}.get(lead.coverage_method, "Do a coverage check")
        days = age(lead.updated_at)
        return {"text": label, "detail": f"waiting {days} day{'s' if days != 1 else ''}" if days else "today",
                "late": days >= 2}
    if lead.status == Lead.Status.QUOTED:
        if lead.quote_valid_until and lead.quote_valid_until < today:
            return {"text": "Quote expired — chase or re-quote", "detail": f"was valid to {lead.quote_valid_until:%d %b}",
                    "late": True}
        return {"text": "Waiting for their answer",
                "detail": f"valid to {lead.quote_valid_until:%d %b}" if lead.quote_valid_until else "",
                "late": bool(lead.follow_up_is_due)}
    return {"text": "", "detail": "", "late": False}
