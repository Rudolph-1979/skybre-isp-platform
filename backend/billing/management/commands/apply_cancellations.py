"""End services whose billing end date has arrived, and cancel the customer.

Service.end_date used to be decoration -- staff could set it and the date
would pass with the customer still connected and still billed. This is what
gives it effect. See billing.cancellations for the rules.

Just after midnight, because the cut-off and the date turning are meant to be
the same moment -- the end date is the first day WITHOUT service, so a service
ending on the 31st stops at 00:00 on the 31st. Running it at 02:30 instead
would hand the customer two and a half hours they were not sold:

    1 0 * * * cd /home/ubuntu/isp-platform && /usr/bin/docker compose exec -T \\
        backend python manage.py apply_cancellations --commit >> \\
        /home/ubuntu/isp-platform/logs/cancellations.log 2>&1

run_recurring_billing also calls it, before invoicing, as a backstop for the
case where this job did not run at all.

Reports and changes nothing without --commit.
"""
import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from audit.context import acting_as_system
from billing.cancellations import apply_due_cancellations


class Command(BaseCommand):
    help = "End services on their billing end date and cancel customers left with none."

    def add_arguments(self, parser):
        parser.add_argument("--commit", action="store_true",
                            help="Actually end them. Without this, only reports what would happen.")
        parser.add_argument("--as-of", help="Pretend today is this date (YYYY-MM-DD), for checking ahead.")

    def handle(self, *args, **options):
        as_of = timezone.localdate()
        if options["as_of"]:
            try:
                as_of = datetime.datetime.strptime(options["as_of"], "%Y-%m-%d").date()
            except ValueError:
                self.stderr.write("--as-of must be YYYY-MM-DD.")
                return

        commit = options["commit"]
        # Names itself in the audit trail. A customer cancelled overnight
        # by this job would otherwise show up as a change nobody made,
        # which is the reading most likely to send somebody hunting for a
        # staff member to blame.
        with acting_as_system("apply_cancellations (scheduled job)"):
            ended, cancelled = apply_due_cancellations(as_of=as_of, commit=commit)

        if not ended and not cancelled:
            self.stdout.write(f"Nothing due on {as_of}.")
            return

        if ended:
            self.stdout.write(self.style.MIGRATE_HEADING(f"Services ending on or before {as_of}"))
            for pk, label in ended:
                self.stdout.write(f"  #{pk}  {label}")
        if cancelled:
            self.stdout.write("")
            self.stdout.write(self.style.MIGRATE_HEADING("Customers cancelled (no services left)"))
            for pk, name in cancelled:
                self.stdout.write(f"  #{pk}  {name}")

        self.stdout.write("")
        if commit:
            self.stdout.write(self.style.SUCCESS(
                f"Ended {len(ended)} service(s); cancelled {len(cancelled)} customer(s)."
            ))
        else:
            self.stdout.write(
                "Dry run — nothing changed. Re-run with --commit.\n"
                "Committing DISCONNECTS these customers."
            )
