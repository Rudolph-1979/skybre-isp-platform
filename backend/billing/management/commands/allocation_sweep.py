"""Apply clients' unapplied money to their open invoices, oldest first.

    python manage.py allocation_sweep                          # dry run, everyone
    python manage.py allocation_sweep --client-code SMF463     # dry run, one client
    python manage.py allocation_sweep --commit                 # do it

The same billing.allocation.sweep_customer the platform now runs by itself
whenever a payment lands, a credit is approved, or an invoice is issued,
cancelled or deleted (5 Oct 2026). This is for the backlog: accounts whose
invoices disagree with their balance from before that existed.

DRY RUN BY DEFAULT: the sweep runs inside a transaction that is rolled
back, and every invoice whose paid amount or status would change is
printed. Nothing here touches Customer.balance.
"""
from django.core.management.base import BaseCommand
from django.db import transaction


class _DryRun(Exception):
    pass


class Command(BaseCommand):
    help = "Apply unapplied money to open invoices oldest-first (dry run unless --commit)."

    def add_arguments(self, parser):
        parser.add_argument("--client-code", help="One client only, e.g. SMF463.")
        parser.add_argument("--commit", action="store_true", help="Actually allocate.")

    def handle(self, *args, **options):
        from billing.allocation import sweep_customer
        from billing.models import Invoice
        from customers.models import Customer

        customers = Customer.objects.all().order_by("customer_id")
        if options["client_code"]:
            customers = customers.filter(customer_id__iexact=options["client_code"].strip())
            if not customers.exists():
                self.stderr.write(f'No client with code "{options["client_code"]}".')
                return

        def snapshot(customer):
            return {
                i.pk: (i.number, i.status, i.paid_amount, i.total)
                for i in Invoice.objects.filter(customer=customer)
            }

        changes = []
        try:
            with transaction.atomic():
                for customer in customers:
                    before = snapshot(customer)
                    sweep_customer(customer.pk)
                    after = snapshot(customer)
                    for pk, (number, status, paid, total) in after.items():
                        b = before.get(pk)
                        if b and (b[1] != status or b[2] != paid):
                            changes.append((customer, number, b[1], b[2], status, paid, total))
                if not options["commit"]:
                    raise _DryRun
        except _DryRun:
            pass

        verb = "Changed" if options["commit"] else "Would change"
        if not changes:
            self.stdout.write("Nothing to change -- every open invoice already reflects the money applied to it.")
            return
        self.stdout.write(f"{verb} {len(changes)} invoice(s):")
        for customer, number, s0, p0, s1, p1, total in changes:
            self.stdout.write(
                f"  {customer.customer_id:<12} {number:<18} total R{total:>9}  "
                f"paid R{p0:>9} -> R{p1:>9}   {s0} -> {s1}"
            )
        if not options["commit"]:
            self.stdout.write("\nNothing was changed. Add --commit when the list above looks right.")
