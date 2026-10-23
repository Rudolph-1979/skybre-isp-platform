"""Convert tariff prices from VAT-inclusive to VAT-exclusive.

Why this exists
---------------
`Tariff.price` is the EXCLUSIVE amount. billing.recurring copies it
straight into `InvoiceItem.unit_price` and Invoice.recalc_totals() then
adds `tax_rate_pct` on top:

    unit_price = svc.tariff.price
    tax        = unit_price * (tax_rate_pct / 100)

Nothing on the tariff form said so, so the inclusive retail price was
typed in instead. MTD 20 Mbps sat at 435.85 -- the price the customer
pays -- and would have been invoiced as 435.85 + 15% = R501.23.

Every price therefore needs dividing by 1 + rate once, and exactly once.
Running this twice would take R435.85 down to R329.57 and undercharge by
the same 15% in the other direction, so it refuses to run a second time
unless you make it (see --force below).

Safety
------
Dry run by default -- it prints what it would do and writes nothing.
`--apply` performs the update inside a transaction, after writing every
old price to a timestamped JSON file under MEDIA_ROOT so the change can
be reversed by hand if a number turns out to have been exclusive all
along.

The round-trip column is the check that matters: new x 1.15 must come
back to the price you are charging today. Any row where it does not is
printed as a MISMATCH and, unless --force, stops the whole run -- that
row is either already exclusive or was never a clean inclusive figure,
and it needs a human before anything is written.
"""

from __future__ import annotations

import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from billing.models import Tariff

CENTS = Decimal("0.01")
GUARD_NAME = ".tariff_vat_converted"


def to_cents(value: Decimal) -> Decimal:
    # ROUND_HALF_UP to match what Postgres does to a numeric(10,2), which
    # is what rounds this column everywhere else.
    return Decimal(value).quantize(CENTS, rounding=ROUND_HALF_UP)


class Command(BaseCommand):
    help = "Divide VAT-inclusive tariff prices down to the exclusive amount."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write the changes. Without this the command only reports.",
        )
        parser.add_argument(
            "--rate",
            default="15",
            help="VAT rate as a percentage. Default 15.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Run even if a previous conversion is recorded, or if some "
                 "rows do not round-trip. Read the report first.",
        )

    def handle(self, *args, **options):
        rate = Decimal(str(options["rate"]))
        divisor = Decimal("1") + (rate / Decimal("100"))
        apply_changes = options["apply"]
        force = options["force"]

        media = Path(getattr(settings, "MEDIA_ROOT", "."))
        guard = media / GUARD_NAME
        if guard.exists() and not force:
            raise CommandError(
                f"A conversion is already recorded at {guard} "
                f"(ran {guard.read_text().strip()}). Tariff prices would be "
                f"divided by {divisor} a second time and every customer would "
                f"be undercharged. Use --force only if you are certain."
            )

        tariffs = list(Tariff.objects.all().order_by("name"))
        if not tariffs:
            self.stdout.write("No tariffs found. Nothing to do.")
            return

        name_width = max(len(t.name) for t in tariffs)
        header = (
            f"{'Tariff'.ljust(name_width)}  {'Now (incl)':>12}  "
            f"{'Ex VAT':>12}  {'VAT':>10}  {'Round-trip':>12}"
        )
        self.stdout.write(header)
        self.stdout.write("-" * len(header))

        rows = []
        mismatches = []
        for tariff in tariffs:
            old = Decimal(tariff.price)
            new = to_cents(old / divisor)
            vat = to_cents(new * rate / Decimal("100"))
            trip = new + vat
            ok = trip == old
            if not ok:
                mismatches.append((tariff, old, new, trip))
            rows.append(
                {
                    "id": tariff.pk,
                    "name": tariff.name,
                    "old_price": str(old),
                    "new_price": str(new),
                    "round_trip": str(trip),
                    "matches": ok,
                }
            )
            flag = "" if ok else "  <-- MISMATCH"
            self.stdout.write(
                f"{tariff.name.ljust(name_width)}  {old:>12}  "
                f"{new:>12}  {vat:>10}  {trip:>12}{flag}"
            )

        self.stdout.write("")
        self.stdout.write(
            f"{len(rows)} tariffs, {len(mismatches)} that do not round-trip."
        )

        if mismatches and not force:
            self.stdout.write("")
            for tariff, old, new, trip in mismatches:
                self.stdout.write(
                    f"  {tariff.name}: {new} x {divisor} = {trip}, "
                    f"but it is priced at {old}."
                )
            raise CommandError(
                "Refusing to write while any row fails the round-trip check. "
                "Those prices are either already exclusive or were never a "
                "clean inclusive figure -- check them, then re-run with "
                "--force to convert everything anyway."
            )

        if not apply_changes:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    "Dry run -- nothing written. Re-run with --apply to commit."
                )
            )
            return

        stamp = timezone.now().strftime("%Y%m%d-%H%M%S")
        media.mkdir(parents=True, exist_ok=True)
        backup = media / f"tariff_price_backup_{stamp}.json"
        backup.write_text(json.dumps(rows, indent=2))

        with transaction.atomic():
            for row in rows:
                Tariff.objects.filter(pk=row["id"]).update(
                    price=Decimal(row["new_price"])
                )

        guard.write_text(f"{timezone.now().isoformat()} rate={rate}\n")

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Updated {len(rows)} tariffs."))
        self.stdout.write(f"Old prices saved to {backup}")
        self.stdout.write(f"Guard written to {guard} -- re-running is blocked.")
