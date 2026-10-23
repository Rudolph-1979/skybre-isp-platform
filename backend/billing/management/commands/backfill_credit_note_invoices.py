"""One-off: give every already-approved credit request its visible credit
note.

Why this exists
----------------
Approving a CreditRequest has always moved Customer.balance directly (see
CreditRequestViewSet.approve) but never left anything a customer or a
staff member could actually see -- no invoice, no line item, nothing on
the Invoices tab or the statement PDF. As of this release, approving one
also calls CreditRequest.create_credit_note_invoice() to create that line.
This command does the same thing, once, for every credit that was already
approved before that change shipped -- so a credit like Christo van den
Heever's R978.68 salary-deduction credit (approved before this existed)
gets the same visible line a credit approved today gets, dated back to
when it actually happened (decided_at), not the day this command happens
to run.

Safety
------
Idempotent by construction rather than by a guard file: it only ever
selects CreditRequest rows that are APPROVED, have decided_at set, and
have no credit_note yet. Running it twice is a no-op the second time
round, because the first run's rows no longer match the third condition.

Dry run by default -- prints what it would create and writes nothing.
--apply commits, one credit at a time inside its own transaction, so one
bad row cannot take the rest down with it.

    manage.py backfill_credit_note_invoices                    # dry run
    manage.py backfill_credit_note_invoices --apply
    manage.py backfill_credit_note_invoices --customer 1603 --apply
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from billing.models import CreditRequest


class Command(BaseCommand):
    help = (
        "Backfill a visible credit-note invoice for every already-approved "
        "credit request that doesn't have one yet."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write the changes. Without this the command only reports.",
        )
        parser.add_argument(
            "--customer",
            type=int,
            default=None,
            help="Limit to one customer's credit requests, by id.",
        )

    def handle(self, *args, **options):
        qs = (
            CreditRequest.objects.filter(
                status=CreditRequest.Status.APPROVED,
                decided_at__isnull=False,
                credit_note__isnull=True,
            )
            .select_related("customer")
            .order_by("decided_at")
        )
        if options["customer"]:
            qs = qs.filter(customer_id=options["customer"])

        credits = list(qs)
        if not credits:
            self.stdout.write(
                "No approved credit requests are missing a credit note. Nothing to do."
            )
            return

        self.stdout.write(
            f"{len(credits)} approved credit request(s) with no visible credit note:"
        )
        self.stdout.write("")
        for credit in credits:
            self.stdout.write(
                f"  #{credit.pk}  {credit.customer.full_name}  R{credit.amount}  "
                f"decided {credit.decided_at.date()}  -- {credit.reason[:60]}"
            )

        if not options["apply"]:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    "Dry run -- nothing written. Re-run with --apply to commit."
                )
            )
            return

        created = 0
        failed = []
        for credit in credits:
            try:
                with transaction.atomic():
                    invoice = credit.create_credit_note_invoice()
                    credit.save(update_fields=["credit_note"])
            except Exception as exc:  # noqa: BLE001 -- reported below, not swallowed
                failed.append((credit, exc))
                continue
            created += 1
            self.stdout.write(f"  #{credit.pk}: created {invoice.number} (R{invoice.total})")

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Created {created} credit note(s)."))
        if failed:
            self.stdout.write(self.style.ERROR(f"{len(failed)} failed:"))
            for credit, exc in failed:
                self.stdout.write(f"  #{credit.pk}: {exc}")
