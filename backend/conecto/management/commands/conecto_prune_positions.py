"""Delete GPS pings older than a cut-off.

A ping a minute, per technician, only while en route. That is small --
roughly 60 rows an hour each -- but it is unbounded, and a table nothing
ever deletes from is a table that is fine for a year and then is not.

Deliberately a command you run rather than something that happens by
itself. These rows are where "where was he at half past two" gets
answered from, and a silent nightly delete is exactly the wrong shape for
that: the day somebody needs a three-month-old trail, the answer should
be "we kept it" or "we chose not to", not "something removed it and
nobody remembers deciding that".

    python manage.py conecto_prune_positions --days 90
    python manage.py conecto_prune_positions --days 90 --apply

Dry run by default, because the default for anything that deletes should
be to tell you what it would do.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Delete technician GPS pings older than N days (dry run unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days", type=int, default=90,
            help="Keep pings newer than this many days (default: 90).",
        )
        parser.add_argument(
            "--apply", action="store_true",
            help="Actually delete. Without this the command only reports.",
        )

    def handle(self, *args, **options):
        from conecto.models import ConectoTechnicianPosition

        days = options["days"]
        if days < 1:
            self.stderr.write("--days must be at least 1.")
            return

        cutoff = timezone.now() - timedelta(days=days)
        stale = ConectoTechnicianPosition.objects.filter(recorded_at__lt=cutoff)
        count = stale.count()
        total = ConectoTechnicianPosition.objects.count()

        self.stdout.write(f"cut-off:       {cutoff:%Y-%m-%d %H:%M} ({days} days)")
        self.stdout.write(f"pings stored:  {total}")
        self.stdout.write(f"older than it: {count}")

        if not count:
            self.stdout.write("Nothing to do.")
            return

        if not options["apply"]:
            self.stdout.write("Dry run. Re-run with --apply to delete.")
            return

        deleted, _ = stale.delete()
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} rows."))
