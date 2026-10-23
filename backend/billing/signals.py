"""A payment lands: it settles the invoices it pays, then the line comes back on.

TWO THINGS, IN THAT ORDER, ON ONE EVENT

Allocation first, reconnection second. Allocation only redistributes money
the balance has already accounted for, so it cannot change whether the
customer qualifies to be reconnected -- but the invoice statuses it
writes are what the rest of the platform reads, and doing it first means
a customer is never briefly back online against an invoice that still
says UNPAID.

WHY ALLOCATION HAS TO HAPPEN AT ALL

billing.allocation's own docstring names the gap: a Payment with an
`invoice` credits that one invoice, and a Payment WITHOUT one only moves
Customer.balance. Bank feeds take the second path -- confirming a feed
line posts to a customer, never an invoice -- so the normal case left a
customer square on their balance while every invoice still read UNPAID.

That is not cosmetic. An invoice reading UNPAID ages into OVERDUE,
collects a reminder on day 3 and day 4, a cut-off warning on the 5th at
09:00, and gets the customer suspended at 15:00 -- all for money already
in the bank. The customer who prompted this had R620 received and an
invoice showing R0.00 paid.

THE GUARD THAT MATTERS MOST

A payment recorded AGAINST A SPECIFIC INVOICE is skipped entirely.
PaymentSerializer.create credits that invoice's paid_amount directly and
writes NO PaymentAllocation row, while allocation.unallocated_amount
computes `amount - allocated_total` and so would report the whole
payment as still unallocated. Auto-allocating it would put the same
money onto a second invoice. The money is already where it belongs;
there is nothing to distribute.

THE LEDGER IMPORT is skipped for both actions. It back-dates years of
history one row at a time, and neither reconnecting somebody on 2024
money nor bulk-settling imported invoices as a side effect of an import
is something that should happen without being asked for.

ON_COMMIT, NOT INLINE, for both. The payment's effect on
Customer.balance and its own row land in one transaction; acting before
that commits reads stale money, and a transaction that rolls back would
leave invoices settled against a payment that never happened.

NOTHING HERE RAISES. A failed allocation or reconnection is a support
call. A failed payment save is an accounting problem. The second must
never be caused by the first.
"""
import logging

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


def _is_imported(payment):
    from .statements import is_imported_note

    return is_imported_note(payment.note)


@receiver(post_save, sender="billing.Payment")
def _payment_settles_and_reconnects(sender, instance, created, **kwargs):
    if not created:
        return
    if _is_imported(instance):
        return

    customer_id = instance.customer_id
    if customer_id is None:
        return

    payment_pk = instance.pk
    # Read now, on the instance we were handed: by the time on_commit runs
    # this object is still the one the caller built, and re-reading the row
    # only to learn whether it named an invoice is a query for nothing.
    # Legacy only: since 5 Oct 2026 PaymentSerializer never sets
    # Payment.invoice -- "record against this invoice" arrives as
    # _preferred_invoice_id and becomes an ordinary allocation below.
    direct_invoice = instance.invoice_id is not None
    skip_allocation = direct_invoice or getattr(instance, "_skip_allocation", False)
    preferred_invoice_id = getattr(instance, "_preferred_invoice_id", None)
    skip_reactivation = getattr(instance, "_skip_reactivation", False)

    def run():
        if not skip_allocation:
            try:
                _allocate(payment_pk, preferred_invoice_id)
            except Exception:
                logger.exception("Auto-allocating payment %s failed", payment_pk)

        if not skip_reactivation:
            from .reactivation import reconnect_customer

            try:
                reconnect_customer(customer_id, reason=f"payment #{payment_pk}")
            except Exception:
                logger.exception(
                    "Reconnection check after payment %s for customer %s failed",
                    payment_pk, customer_id,
                )

    transaction.on_commit(run)


def _allocate(payment_pk, preferred_invoice_id=None):
    """Settle the oldest open invoices this payment can reach.

    Re-read rather than trusting the instance: allocation locks invoices
    with select_for_update and writes paid_amount, and it must do that
    against the row as it is now, not as it was when the serializer built
    it.
    """
    from .allocation import settle_payment
    from .models import Payment

    payment = Payment.objects.filter(pk=payment_pk).first()
    if payment is None:
        return []
    # The invoice it was recorded against first (if any), then oldest-first.
    lines = settle_payment(payment, preferred_invoice_id)
    if lines:
        logger.info(
            "Payment %s auto-allocated across %d invoice(s)", payment_pk, len(lines)
        )
    return lines
