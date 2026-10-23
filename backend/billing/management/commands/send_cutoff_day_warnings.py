"""Warn the people whose line goes off later today.

    python manage.py send_cutoff_day_warnings                 # dry run
    python manage.py send_cutoff_day_warnings --commit

Runs in the MORNING, ahead of the recurring-billing run that does the
disconnecting in the afternoon:

    0 9 * * * cd /home/ubuntu/isp-platform && docker compose exec -T backend \\
        python manage.py send_cutoff_day_warnings --commit >> logs/billing.log 2>&1
    0 15 * * * cd /home/ubuntu/isp-platform && docker compose exec -T backend \\
        python manage.py run_recurring_billing --commit >> logs/billing.log 2>&1

WHY THIS IS NOT PART OF THE ENGINE

recurring._process_suspension_warning refuses to warn anybody whose
cut-off is today, and says why: "Today's are the suspension pass's
business and would get both emails in the same minute." That is correct
for a single pass -- the engine does warnings and blocking one after the
other, and nothing in it knows what time it is.

Splitting the two across the day makes the same-day warning meaningful
rather than absurd, but only because the disconnection is hours away
rather than microseconds. So the rule stays exactly as it is inside the
engine, and this command carries the case the engine deliberately will
not: warn today, for a cut-off happening today, because something else
is doing the cutting later.

GATED ON auto_suspend_enabled, AND THAT IS THE OPPOSITE OF THE ENGINE'S
WARNING. The countdown warning is deliberately ungated -- warning people
while the cut-off is still disarmed is a reasonable way to run a first
cycle. This one cannot be: it says a line goes off today. With
auto-suspension off, nothing will cut anyone off, and the email would be
a threat the platform has no intention of carrying out. A warning nobody
acts on teaches customers to ignore the next one.

DRY RUN BY DEFAULT, like every other command here that sends real email
to real customers.

SIX HOURS IS NOT LONG. An EFT does not clear in an afternoon. This is a
courtesy so the disconnection is not a surprise, not a payment window --
if the intent is to give somebody a chance to pay, the engine's countdown
warning days in advance is the thing that does that, and the two can run
together.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Warn customers being suspended later today (dry run unless --commit)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--client-code", help="One customer only, e.g. SMF463.",
        )
        parser.add_argument(
            "--commit", action="store_true", help="Actually send. Without it, nothing leaves.",
        )

    def handle(self, *args, **options):
        from billing.models import CustomerBillingConfig, SuspensionSettings
        from billing.recurring import blocking_candidate_services
        from notifications.models import EmailLog, EmailTemplate
        from notifications.services import send_customer_email

        today = timezone.localdate()
        suspension_settings = SuspensionSettings.objects.first()
        if suspension_settings is None or not suspension_settings.auto_suspend_enabled:
            self.stdout.write(
                "Auto-suspension is off platform-wide. Nobody is being cut off today, "
                "so nobody is being told they are."
            )
            return

        configs = CustomerBillingConfig.objects.filter(
            billing_enabled=True
        ).select_related("customer")
        if options["client_code"]:
            code = options["client_code"].strip()
            configs = configs.filter(customer__customer_id__iexact=code)

        warned = skipped_silent = already = failed = 0
        for config in configs:
            customer = config.customer
            services = blocking_candidate_services(customer, config, today)
            if not services:
                continue
            if not config.send_billing_notifications:
                # Counted and printed rather than passed over: somebody is
                # losing their line today with no email, and that is worth
                # seeing in the log.
                skipped_silent += 1
                self.stdout.write(
                    f"  {customer.customer_id} {customer.full_name} -- CUT OFF TODAY, "
                    f"notifications off, no warning sent"
                )
                continue
            if config.suspension_warned_on == today:
                already += 1
                continue

            self.stdout.write(
                f"  {customer.customer_id} {customer.full_name} -- "
                f"{len(services)} service(s), balance {customer.balance}"
            )
            if not options["commit"]:
                warned += 1
                continue

            log = send_customer_email(
                EmailTemplate.Key.SUSPENSION_WARNING, customer,
                extra_context={"suspend_date": today, "days_until_suspension": 0},
            )
            if log is not None and log.status == EmailLog.Status.SENT:
                # Recorded only once the mailer says it went, the same rule
                # the reminder path uses: a warning marked sent after a mail
                # outage is a warning nobody receives and nobody re-sends.
                CustomerBillingConfig.objects.filter(pk=config.pk).update(
                    suspension_warned_on=today
                )
                warned += 1
            else:
                failed += 1

        verb = "warned" if options["commit"] else "would warn"
        line = f"{today}: {verb} {warned}"
        if already:
            line += f", {already} already warned today"
        if skipped_silent:
            line += f", {skipped_silent} being cut off with notifications off"
        if failed:
            line += f", {failed} failed to send"
        self.stdout.write("\n" + line)
        if not options["commit"] and warned:
            self.stdout.write("Nothing was sent. Add --commit when the list looks right.")
