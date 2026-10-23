"""Financial notifications mirrored to the client's Conecto app
(George, 12 Oct 2026: "send all financial notifications to Conecto
including payment reminders and suspension notifications").

Hooked into notifications.services.send_customer_email, the one place
every customer email passes through, so the app gets exactly what the
email engine decided to send -- the same day, the same invoice, the same
amounts. Invoices and statements are NOT here: Conecto already gets those
as invoiceCreated / statementCreated, which push to the phone themselves.

Sent whatever happened to the email (George's choice): a client with no
email address, or whose email bounced, still hears about it in the app.
`email_status` says which.

Never raises. A notification that fails to reach Conecto is a ConectoCall
row (conecto_resync replays the last 24 hours of them); it must never
break the billing run that sent the email.
"""
import logging
from decimal import Decimal, InvalidOperation

from django.utils import timezone

logger = logging.getLogger(__name__)

#: template key -> notification type Conecto receives.
FEED_KEYS = {
    "payment_reminder": "payment_reminder",
    "suspension_warning": "suspension_warning",     # "suspension_today" when days_until_suspension is 0
    "suspension": "service_suspended",
    "payment_received": "payment_received",
    "service_restored": "service_restored",
}


def _money(value):
    try:
        return f"{Decimal(str(value)):.2f}"
    except (InvalidOperation, TypeError, ValueError):
        return None


def _iso(value):
    if value is None or value == "":
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _text(kind, ctx):
    """(title, message) in plain words -- the app can show these as they are,
    or build its own from the structured fields beside them."""
    company = ctx.get("company_name") or "Skybre"
    balance = _money(ctx.get("balance")) or "0.00"
    if kind == "payment_reminder":
        due = ctx.get("invoice_due_date")
        due_text = f" Invoice {ctx.get('invoice_number')} was due on {due.strftime('%d/%m/%Y')}." if due and ctx.get(
            "invoice_number") else ""
        return "Payment reminder", f"Your {company} account has an outstanding balance of R {balance}.{due_text}"
    if kind == "suspension_warning":
        days = ctx.get("days_until_suspension")
        when = ctx.get("suspend_date")
        when_text = when.strftime("%d/%m/%Y") if hasattr(when, "strftime") else str(when or "")
        plural = "day" if days == 1 else "days"
        return (f"Service suspension in {days} {plural}",
                f"Your service will be suspended on {when_text} unless the outstanding balance of R {balance} is paid.")
    if kind == "suspension_today":
        return ("Service suspension today",
                f"Your service will be suspended today unless the outstanding balance of R {balance} is paid.")
    if kind == "service_suspended":
        return ("Service suspended",
                f"Your service has been suspended because of an outstanding balance of R {balance}. "
                "Pay the balance to have it restored.")
    if kind == "payment_received":
        amount = _money(ctx.get("payment_amount")) or ""
        return "Payment received", f"Thank you, we received your payment of R {amount}. Your balance is now R {balance}."
    if kind == "service_restored":
        return "Service restored", "Thank you for your payment. Your service is active again."
    return kind.replace("_", " ").capitalize(), ""


def build_payload(template_key, customer, log, ctx):
    kind = FEED_KEYS[template_key]
    if kind == "suspension_warning" and ctx.get("days_until_suspension") == 0:
        kind = "suspension_today"
    title, message = _text(kind, ctx)
    if log is None:
        email_status = "no_email"
    elif log.status == "sent":
        email_status = "sent"
    elif not log.recipient_email:
        email_status = "no_email"
    else:
        email_status = "failed"
    return {
        "notification_id": f"vexar-email-{log.pk}" if log is not None else f"vexar-{template_key}-{customer.pk}-{timezone.now():%Y%m%d%H%M%S}",
        "client_code": customer.customer_id,
        "type": kind,
        "title": title,
        "message": message,
        "currency": "ZAR",
        "balance": _money(ctx.get("balance")),
        "invoice_number": ctx.get("invoice_number") or None,
        "due_date": _iso(ctx.get("invoice_due_date")),
        "suspend_date": _iso(ctx.get("suspend_date")),
        "days_until_suspension": ctx.get("days_until_suspension"),
        "payment_amount": _money(ctx.get("payment_amount")) if ctx.get("payment_amount") is not None else None,
        "email_status": email_status,
        "created_at": timezone.now().isoformat(),
    }


def mirror(template_key, customer, log, invoice=None, payment=None, extra_context=None):
    """Push one notification to Conecto if this key is in the feed."""
    if template_key not in FEED_KEYS or not getattr(customer, "customer_id", ""):
        return None
    try:
        from conecto import client

        if not client.notifications_enabled():
            return None
        from .services import build_context

        ctx = build_context(template_key, customer, invoice, payment, extra=extra_context)
        return client.push_notification(build_payload(template_key, customer, log, ctx))
    except Exception:  # noqa: BLE001 -- see module docstring
        logger.exception("Mirroring %s to Conecto for customer %s failed", template_key, getattr(customer, "pk", None))
        return None
