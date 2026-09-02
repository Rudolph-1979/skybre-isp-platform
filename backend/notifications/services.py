"""Email-sending logic: render a stored EmailTemplate against a customer
(and optionally an invoice), attach a PDF where relevant, send it, and log
the result — used by both the single-customer send and the bulk send.
"""

import logging
import threading
import uuid

from django.core.mail import EmailMultiAlternatives
from django.template import Context, Template
from django.utils import timezone
from django.utils.html import strip_tags

from .email_settings import get_email_config, get_email_connection
from .models import EmailTemplate, EmailLog
from .pdf import render_invoice_pdf, render_statement_pdf

logger = logging.getLogger(__name__)


def build_context(template_key: str, customer, invoice=None, payment=None) -> dict:
    """Placeholders available to every template's subject/body — documented
    for staff in the Email Templates admin page. Keep this in sync with
    that page's "available placeholders" hint list.
    """
    cfg = get_email_config()
    ctx = {
        "company_name": cfg["company_name"],
        "customer_name": customer.full_name,
        "customer_id": customer.customer_id,
        "customer_email": customer.email,
        "portal_url": f"{cfg['site_url'].rstrip('/')}/login",
        # Whatever customer.balance is at the moment this is rendered --
        # for payment_received this is called after the payment has
        # already been applied, so it reflects the post-payment balance.
        "balance": f"{customer.balance:.2f}",
        "today": timezone.localdate(),
        "invoice_number": "",
        "invoice_total": "",
        "invoice_due_date": "",
        "payment_amount": "",
    }
    if template_key == EmailTemplate.Key.STATEMENT:
        ctx["statement_date"] = timezone.localdate()
    elif template_key in (EmailTemplate.Key.INVOICE, EmailTemplate.Key.QUOTE, EmailTemplate.Key.PROFORMA) and invoice is not None:
        ctx["invoice_number"] = invoice.number
        ctx["invoice_total"] = f"{invoice.total:.2f}"
        # For a Quote this is really "valid until", not a due date -- same
        # underlying field, reused as-is so template authors only need to
        # learn one placeholder name across all three document types.
        ctx["invoice_due_date"] = invoice.date_due
    elif template_key == EmailTemplate.Key.PAYMENT_REMINDER and invoice is not None:
        ctx["invoice_number"] = invoice.number
        ctx["invoice_due_date"] = invoice.date_due
    elif template_key == EmailTemplate.Key.PAYMENT_RECEIVED and payment is not None:
        ctx["payment_amount"] = f"{payment.amount:.2f}"
        if payment.invoice_id:
            ctx["invoice_number"] = payment.invoice.number
    return ctx


def render_template_strings(template: "EmailTemplate", context: dict):
    subject = Template(template.subject).render(Context(context))
    body = Template(template.body_html).render(Context(context))
    return subject, body


def preview_template(template_key: str, customer, invoice=None):
    template = EmailTemplate.objects.get(key=template_key)
    context = build_context(template_key, customer, invoice)
    return render_template_strings(template, context)


def _build_attachment(template_key: str, customer, invoice=None):
    """Returns (filename, pdf_bytes, mimetype) for template types that ship
    a PDF, or None for plain-text-only template types."""
    from billing.models import Invoice

    company_name = get_email_config()["company_name"]
    if template_key in (EmailTemplate.Key.INVOICE, EmailTemplate.Key.QUOTE, EmailTemplate.Key.PROFORMA):
        if invoice is None:
            raise ValueError("A document must be selected to send this email.")
        # Same PDF renderer for all three -- invoice_pdf.html itself
        # switches the heading/wording based on invoice.status.
        pdf_bytes = render_invoice_pdf(invoice, company_name)
        return f"{invoice.number}.pdf", pdf_bytes, "application/pdf"
    if template_key == EmailTemplate.Key.STATEMENT:
        # Quotes and pro formas aren't real invoices yet -- excluding them
        # keeps the statement an accurate record of actual billing, not a
        # mix of billed and merely-quoted amounts.
        invoices = list(
            Invoice.objects.filter(customer=customer)
            .exclude(status__in=Invoice.PRE_INVOICE_STATUSES)
            .order_by("-date_created")
        )
        pdf_bytes = render_statement_pdf(customer, invoices, company_name)
        return f"Statement-{customer.customer_id}.pdf", pdf_bytes, "application/pdf"
    return None


def send_customer_email(template_key: str, customer, sent_by_id=None, invoice=None, payment=None, batch_id: str = "") -> EmailLog:
    """Renders + sends one email to one customer. Always writes an
    EmailLog row, on success or failure, so every attempt is auditable from
    the customer's Email tab / the platform-wide email log."""
    recipient = (customer.email or "").strip()
    if not recipient:
        return EmailLog.objects.create(
            customer=customer,
            template_key=template_key,
            recipient_email="",
            subject="",
            status=EmailLog.Status.FAILED,
            error_message="Customer has no email address on file.",
            sent_by_id=sent_by_id,
            batch_id=batch_id,
        )

    subject = ""
    try:
        template = EmailTemplate.objects.get(key=template_key)
        context = build_context(template_key, customer, invoice, payment)
        subject, body = render_template_strings(template, context)
        attachment = _build_attachment(template_key, customer, invoice)

        plain_text = strip_tags(body)
        message = EmailMultiAlternatives(
            subject=subject,
            body=plain_text,
            from_email=get_email_config()["from_email"],
            to=[recipient],
            connection=get_email_connection(),
        )
        message.attach_alternative(body, "text/html")
        if attachment:
            filename, content, mimetype = attachment
            message.attach(filename, content, mimetype)
        message.send()

        return EmailLog.objects.create(
            customer=customer,
            template_key=template_key,
            recipient_email=recipient,
            subject=subject,
            status=EmailLog.Status.SENT,
            sent_by_id=sent_by_id,
            batch_id=batch_id,
        )
    except Exception as exc:  # noqa: BLE001 — any failure must still be logged, not raised, in bulk sends
        logger.exception("Failed to send %s email to customer %s", template_key, customer.pk)
        return EmailLog.objects.create(
            customer=customer,
            template_key=template_key,
            recipient_email=recipient,
            subject=subject,
            status=EmailLog.Status.FAILED,
            error_message=str(exc),
            sent_by_id=sent_by_id,
            batch_id=batch_id,
        )


def send_bulk_emails(template_key: str, customer_ids: list, sent_by_id=None):
    """Kicks off sending to many customers in a background thread so the
    request returns immediately instead of blocking (or timing out) on
    potentially hundreds of SMTP round-trips. This project has no
    Celery/Redis worker set up — a daemon thread is the simplest way to get
    non-blocking bulk sends without adding that infrastructure. Results are
    visible afterwards via the EmailLog list filtered by the returned
    batch_id.
    """
    batch_id = uuid.uuid4().hex[:20]

    def _worker():
        from customers.models import Customer  # imported here: runs after the request/response cycle

        for cid in customer_ids:
            try:
                customer = Customer.objects.get(pk=cid)
            except Customer.DoesNotExist:
                continue
            send_customer_email(template_key, customer, sent_by_id=sent_by_id, batch_id=batch_id)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    return batch_id, len(customer_ids)


def send_payslip_email(line, pdf_bytes, sent_by_id=None) -> EmailLog:
    """Email one payslip to the employee it belongs to.

    Deliberately NOT built on the EmailTemplate machinery the customer emails
    use. Those templates are edited by staff and rendered with a customer in
    context; a payslip goes to a colleague, carries a statutory document, and
    its wording should not be something anyone can accidentally break. So the
    body is fixed here, and only the send and the log are shared.

    Always writes an EmailLog row, success or failure, hung off the staff
    member -- a payslip is the kind of send you may need to prove you made.
    """
    from .models import EmailTemplate

    recipient = (line.staff.email or "").strip()
    name = line.staff.get_full_name() or line.staff.username
    period = f"{line.payroll_run.period_start:%d %b %Y} to {line.payroll_run.period_end:%d %b %Y}"
    config = get_email_config()
    employer = config["company_name"]
    subject = f"Payslip — {period}"

    if not recipient:
        return EmailLog.objects.create(
            staff=line.staff,
            template_key=EmailTemplate.Key.PAYSLIP,
            recipient_email="",
            subject=subject,
            status=EmailLog.Status.FAILED,
            error_message="No email address on file for this staff member.",
            sent_by_id=sent_by_id,
        )

    body = (
        f"<p>Hi {name},</p>"
        f"<p>Your payslip for <strong>{period}</strong> is attached.</p>"
        f"<p>Net pay: <strong>R {line.net_pay:.2f}</strong></p>"
        f"<p>If anything on it looks wrong, reply to this email and we will check it.</p>"
        f"<p>{employer}</p>"
    )

    log_kwargs = {
        "staff": line.staff,
        "template_key": EmailTemplate.Key.PAYSLIP,
        "recipient_email": recipient,
        "subject": subject,
        "sent_by_id": sent_by_id,
    }
    try:
        message = EmailMultiAlternatives(
            subject=subject,
            body=strip_tags(body),
            from_email=config["from_email"],
            to=[recipient],
            connection=get_email_connection(),
        )
        message.attach_alternative(body, "text/html")
        message.attach(
            f"payslip-{line.payroll_run.period_end}.pdf", pdf_bytes, "application/pdf"
        )
        message.send()
    except Exception as exc:                                        # noqa: BLE001
        # Whatever went wrong -- SMTP down, bad credentials, rejected
        # recipient -- the attempt is recorded rather than lost.
        logger.exception("Payslip email to %s failed", recipient)
        return EmailLog.objects.create(
            status=EmailLog.Status.FAILED, error_message=str(exc)[:500], **log_kwargs
        )
    return EmailLog.objects.create(status=EmailLog.Status.SENT, **log_kwargs)
