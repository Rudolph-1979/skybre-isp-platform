"""Syncs every active BankAccount via FNBClient -- the same logic the Bank
Feeds screen's "Sync now" button calls per-account, just callable from a
shell/cron for the hourly unattended schedule this was scoped for.

This release, nothing schedules this automatically -- a crontab entry to
run it hourly is documented here for when it's ready to be installed, but
is NOT installed by this deploy (same convention as run_recurring_billing):

    0 * * * * cd /home/ubuntu/isp-platform && docker compose exec -T backend \\
        python manage.py sync_bank_feeds >> /var/log/skybre-bankfeeds.log 2>&1

Accounts with no api_base_url configured yet (i.e. every account, until
FNB confirms API access -- see fnb_client.py) will simply log a failed
sync explaining that; this doesn't need to be commented out or worked
around before turning the cron on, and doesn't stop CSV import from
working in the meantime.

Usage:
  python manage.py sync_bank_feeds
"""
from django.core.management.base import BaseCommand

from bankfeeds.sync import sync_all_active_accounts


class Command(BaseCommand):
    help = "Sync transactions for every active bank account (see bankfeeds.sync)."

    def handle(self, *args, **options):
        logs = sync_all_active_accounts()
        if not logs:
            self.stdout.write(self.style.WARNING("No active bank accounts configured -- nothing to sync."))
            return
        for log in logs:
            line = (
                f"{log.account.name}: {log.status} -- {log.transactions_fetched} fetched, "
                f"{log.transactions_new} new, {log.transactions_matched} auto-matched"
            )
            if log.status == log.Status.FAILED:
                self.stdout.write(self.style.ERROR(f"{line} ({log.status_message})"))
            else:
                self.stdout.write(self.style.SUCCESS(line))
