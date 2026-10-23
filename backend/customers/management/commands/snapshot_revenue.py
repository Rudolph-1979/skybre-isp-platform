"""Save today's MRR per partner for Administration -> Revenue (9 Oct 2026).

Run nightly from cron so every day has an exact figure, e.g. 23:50 SAST:

    50 21 * * * cd ~/isp-platform && docker compose exec -T backend python manage.py snapshot_revenue

Opening the Revenue page also refreshes today's row, so a missed night only
means that one day falls back to the estimate.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from customers.revenue_reports import take_snapshot


class Command(BaseCommand):
    help = "Save today's MRR per partner (RevenueSnapshot)."

    def handle(self, *args, **options):
        day = timezone.localdate()
        figures = take_snapshot(day)
        total = sum((row[0] for row in figures.values()), 0)
        clients = set()
        for row in figures.values():
            clients |= row[2]
        self.stdout.write(self.style.SUCCESS(
            f"Revenue snapshot {day}: MRR R{total:.2f}, {len(clients)} paying clients, {len(figures)} partner row(s)."
        ))
