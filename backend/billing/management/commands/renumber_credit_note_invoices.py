"""One-off: move an already-existing credit-note invoice off the ordinary
"INV-" sequence and onto its own dedicated "CN-" sequence.

Why this exists
----------------
The first build of this feature gave a credit note whatever number the
ordinary invoice sequence handed out next -- Christo van den Heever's
R978.68 credit note landed as INV-000015. Once deployed, Rudolph saw it
sitting next to the historical Splynx-imported CN202605000020 and said:
"You named the credit INV? It should be CN." The code fix
(Invoice._CREDIT_NOTE_PREFIX / _next_number) only changes what a NEW
credit note is numbered from here on -- it does nothing for one that
already exists, because a number, once issued, is never silently
reassigned by ordinary code. This command is the one-off, explicit fixup
for that: it finds every already-existing credit note invoice still
carrying an old-style number and gives it a fresh one under the CN
sequence, leaving everything else about the invoice (dates, amount,
items, paid status, the customer it belongs to) untouched.

Which rows it touches
----------------------
Only an Invoice reached via CreditRequest.credit_note whose current
`number` does NOT already match `CN-<digits>` -- so a credit note created
after the CN-sequence fix shipped (already numbered correctly) is left
alone, and re-running this command once nothing is left on the old
numbering is a no-op with nothing new to do. This is deliberately
narrower than "every Invoice with a negative total": it never touches the
historical Splynx
import (CN202605000020 and similar), which has no CreditRequest at all
and already carries its own number.

Renumbering itself reuses the exact mechanism a brand-new credit note
uses -- Invoice._next_number_for_prefix(Invoice._CREDIT_NOTE_PREFIX) --
via the same transient `_credit_note_prefix` flag, so a renumbered row
and a freshly-created one are indistinguishable afterwards, and the
IssuedNumberHighWater watermark for "CN" is updated exactly the same way
(_record_issued_number), so no future number can collide with one that
used to belong to a since-renumbered invoice. The old number is not
reused by anything else -- INV numbering only ever goes up, so this
leaves a harmless gap where INV-000015 used to be, the same kind of gap a
deleted invoice already leaves.

Dry run by default -- prints what it would renumber and writes nothing.
--apply commits, one credit note at a time inside its own transaction, so
one bad row cannot take the rest down with it.

    manage.py renumber_credit_note_invoices                    # dry run
    manage.py renumber_credit_note_invoices --apply
    manage.py renumber_credit_note_invoices --customer 1603 --apply
"""
import re

from django.core.management.base import BaseCommand
from django.db import transaction

from billing.models import CreditRequest, Invoice

_CN_NUMBER_RE = re.compile(r"^CN-\d{1,9}$")


class Command(BaseCommand):
    help = (
        "Renumber an already-existing credit-note invoice from the ordinary "
        "INV- sequence onto its own dedicated CN- sequence."
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
            CreditRequest.objects.filter(credit_note__isnull=False)
            .select_related("credit_note", "customer")
            .order_by("decided_at")
        )
        if options["customer"]:
            qs = qs.filter(customer_id=options["customer"])

        to_renumber = [
            credit
            for credit in qs
            if credit.credit_note is not None
            and not _CN_NUMBER_RE.match(credit.credit_note.number or "")
        ]
        if not to_renumber:
            self.stdout.write(
                "No credit note invoices are still on the old numbering. Nothing to do."
            )
            return

        self.stdout.write(
            f"{len(to_renumber)} credit note invoice(s) still on the old numbering:"
        )
        self.stdout.write("")
        for credit in to_renumber:
            invoice = credit.credit_note
            self.stdout.write(
                f"  #{credit.pk}  {credit.customer.full_name}  {invoice.number}  "
                f"R{invoice.total}  decided {credit.decided_at.date() if credit.decided_at else '?'}"
            )

        if not options["apply"]:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    "Dry run -- nothing written. Re-run with --apply to commit."
                )
            )
            return

        renumbered = 0
        failed = []
        for credit in to_renumber:
            invoice = credit.credit_note
            old_number = invoice.number
            try:
                with transaction.atomic():
                    invoice._credit_note_prefix = True
                    invoice.number = invoice._next_number_for_prefix(
                        Invoice._CREDIT_NOTE_PREFIX
                    )
                    invoice.save(update_fields=["number"])
                    invoice._record_issued_number()
            except Exception as exc:  # noqa: BLE001 -- reported below, not swallowed
                failed.append((credit, old_number, exc))
                continue
            renumbered += 1
            self.stdout.write(f"  #{credit.pk}: {old_number} -> {invoice.number}")

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Renumbered {renumbered} credit note(s)."))
        if failed:
            self.stdout.write(self.style.ERROR(f"{len(failed)} failed:"))
            for credit, old_number, exc in failed:
                self.stdout.write(f"  #{credit.pk} ({old_number}): {exc}")
