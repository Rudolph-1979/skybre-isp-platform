"""PDF generation for emailed documents (invoice / statement attachments).

Uses xhtml2pdf (pure-Python, no system libraries like Cairo/Pango needed —
important because it has to build cleanly in the slim Docker image), driven
off plain Django templates so the layout is easy to tweak later.
"""

import io
import os
from decimal import Decimal

from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone
from xhtml2pdf import pisa


def _link_callback(uri, rel):
    """Resolve url() and src= references to real filesystem paths.

    xhtml2pdf has NO default resolver, so without this every @font-face and
    every <img> silently fails: the PDF still renders, just in Helvetica with
    no logo. That is the kind of failure nobody notices until a customer asks
    why their invoice looks different, so it is wired up explicitly.

    Handles three cases: MEDIA_URL paths (the uploaded logo), STATIC_URL
    paths, and plain absolute filesystem paths (the DejaVu fonts). Anything
    unrecognised is handed back untouched for xhtml2pdf to deal with.
    """
    if not uri:
        return uri
    if uri.startswith("file://"):
        uri = uri[len("file://"):]

    media_url = getattr(settings, "MEDIA_URL", "") or ""
    static_url = getattr(settings, "STATIC_URL", "") or ""

    path = None
    if media_url and uri.startswith(media_url):
        path = os.path.join(settings.MEDIA_ROOT, uri[len(media_url):])
    elif static_url and uri.startswith(static_url):
        root = getattr(settings, "STATIC_ROOT", None)
        if root:
            path = os.path.join(root, uri[len(static_url):])
    elif os.path.isabs(uri):
        path = uri

    if path and os.path.exists(path):
        return path
    return uri


def _render_pdf(template_name: str, context: dict) -> bytes:
    html = render_to_string(template_name, context)
    buffer = io.BytesIO()
    result = pisa.CreatePDF(src=html, dest=buffer, link_callback=_link_callback)
    if result.err:
        raise RuntimeError(f"Failed to render PDF from {template_name} ({result.err} error(s))")
    return buffer.getvalue()


def _company_block():
    """The supplier details printed on a tax invoice.

    A valid tax invoice has to carry the supplier's registered name, address
    and VAT number; none of that existed in the platform before, so it lives
    on BillingDefaults (a singleton) and is edited under Configs -> Billing.
    Falls back to the email config's company name so an invoice is never
    nameless on a fresh install.
    """
    from billing.models import BillingDefaults
    from .email_settings import get_email_config

    d = BillingDefaults.load()
    postal_bits = [b for b in (d.company_postal_code, d.company_country) if b]
    return {
        "name": d.company_legal_name or get_email_config()["company_name"],
        "address": d.company_address,
        "city": d.company_city,
        "postal_line": ", ".join(postal_bits),
        "vat_number": d.vat_number,
        "phone": d.company_phone,
        "email": d.company_email,
        "bank_name": d.bank_name,
        "bank_account_number": d.bank_account_number,
        "bank_branch_code": d.bank_branch_code,
    }, d


def _logo_path(defaults):
    """Absolute path to the logo, or None.

    Returned as a path rather than a URL because _link_callback resolves
    absolute paths directly and this avoids depending on MEDIA_URL being set
    to anything in particular.
    """
    if not defaults.logo:
        return None
    try:
        path = defaults.logo.path
    except (ValueError, NotImplementedError):
        return None
    return path if os.path.exists(path) else None


def render_invoice_pdf(invoice, company_name: str) -> bytes:
    """`company_name` is still accepted so existing callers don't change, but
    the document now prefers the registered company name from
    BillingDefaults -- a tax invoice needs the legal entity, not a brand."""
    from billing.models import Invoice, Payment

    company, defaults = _company_block()
    if company_name and not company["name"]:
        company["name"] = company_name

    titles = {
        Invoice.Status.QUOTE: ("Quotation", "Valid until"),
        Invoice.Status.PROFORMA: ("Pro forma invoice", "Due date"),
    }
    doc_title, due_label = titles.get(invoice.status, ("Tax invoice", "Due date"))

    # Per-line figures. InvoiceItem stores the ex-VAT unit price and a rate,
    # so the inclusive column is derived here rather than in the template --
    # Django templates can't multiply, and doing it in the view keeps the
    # rounding in one place.
    rows = []
    total_exclusive = Decimal("0")
    total_tax = Decimal("0")
    for item in invoice.items.all():
        excl_total = (item.quantity or 0) * (item.unit_price or Decimal("0"))
        rate = item.tax_rate_pct or Decimal("0")
        tax = (excl_total * rate / Decimal("100")).quantize(Decimal("0.01"))
        total_exclusive += excl_total
        total_tax += tax
        period = ""
        if item.period_start and item.period_end:
            period = f"{item.period_start:%d/%m/%Y} - {item.period_end:%d/%m/%Y}"
        rows.append({
            "item": item,
            "excl_price": item.unit_price,
            "excl_total": excl_total,
            "incl_total": excl_total + tax,
            "period": period,
        })

    # "Related items": payments actually applied to THIS invoice. Deliberately
    # not the customer's whole payment history -- an invoice should only show
    # what was allocated against it.
    related = [
        {
            "type": "Payment",
            "description": f"#{p.pk} - {p.note}" if p.note else f"#{p.pk}",
            "date": p.date,
            "amount": p.amount,
        }
        for p in Payment.objects.filter(invoice=invoice).order_by("date", "pk")
    ]

    customer = invoice.customer
    context = {
        "company_name": company["name"],
        "company": company,
        "logo_path": _logo_path(defaults),
        "generated_date": timezone.localdate(),
        "invoice": invoice,
        "customer": customer,
        "customer_display_name": customer.company_name or customer.full_name,
        "doc_title": doc_title,
        "due_label": due_label,
        "rows": rows,
        "total_exclusive": total_exclusive,
        "total_tax": total_tax,
        "related": related,
        "balance_due": invoice.total - invoice.paid_amount,
    }
    return _render_pdf("notifications/invoice_pdf.html", context)


def _payslip_context(line):
    """One payslip's worth of context, from a PayrollRunLine.

    Built here rather than in the template because the earnings and deductions
    lists are conditional -- a zero row is noise on a payslip, and an employee
    reading "Other deduction: 0.00" reasonably wonders what it was for. Only
    rows with an amount are included.
    """
    from payroll.models import PayrollRun, PayrollSettings, StaffProfile

    run = line.payroll_run
    payroll_settings = PayrollSettings.load()
    profile = getattr(line.staff, "staff_profile", None)

    # Falls back to the invoice company name so a payslip is never issued by
    # nobody, even before Payroll settings have been filled in.
    employer_name = payroll_settings.employer_name
    if not employer_name:
        company, _ = _company_block()
        employer_name = company["name"]

    earnings = []
    if line.base_pay:
        earnings.append({
            "label": "Basic pay" if line.pay_type == StaffProfile.PayType.SALARY else "Normal time",
            "detail": "" if line.pay_type == StaffProfile.PayType.SALARY
                      else f"{line.regular_hours:.2f} h @ {line.hourly_rate:.2f}",
            "amount": line.base_pay,
        })
    if line.overtime_pay:
        earnings.append({
            "label": "Overtime",
            "detail": f"{line.overtime_hours:.2f} h @ {line.overtime_rate:.2f}",
            "amount": line.overtime_pay,
        })
    if line.additional_amount:
        earnings.append({
            "label": line.additional_description or "Additional payment",
            "detail": "",
            "amount": line.additional_amount,
        })
    if not earnings:
        # A zero payslip is legitimate (no attendance in the period) and still
        # has to show something, or the earnings table renders empty.
        earnings.append({"label": "No earnings this period", "detail": "", "amount": Decimal("0.00")})

    deductions = []
    if line.paye:
        deductions.append({"label": "PAYE", "detail": "", "amount": line.paye})
    if line.uif_employee:
        deductions.append({"label": "UIF", "detail": "", "amount": line.uif_employee})
    if line.other_deduction_amount:
        deductions.append({
            "label": line.other_deduction_description or "Other deduction",
            "detail": "",
            "amount": line.other_deduction_amount,
        })

    employer_rows = []
    if line.uif_employer:
        employer_rows.append({"label": "UIF (employer contribution)", "amount": line.uif_employer})
    if line.sdl:
        employer_rows.append({"label": "SDL (Skills Development Levy)", "amount": line.sdl})

    return {
        "employer": {
            "name": employer_name,
            "address": payroll_settings.employer_address,
            "paye_reference": payroll_settings.paye_reference,
            "uif_reference": payroll_settings.uif_reference,
            "payslip_note": payroll_settings.payslip_note,
        },
        "period_start": run.period_start,
        "period_end": run.period_end,
        "payment_date": run.finalized_at.date() if run.finalized_at else run.period_end,
        # A draft run's figures can still change, so the payslip says so rather
        # than looking like a final document.
        "is_draft": run.status != PayrollRun.Status.FINALIZED,
        "staff_name": line.staff.get_full_name() or line.staff.username,
        "employee_number": profile.employee_number if profile else "",
        "id_number": profile.id_number if profile else "",
        "occupation": line.staff.get_role_display() if hasattr(line.staff, "get_role_display") else "",
        "pay_type_label": "Monthly salary" if line.pay_type == StaffProfile.PayType.SALARY else "Hourly",
        "earnings": earnings,
        "deductions": deductions,
        "employer_rows": employer_rows,
        "total_earnings": line.total_earnings,
        "total_deductions": line.total_deductions,
        "net_pay": line.net_pay,
        # Hours are only meaningful for someone paid by the hour; a salaried
        # employee's attendance doesn't drive their pay, so showing rates
        # against it would invite a query the numbers can't answer.
        "show_hours": line.pay_type != StaffProfile.PayType.SALARY,
        "regular_hours": line.regular_hours,
        "overtime_hours": line.overtime_hours,
        "hourly_rate": line.hourly_rate,
        "overtime_rate": line.overtime_rate,
        "base_pay": line.base_pay,
        "overtime_pay": line.overtime_pay,
        "line_notes": line.notes,
        "generated_on": timezone.localdate(),
    }


def render_payslip_pdf(lines) -> bytes:
    """One PDF for one or many payroll lines.

    Takes a sequence rather than a single line so the whole-run PDF and the
    per-employee one are the same code path, page-broken between employees --
    which means they can't drift into disagreeing about someone's net pay.
    """
    slips = [_payslip_context(line) for line in lines]
    if not slips:
        raise ValueError("Nothing to render a payslip from.")
    return _render_pdf("notifications/payslip_pdf.html", {"slips": slips})


def render_statement_pdf(customer, invoices, company_name: str) -> bytes:
    context = {
        "company_name": company_name,
        "generated_date": timezone.localdate(),
        "statement_date": timezone.localdate(),
        "customer": customer,
        "invoices": invoices,
        "balance": customer.balance,
    }
    return _render_pdf("notifications/statement_pdf.html", context)


def render_vat_report_pdf(vat_return: dict) -> bytes:
    """`vat_return` is exactly the dict expenses.views.build_vat_return()
    returns -- unlike the two functions above, this is downloaded
    directly from the browser (see expenses.views.VatReturnPdfView), not
    emailed as an attachment, so there's no separate company_name param;
    it's already inside vat_return."""
    context = {
        "company_name": vat_return["company_name"],
        "generated_date": timezone.localdate(),
        "vat": vat_return,
    }
    return _render_pdf("notifications/vat_report_pdf.html", context)
