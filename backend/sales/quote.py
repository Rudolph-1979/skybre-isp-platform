"""The quote a lead gets (6 Oct 2026): a PDF on the company letterhead, and
optionally emailed to the lead. Logged on the lead's timeline rather than
in EmailLog -- a lead isn't a customer yet, and EmailLog hangs off one.
"""
from decimal import Decimal

from django.core.mail import EmailMultiAlternatives
from django.utils import timezone
from django.utils.html import escape, strip_tags

from .models import LeadNote
from .workflow import FlowError, quote_number, quote_totals


def _pct(value):
    """10.00 -> 10, 12.50 -> 12.5"""
    text = f"{Decimal(value):f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _d(iso):
    import datetime

    return datetime.date.fromisoformat(iso).strftime("%d/%m/%Y")


def _r(value):
    return f"R {Decimal(str(value)):.2f}"


def calculation_lines(lead, totals):
    """The sums behind the totals, in words, for the quote (Rudolph, 9 Oct
    2026: "Lets show calc description on the quote"). Every price on a
    quote is excl. VAT; VAT is added once, on the total."""
    rate = _pct(totals["rate_pct"])
    lines = []
    parts = []
    if lead.interested_tariff_id:
        parts.append(f"{lead.interested_tariff.name} {_r(totals['plan_gross']['excl'])}")
    if totals["discount"]["monthly"]["excl"] > 0:
        parts.append(f"− discount {_r(totals['discount']['monthly']['excl'])}")
    for item in lead.quote_recurring or []:
        parts.append(f"{item['label']} {_r(item['amount'])}")
    m = totals["monthly"]
    lines.append(
        "Monthly: " + " + ".join(parts).replace("+ −", "−")
        + f" = {_r(m['excl'])} excl. VAT; + VAT {rate}% ({_r(m['vat'])}) = {_r(m['incl'])} incl. VAT."
    )
    period = totals["pro_rata_period"]
    if period:
        p = totals["pro_rata"]
        lines.append(
            f"Pro rata: {_r(m['excl'])} × {period['days']}/{period['month_days']} days "
            f"({_d(period['from'])} – {_d(period['to'])}) = {_r(p['excl'])} excl. VAT; "
            f"+ VAT {rate}% ({_r(p['vat'])}) = {_r(p['incl'])} incl. VAT."
        )
    if lead.quote_items:
        parts = [f"{item['label']} {_r(item['amount'])}" for item in lead.quote_items]
        if totals["discount"]["once_off"]["excl"] > 0:
            parts.append(f"− discount {_r(totals['discount']['once_off']['excl'])}")
        o = totals["once_off"]
        lines.append(
            "Once-off: " + " + ".join(parts).replace("+ −", "−")
            + f" = {_r(o['excl'])} excl. VAT; + VAT {rate}% ({_r(o['vat'])}) = {_r(o['incl'])} incl. VAT."
        )
    return lines


def render_quote_pdf(lead) -> bytes:
    from notifications.email_settings import get_email_config
    from notifications.pdf import _company_block, _logo_path, _render_pdf

    company, defaults = _company_block()
    totals = quote_totals(lead)
    items = []
    if lead.interested_tariff_id:
        items.append({"label": f"{lead.interested_tariff.name} (monthly)", "amount": totals["plan_gross"]["excl"]})
    disc = totals["discount"]
    reason = f" — {disc['reason']}" if disc["reason"] else ""
    if disc["monthly"]["excl"] > 0:
        spec = lead.quote_discount["monthly"]
        how = f"{_pct(spec['value'])}% off the plan" if spec["type"] == "percentage" else "off the plan"
        span = f", first {disc['months']} month{'s' if disc['months'] != 1 else ''}" if disc["months"] else ""
        items.append({"label": f"Discount: {how}{span}{reason} (monthly)", "amount": -disc["monthly"]["excl"]})
    for item in lead.quote_recurring or []:
        items.append({"label": f"{item['label']} (monthly)", "amount": item["amount"]})
    for item in lead.quote_items or []:
        items.append({"label": f"{item['label']} (once-off)", "amount": item["amount"]})
    if disc["once_off"]["excl"] > 0:
        spec = lead.quote_discount["once_off"]
        how = f"{_pct(spec['value'])}% off" if spec["type"] == "percentage" else "off"
        items.append({"label": f"Discount: {how} the once-off items{reason} (once-off)",
                      "amount": -disc["once_off"]["excl"]})
    # The old "Pro rata ..." summary row is gone: each monthly line now
    # carries its own first-month share, and a "First month total" row
    # closes the table (9 Oct 2026).
    # Next to each monthly line: its share of the first month (Rudolph,
    # 9 Oct 2026: "Next to each item line, ... Days (R .. per day)").
    start = lead.quote_start_date
    if start:
        import calendar
        from decimal import ROUND_HALF_UP

        month_days = calendar.monthrange(start.year, start.month)[1]
        days = month_days - start.day + 1
        for item in items:
            if item["label"].endswith("(monthly)"):
                amount = Decimal(str(item["amount"]))
                per_day = (amount / month_days).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                share = (amount * days / month_days).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                item["first_month"] = f"{days} days ({_r(per_day)} per day)"
                item["first_month_amount"] = f"{share:.2f}"
    context = {
        "company_name": get_email_config()["company_name"],
        "company": company,
        "logo_path": _logo_path(defaults),
        "generated_date": timezone.localdate(),
        "lead": lead,
        "number": quote_number(lead),
        "quote_date": timezone.localtime(lead.quote_sent_at).date() if lead.quote_sent_at else timezone.localdate(),
        "items": items,
        "totals": totals,
        "calculation": calculation_lines(lead, totals),
    }
    return _render_pdf("notifications/lead_quote_pdf.html", context)


def email_quote(lead, actor):
    """Send the quote PDF to the lead. Raises FlowError when it can't."""
    from notifications.email_settings import get_email_config, get_email_connection

    recipient = (lead.email or "").strip()
    if not recipient:
        raise FlowError("This lead has no email address — add one, or download the PDF and send it yourself.")
    config = get_email_config()
    company = config["company_name"]
    number = quote_number(lead)
    totals = quote_totals(lead)
    plan = lead.interested_tariff.name if lead.interested_tariff_id else "your internet service"
    body = (
        f"<p>Hi {escape(lead.full_name)},</p>"
        f"<p>Thank you for your interest. Our quote <strong>{number}</strong> for "
        f"<strong>{escape(plan)}</strong> is attached: R {totals['monthly']['incl']} per month incl. VAT"
        + (f", plus R {totals['once_off']['incl']} once-off incl. VAT" if lead.quote_items else "")
        + "."
        + (f" After the {totals['discount']['months']}-month discount it is R "
           f"{totals['discount']['monthly_after']['incl']} per month incl. VAT."
           if totals["discount"]["monthly_after"] else "")
        + (f" Your service starts on {lead.quote_start_date:%d %B %Y}: the rest of that month is charged pro rata "
           f"(R {totals['pro_rata']['incl']} incl. VAT), then monthly from the 1st."
           if totals["pro_rata_period"] else "")
        + (f" It is valid until {lead.quote_valid_until:%d %B %Y}." if lead.quote_valid_until else "")
        + "</p><p>Reply to this email to accept it, or if you have any questions.</p>"
        f"<p>{escape(company)}</p>"
    )
    pdf = render_quote_pdf(lead)
    try:
        message = EmailMultiAlternatives(
            subject=f"Quote {number} — {company}",
            body=strip_tags(body),
            from_email=config["from_email"],
            to=[recipient],
            connection=get_email_connection(),
        )
        message.attach_alternative(body, "text/html")
        message.attach(f"{number}.pdf", pdf, "application/pdf")
        message.send()
    except Exception as exc:  # noqa: BLE001
        LeadNote.objects.create(lead=lead, author=actor, kind=LeadNote.Kind.SYSTEM,
                                body=f"Quote {number} email to {recipient} FAILED: {exc}")
        raise FlowError(f"The quote was saved, but the email didn't go: {exc}")
    LeadNote.objects.create(lead=lead, author=actor, kind=LeadNote.Kind.EMAIL,
                            body=f"Quote {number} emailed to {recipient}.")
    return recipient
