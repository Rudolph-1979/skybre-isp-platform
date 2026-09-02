"""Applies tariff changes that were booked for today or earlier.

Meant for a daily cron. Also called by run_recurring_billing, so a scheduled
change still lands even if only one of the two is set up -- the work is
idempotent, so both running is harmless.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from billing.tariff_changes import apply_due_tariff_changes


class Command(BaseCommand):
    help = "Switches services onto their pending tariff once the effective date has arrived."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="List what would change without writing anything.",
        )
        parser.add_argument(
            "--as-of", default=None,
            help="Pretend today is this date (YYYY-MM-DD), for checking a change before it lands.",
        )

    def handle(self, *args, **options):
        as_of = options["as_of"]
        if as_of:
            from datetime import date
            try:
                as_of = date.fromisoformat(as_of)
            except ValueError:
                self.stderr.write("--as-of must be YYYY-MM-DD.")
                return
        else:
            as_of = timezone.localdate()

        applied = apply_due_tariff_changes(as_of=as_of, commit=not options["dry_run"])
        if not applied:
            self.stdout.write(f"No tariff changes due on or before {as_of}.")
            return

        for service, old_tariff, new_tariff in applied:
            self.stdout.write(
                f"  {service.customer.full_name} (service #{service.pk}): "
                f"{old_tariff.name} -> {new_tariff.name}"
            )
        verb = "would change" if options["dry_run"] else "changed"
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"{len(applied)} service(s) {verb}."))
        if not options["dry_run"]:
            self.stdout.write(
                "Live sessions were dropped so the new speed applies now; clients reconnect in seconds."
            )
