"""Runs the recurring-billing engine for a given date -- the same logic
Finance -> Recurring Billing's Preview/Run buttons call over the API, just
callable from a shell/cron for eventual unattended scheduling (see the
design notes in billing.recurring and RecurringBillingRun's docstring).

This release, nothing schedules this automatically -- staff use the
Finance screen's Preview -> Run buttons each cycle. A crontab entry to run
this daily with --commit is documented here for when that's ready to flip
on, but is NOT installed by this deploy:

    0 6 * * * cd /home/ubuntu/isp-platform && docker compose exec -T backend \\
        python manage.py run_recurring_billing --commit >> /var/log/skybre-billing.log 2>&1

Usage:
  python manage.py run_recurring_billing                       # preview today, prints counts
  python manage.py run_recurring_billing --date=2026-09-01      # preview a specific date
  python manage.py run_recurring_billing --commit                # actually run + log it
  python manage.py run_recurring_billing --commit --partner=3,5  # scoped to specific partner ids
"""
from datetime import date as date_cls

from django.core.management.base import BaseCommand
from django.utils import timezone

from billing.recurring import run_recurring_billing


class Command(BaseCommand):
    help = "Preview or run the recurring-billing engine (invoicing, reminders, auto-suspension) for a given date."

    def add_arguments(self, parser):
        parser.add_argument("--date", type=str, default=None, help="YYYY-MM-DD. Defaults to today.")
        parser.add_argument("--commit", action="store_true", help="Actually create invoices/send emails/suspend services, and log a RecurringBillingRun. Without this flag, only counts what would happen.")
        parser.add_argument("--partner", type=str, default=None, help="Comma-separated partner IDs to scope this run to. Omit for every partner.")

    def handle(self, *args, **options):
        run_date = date_cls.fromisoformat(options["date"]) if options["date"] else timezone.localdate()
        partner_ids = None
        if options["partner"]:
            partner_ids = [int(p) for p in options["partner"].split(",") if p.strip()]

        result = run_recurring_billing(run_date, partner_ids=partner_ids, commit=options["commit"])
        counts = result["counts"]
        mode = "COMMITTED" if options["commit"] else "preview only, nothing written"
        self.stdout.write(self.style.SUCCESS(
            f"Recurring billing for {run_date} ({mode}): "
            f"{counts['invoices_created']} invoice(s), {counts['proforma_invoices_created']} pro forma(s), "
            f"{counts['reminders_sent']} reminder(s), {counts['suspensions_applied']} suspension(s)."
        ))
        if result["status"] == "failed":
            self.stdout.write(self.style.ERROR(f"Run FAILED: {result['status_message']}"))
