"""PDF generation for emailed documents (invoice / statement attachments).

Uses xhtml2pdf (pure-Python, no system libraries like Cairo/Pango needed —
important because it has to build cleanly in the slim Docker image), driven
off plain Django templates so the layout is easy to tweak later.
"""

import io

from django.template.loader import render_to_string
from django.utils import timezone
from xhtml2pdf import pisa


def _render_pdf(template_name: str, context: dict) -> bytes:
    html = render_to_string(template_name, context)
    buffer = io.BytesIO()
    result = pisa.CreatePDF(src=html, dest=buffer)
    if result.err:
        raise RuntimeError(f"Failed to render PDF from {template_name} ({result.err} error(s))")
    return buffer.getvalue()


def render_invoice_pdf(invoice, company_name: str) -> bytes:
    context = {
        "company_name": company_name,
        "generated_date": timezone.localdate(),
        "invoice": invoice,
        "customer": invoice.customer,
        "items": list(invoice.items.all()),
        "balance_due": invoice.total - invoice.paid_amount,
    }
    return _render_pdf("notifications/invoice_pdf.html", context)


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
