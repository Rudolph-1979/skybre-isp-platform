"""Send the "your line will be suspended on <date>" emails.

WHY THIS IS ITS OWN COMMAND, and not the billing Run on a schedule.

A Run raises invoices, debits balances, emails PDFs and suspends
customers. None of that is allowed to happen without a person: Preview ->
Approve -> Run exists so a human signs off the exact numbers first, and
the approval is checked against a fingerprint of those numbers at the
moment Run is pressed. Putting that in cron would remove the only real
safeguard on the money side of this platform.

This does one thing: emails customers whose cut-off falls within the next
`warning_days`. Nothing is billed, nothing is cancelled, no balance
moves, nobody is cut off. The worst outcome of a bad run here is an email
somebody did not need, which is a phone call, not a wrong invoice. That
is why this one is safe to schedule and the rest is not.

A DAILY COUNTDOWN: a customer in the notice period is warned each day,
with the days remaining falling as the cut-off approaches. Deduped per
day, so an overlapping cron job, a manual re-run, or a Run happening the
same afternoon all send nothing extra.

It also reports anyone already PAST their cut-off and still connected.
Those are not warned (a date in the past cannot honestly be put in "your
line will be suspended on"), but they are the ones who most need looking
at: nothing has suspended them. The next Run will, because the engine's
blocking test is `date_due <= run_date - blocking_period_days`, which
keeps matching however late the run is -- it catches up rather than
skipping a missed day. What it cannot do is run itself.

Usage:
    python manage.py send_suspension_warnings              # report only
    python manage.py send_suspension_warnings --apply
    python manage.py send_suspension_warnings --date 2026-09-20 --apply

Daily, from the host's crontab (07:10, after people are at their desks
and before the day's work starts):

    10 7 * * * cd /home/ubuntu/isp-platform && docker compose exec -T \\
        backend python manage.py send_suspension_warnings --apply \\
        >> /var/log/skybre-warnings.log 2>&1
"""
import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from billing.models import SuspensionSettings
from billing.recurring import _process_suspension_warning, suspension_block_date
from customers.models import Customer


class Command(BaseCommand):
    help = "Email customers who are heading for an auto-suspension, before it happens."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true",
            help="Actually send. Without this the command only reports who would be warned.",
        )
        parser.add_argument(
            "--date", default="", metavar="YYYY-MM-DD",
            help="Treat this as today. Default: today. Useful for checking a date ahead.",
        )

    def handle(self, *args, **options):
        if options["date"]:
            try:
                run_date = datetime.date.fromisoformat(options["date"])
            except ValueError:
                self.stderr.write(self.style.ERROR("--date must be YYYY-MM-DD."))
                return
        else:
            run_date = timezone.localdate()

        settings = SuspensionSettings.load()
        if not settings.warning_enabled:
            self.stdout.write(self.style.WARNING(
                "Suspension warnings are switched off (Configs -> Billing -> Auto-suspension). "
                "Nothing to do."
            ))
            return

        horizon = run_date + datetime.timedelta(days=settings.warning_days or 0)
        self.stdout.write(
            f"Warning customers whose cut-off falls between {run_date + datetime.timedelta(days=1)} "
            f"and {horizon} ({settings.warning_days} days from {run_date})."
        )

        customers = (
            Customer.objects.filter(billing_config__billing_enabled=True)
            .select_related("billing_config")
            .order_by("customer_id")
        )

        warned, already, overdue_unblocked = [], [], []
        for customer in customers:
            config = customer.billing_config
            block_date = suspension_block_date(customer, config, run_date)
            if block_date is None:
                continue
            days_until = (block_date - run_date).days

            # Already at or past their cut-off and still connected. Not a
            # warning case -- naming a date in the past would read as a
            # mistake -- but the single most important line this command
            # prints: these people are overdue for suspension and nothing
            # has done it. Counted so they cannot sit there unnoticed.
            if days_until < 1:
                overdue_unblocked.append((customer, block_date))
                continue

            # commit=False against the engine's own function, so the report
            # is exactly the set a send would touch rather than a second
            # opinion that could disagree with it.
            if _process_suspension_warning(customer, config, settings, run_date, commit=False):
                warned.append((customer, block_date, days_until))
            elif config.suspension_warned_on == run_date:
                already.append(customer)

        if warned:
            self.stdout.write("")
            width = max(len(c.customer_id or "") for c, _, _ in warned)
            for customer, block_date, days_until in warned:
                self.stdout.write(
                    f"  {customer.customer_id:<{width}}  {customer.full_name}  "
                    f"cut off {block_date} (in {days_until} day(s))  R{customer.balance}  "
                    f"{customer.email or 'NO EMAIL ON FILE'}"
                )
        else:
            self.stdout.write("Nobody new is due a warning today.")

        if already:
            self.stdout.write("")
            self.stdout.write(
                f"{len(already)} already warned today -- not sent again."
            )

        if overdue_unblocked:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(
                f"{len(overdue_unblocked)} customer(s) are PAST their cut-off and still "
                "connected. They are not warned -- they are overdue for suspension:"
            ))
            for customer, block_date in overdue_unblocked[:20]:
                self.stdout.write(
                    f"  {customer.customer_id}  {customer.full_name}  "
                    f"due for cut-off since {block_date}  R{customer.balance}"
                )
            if len(overdue_unblocked) > 20:
                self.stdout.write(f"  ...and {len(overdue_unblocked) - 20} more")
            self.stdout.write(
                "The next Finance -> Recurring Billing Run will suspend them (the engine "
                "catches up rather than skipping a missed day). If that is not what you "
                "want, deal with them by hand first."
            )

        if not options["apply"]:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(
                f"Dry run -- {len(warned)} email(s) NOT sent. Re-run with --apply."
            ))
            self._footnote(settings)
            return

        sent = 0
        for customer, _, _ in warned:
            sent += _process_suspension_warning(
                customer, customer.billing_config, settings, run_date, commit=True
            )

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Sent {sent} warning(s)."))
        self._footnote(settings)

    def _footnote(self, settings):
        """The two things that make this quietly do nothing, said every run
        rather than left for somebody to work out from an empty list."""
        if not settings.auto_suspend_enabled:
            self.stdout.write("")
            self.stdout.write(
                "Note: auto-suspension itself is OFF, so nobody will actually be cut off on "
                "the date these warnings name. The warnings are still accurate about who is "
                "overdue -- but if it stays off, the deadline passes and nothing happens."
            )
        from notifications.models import EmailTemplate

        if not EmailTemplate.objects.filter(key=EmailTemplate.Key.SUSPENSION_WARNING).exists():
            self.stdout.write("")
            self.stdout.write(self.style.ERROR(
                "There is no 'suspension_warning' email template, so nothing can be sent. "
                "Create it under Configs -> Email templates."
            ))
