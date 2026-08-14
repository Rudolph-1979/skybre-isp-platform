"""Email-sending logic: render a stored EmailTemplate against a customer
(and optionally an invoice), attach a PDF where relevant, send it, and log
the result — used by both the single-customer send and the bulk send.
"""

import logging
import threading
import uuid

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template import Context, Template
from django.utils import timezone
from django.utils.html import strip_tags

from .models import EmailTemplate, EmailLog
from .pdf import render_invoice_pdf, render_statement_pdf

logger = logging.getLogger(__name__)


def build_context(template_key: str, customer, invoice=None) -> dict:
    """Placeholders available to every template's subject/body — documented
    for staff in the Email Templates admin page. Keep this in sync with
    that page's "available placeholders" hint list.
    """
    ctx = {
        "company_name": settings.COMPANY_NAME,
        "customer_name": customer.full_name,
        "customer_id": customer.customer_id,
        "customer_email": customer.email,
        "portal_url": f"{settings.SITE_URL.rstrip('/')}/login",
        "balance": f"{customer.balance:.2f}",
        "today": timezone.localdate(),
        "invoice_number": "",
        "invoice_total": "",
        "invoice_due_date": "",
    }
    if template_key == EmailTemplate.Key.STATEMENT:
        ctx["statement_date"] = timezone.localdate()
    elif template_key == EmailTemplate.Key.INVOICE and invoice is not None:
        ctx["invoice_number"] = invoice.number
        ctx["invoice_total"] = f"{invoice.total:.2f}"
        ctx["invoice_due_date"] = invoice.date_due
    elif template_key == EmailTemplate.Key.PAYMENT_REMINDER and invoice is not None:
        ctx["invoice_number"] = invoice.number
        ctx["invoice_due_date"] = invoice.date_due
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

    if template_key == EmailTemplate.Key.INVOICE:
        if invoice is None:
            raise ValueError("An invoice must be selected to send an Invoice email.")
        pdf_bytes = render_invoice_pdf(invoice, settings.COMPANY_NAME)
        return f"{invoice.number}.pdf", pdf_bytes, "application/pdf"
    if template_key == EmailTemplate.Key.STATEMENT:
        invoices = list(Invoice.objects.filter(customer=customer).order_by("-date_created"))
        pdf_bytes = render_statement_pdf(customer, invoices, settings.COMPANY_NAME)
        return f"Statement-{customer.customer_id}.pdf", pdf_bytes, "application/pdf"
    return None


def send_customer_email(template_key: str, customer, sent_by_id=None, invoice=None, batch_id: str = "") -> EmailLog:
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
        context = build_context(template_key, customer, invoice)
        subject, body = render_template_strings(template, context)
        attachment = _build_attachment(template_key, customer, invoice)

        plain_text = strip_tags(body)
        message = EmailMultiAlternatives(subject=subject, body=plain_text, to=[recipient])
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
