"""Settle open invoices from payments that were never allocated.

    python manage.py allocate_payments                        # dry run
    python manage.py allocate_payments --commit
    python manage.py allocate_payments --client-code CUS-001600 --commit

WHAT THIS IS FOR

Every payment recorded before auto-allocation existed. Each one moved the
customer's balance and left their invoices reading UNPAID -- so the
invoice list, the aging report, the statement and the customer portal all
show money outstanding that is sitting in the bank, and the dunning
engine chases it.

From now on billing.signals allocates on the way in. This is the
backlog.

WHAT IT WILL NOT TOUCH

  * A payment recorded AGAINST A SPECIFIC INVOICE. That already credited
    the invoice directly, without writing an allocation row, so the
    money is where it belongs -- and allocating it again would put the
    same rand onto a second invoice. This is the reason this command
    exists as its own pass rather than a loop over every payment.
  * A payment from the ledger import, unless --include-imported. Those
    are historical and their invoices may well have been settled in the
    system they came from.
  * Anything already fully allocated.

HOW IT ALLOCATES

billing.allocation.auto_allocate: oldest due first, capped per invoice at
what is still owed, remainder left as account credit. Nothing here
touches Customer.balance -- the balance moved once, when the payment was
recorded, and allocation only distributes money it has already accounted
for. Anything that starts moving the balance here is a double-count.

DRY RUN BY DEFAULT. This writes to invoices.
"""
from decimal import Decimal

from django.core.management.base import BaseCommand

ZERO = Decimal("0")


class Command(BaseCommand):
    help = "Allocate unallocated payments to open invoices (dry run unless --commit)."

    def add_arguments(self, parser):
        parser.add_argument("--client-code", help="One customer only, e.g. CUS-001600.")
        parser.add_argument(
            "--include-imported", action="store_true",
            help="Also allocate payments brought across by the ledger import.",
        )
        parser.add_argument(
            "--commit", action="store_true", help="Actually allocate. Without it, nothing changes.",
        )

    def handle(self, *args, **options):
        from customers.models import Customer

        from billing.allocation import auto_allocate, open_invoices, unallocated_amount
        from billing.models import Payment
        # is_imported_note, not IMPORT_NOTE_PREFIX: the transaction-report
        # import writes "Imported from previous system (transaction report):"
        # and was not recognised, so --include-imported changed nothing for
        # it (12 Oct 2026).
        from billing.statements import is_imported_note

        customer = None
        if options["client_code"]:
            code = options["client_code"].strip()
            customer = Customer.objects.filter(customer_id__iexact=code).first()
            if customer is None:
                self.stderr.write(f'No customer with client code "{code}".')
                return

        payments = (
            Payment.objects.filter(invoice__isnull=True, amount__gt=ZERO)
            .select_related("customer")
            .order_by("date", "pk")
        )
        if customer is not None:
            payments = payments.filter(customer=customer)

        planned = []
        skipped_imported = 0
        for payment in payments:
            if is_imported_note(payment.note) and not options["include_imported"]:
                skipped_imported += 1
                continue
            spare = unallocated_amount(payment)
            if spare <= ZERO:
                continue
            owed = sum(
                (invoice.balance_due for invoice in open_invoices(payment.customer)), ZERO
            )
            if owed <= ZERO:
                continue
            planned.append((payment, spare, min(spare, owed)))

        if not planned:
            self.stdout.write("Nothing to allocate: every payment is already settled.")
            if skipped_imported:
                self.stdout.write(
                    f"({skipped_imported} imported payment(s) skipped -- --include-imported to consider them.)"
                )
            return

        verb = "Allocating" if options["commit"] else "Would allocate"
        self.stdout.write(f"{verb} {len(planned)} payment(s)\n")
        for payment, spare, usable in planned:
            owner = payment.customer
            self.stdout.write(
                f"  {(owner.customer_id or '-'):<12} {owner.full_name[:24]:<26} "
                f"payment #{payment.pk:<5} {payment.date:%d/%m/%Y}  "
                f"unallocated {spare:>10,.2f}  to settle {usable:>10,.2f}"
            )
        if skipped_imported:
            self.stdout.write(f"\n{skipped_imported} imported payment(s) skipped.")

        if not options["commit"]:
            self.stdout.write("\nNothing was changed. Add --commit when the list above looks right.")
            return

        done = failed = 0
        for payment, _spare, _usable in planned:
            try:
                lines = auto_allocate(payment)
                done += len(lines)
            except Exception as exc:
                failed += 1
                self.stderr.write(f"    FAILED payment #{payment.pk}: {exc}")

        self.stdout.write(
            f"\nWrote {done} allocation line(s)" + (f", {failed} payment(s) failed" if failed else "")
        )
