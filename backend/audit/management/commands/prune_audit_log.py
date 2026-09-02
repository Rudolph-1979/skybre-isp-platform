"""Delete audit rows older than a retention window.

Not exposed through the API on purpose. Everything else in this app is
read-only from a browser, and the one operation that destroys history
should require server access rather than a session belonging to somebody
the log might be about.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from audit.models import AuditEvent


class Command(BaseCommand):
    help = "Delete audit events older than the given number of days."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=730, help="Retention window (default 730, two years).")
        parser.add_argument("--commit", action="store_true", help="Actually delete. Without this, only reports.")

    def handle(self, *args, **options):
        days = options["days"]
        if days < 30:
            self.stderr.write("Refusing to prune to under 30 days.")
            return
        cutoff = timezone.now() - timezone.timedelta(days=days)
        qs = AuditEvent.objects.filter(created_at__lt=cutoff)
        count = qs.count()
        if not options["commit"]:
            self.stdout.write(f"{count} events older than {days} days. Re-run with --commit to delete.")
            return
        qs.delete()
        self.stdout.write(self.style.SUCCESS(f"Deleted {count} events older than {days} days."))
