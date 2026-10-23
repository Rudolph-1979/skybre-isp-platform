"""Settle invoices that were never owed anything.

Invoice.recalc_totals used to refuse to mark an invoice paid unless its
total was greater than zero. The guard was standing in for "this document
has lines yet" -- it stops an invoice created empty from flashing up as
Paid before its items are added -- but it also stranded every invoice
that is LEGITIMATELY zero.

A customer on a 100% discount is billed R999 and discounted R999. Nothing
is owed and nothing can be paid, and the invoice sat at Unpaid: ageing
into Overdue, counted among the unpaid invoices, and collecting payment
reminders chasing R0.00. (It could not get anyone suspended -- blocking
tests the customer's BALANCE, which a zero invoice never moved.)

The code no longer does that. This fixes the invoices raised before it
was fixed, which will not correct themselves: recalc_totals only runs
when a document's lines change, and nobody is going to edit these.

Only invoices that are OWED (unpaid/overdue), total zero or less, and
have at least one line. A zero invoice with no lines at all is an empty
draft somebody abandoned, not a discounted customer, and is left alone.

Usage:
    python manage.py settle_zero_invoices            # report only
    python manage.py settle_zero_invoices --apply
"""
from django.core.management.base import BaseCommand
from django.db.models import Count

from billing.models import Invoice
from billing.overdue import OWED_STATUSES


class Command(BaseCommand):
    help = "Mark zero-total invoices as paid (they were never owed anything)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true",
            help="Actually update them. Without this the command only reports.",
        )

    def handle(self, *args, **options):
        stranded = (
            Invoice.objects.filter(status__in=OWED_STATUSES, total__lte=0)
            .annotate(line_count=Count("items"))
            .filter(line_count__gt=0)
            .select_related("customer")
            .order_by("number")
        )

        rows = list(stranded)
        if not rows:
            self.stdout.write("No zero-total invoices are sitting unpaid.")
            return

        self.stdout.write(f"{len(rows)} zero-total invoice(s) still marked unpaid:")
        width = max(len(i.number or "") for i in rows)
        for invoice in rows:
            self.stdout.write(
                f"  {invoice.number:<{width}}  {invoice.customer.full_name}  "
                f"due {invoice.date_due}  R{invoice.total}"
            )

        if not options["apply"]:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(
                "Dry run -- nothing written. Re-run with --apply to mark these paid."
            ))
            return

        # update(), not save(): these carry no balance debit to reverse (a
        # zero invoice never debited anything) and no totals to recompute, so
        # there is nothing for the save path to do except risk doing it
        # twice.
        updated = Invoice.objects.filter(pk__in=[i.pk for i in rows]).update(
            status=Invoice.Status.PAID
        )

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Marked {updated} invoice(s) paid."))
        self.stdout.write(
            "They will stop appearing in the overdue lists and stop receiving "
            "payment reminders."
        )
