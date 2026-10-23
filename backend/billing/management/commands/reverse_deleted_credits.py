"""Undo approved credits whose credit note was deleted before deleting one
reversed it.

    python manage.py reverse_deleted_credits --client-code SMF883            # dry run
    python manage.py reverse_deleted_credits --client-code SMF883 --commit

Until 1 Oct 2026, deleting a credit note removed the document but left the
credit's money exactly where it was: the customer's balance stayed
reduced and any invoice it settled stayed Paid. Deleting one now reverses
it (InvoiceViewSet.destroy -> CreditRequest.reverse). This catches up the
ones deleted before that: Approved credits with no credit note left.

ONE CUSTOMER AT A TIME, ON PURPOSE. An Approved credit with no credit note
can also be an old credit approved before credit notes existed and never
backfilled (see backfill_credit_note_invoices) -- not deleted at all.
This command cannot tell the two apart. Read the dry run.

Balance: always exact -- each credit's own amount goes back on.

Linked invoice: a credit approved since 5 Oct 2026 records what it settled
(CreditAllocation) and reverse() undoes exactly that. Older credits never
recorded how much they moved onto their invoice. What credits put there is worked out as the invoice's
paid amount minus what payments account for (allocations plus payments
recorded straight against it), shared out across this customer's
reversed credits for that invoice, never more than each one's own amount.
If NO payment accounts for any of the invoice's paid amount, the figure
can't be trusted (an imported invoice can carry a paid amount with no
payment row behind it), so the invoice is left alone and flagged CHECK.
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum

ZERO = Decimal("0.00")


class Command(BaseCommand):
    help = "Reverse approved credits whose credit note was deleted (one customer, dry run by default)."

    def add_arguments(self, parser):
        parser.add_argument("--client-code", required=True, help="One customer, e.g. SMF883.")
        parser.add_argument("--commit", action="store_true", help="Actually reverse. Without it, nothing changes.")

    def _plan(self, customer):
        from billing.models import CreditRequest, Invoice, Payment, PaymentAllocation

        credits = list(
            CreditRequest.objects.filter(
                customer=customer, status=CreditRequest.Status.APPROVED, credit_note__isnull=True,
            ).order_by("decided_at", "pk")
        )
        # What credits (rather than payments) put on each linked invoice.
        room = {}
        flagged = set()
        for inv_id in {c.invoice_id for c in credits if c.invoice_id}:
            inv = Invoice.objects.filter(pk=inv_id).first()
            if inv is None:
                continue
            by_payments = (
                (PaymentAllocation.objects.filter(invoice=inv).aggregate(s=Sum("amount"))["s"] or ZERO)
                + (Payment.objects.filter(invoice=inv, amount__gt=0).aggregate(s=Sum("amount"))["s"] or ZERO)
            )
            if by_payments <= ZERO and inv.paid_amount > ZERO:
                flagged.add(inv_id)
                continue
            room[inv_id] = max(ZERO, inv.paid_amount - by_payments)

        plan = []
        for c in credits:
            take_back = ZERO
            note = ""
            if c.allocation_tracked:
                # Recorded as CreditAllocation rows -- reverse() undoes
                # exactly those; nothing to work out.
                take_back = sum((a.amount for a in c.allocations.all()), ZERO)
            elif c.invoice_id in flagged:
                note = "CHECK: invoice paid amount has no payment behind it -- invoice left alone"
            elif c.invoice_id in room:
                take_back = min(c.amount, room[c.invoice_id])
                room[c.invoice_id] -= take_back
            plan.append((c, take_back, note))
        return plan

    def handle(self, *args, **options):
        from customers.models import Customer

        code = options["client_code"].strip()
        customer = Customer.objects.filter(customer_id__iexact=code).first()
        if customer is None:
            self.stderr.write(f'No customer with client code "{code}".')
            return

        plan = self._plan(customer)
        if not plan:
            self.stdout.write(f"{customer}: no approved credits without a credit note. Nothing to do.")
            return

        commit = options["commit"]
        self.stdout.write(f"{'Reversing' if commit else 'Would reverse'} {len(plan)} credit(s) for {customer}"
                          f" (balance now {customer.balance}):")
        total = ZERO
        for c, take_back, note in plan:
            inv = c.invoice.number if c.invoice_id and c.invoice else "-"
            decided = c.decided_at.strftime("%d/%m/%Y") if c.decided_at else "-"
            self.stdout.write(
                f"  credit #{c.pk:<5} {decided}  R{c.amount:>9}  invoice {inv:<16} "
                f"take back from invoice R{take_back:>8}  {c.reason[:40]!r}  {note}"
            )
            total += c.amount
        self.stdout.write(f"Balance would go from {customer.balance} to {customer.balance + total}.")

        if not commit:
            self.stdout.write("\nNothing was changed. Add --commit when the list above looks right.")
            return

        with transaction.atomic():
            for c, take_back, _note in plan:
                c.reverse(note="Reversed: credit note deleted before 1 Oct fix (reverse_deleted_credits)",
                          invoice_take_back=take_back)
        customer.refresh_from_db()
        self.stdout.write(f"Reversed {len(plan)} credit(s). Balance now {customer.balance}.")
