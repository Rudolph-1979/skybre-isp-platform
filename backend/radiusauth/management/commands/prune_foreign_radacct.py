"""Remove accounting rows for sessions that are not ours.

Why this exists
---------------
`radacct` is written by FreeRADIUS, not by this platform, and it records
whatever the NASes send. On 2026-09-15 that turned out to include 48 of
55 open sessions belonging to Grunder Co customers who live on SPLYNX --
same company, same routers, different platform. Their CPE attaches to the
PPPoE server whose service-name is "Skybre", so their sessions carry
`Called-Station-Id: Skybre`, which matches our `/radius` entry -- and that
entry sits first in the router's list, so RouterOS sends us their
accounting even though we reject their logins outright (every one of them
is an Access-Reject in radpostauth).

They are our customers. They are not our sessions: Splynx authenticates
them, Splynx bills them, and Splynx is where their usage must be counted.
A copy of their accounting sitting here is a second set of books for
lines this platform does not manage -- which is worth removing not for
privacy but for arithmetic.

What this does NOT do
---------------------
It does not touch FreeRADIUS. Filtering at write time means editing the
stock accounting queries, and getting one of start/interim/stop wrong
would cost us accounting for our OWN customers -- a far worse failure
than storing rows we don't want. So the write path is left alone and the
rows are pruned afterwards, where a mistake is visible and recoverable
from the backup this command writes.

It also does not hide the problem. The count it reports IS the measure of
the router misconfiguration; if it keeps finding thousands of rows, the
router still needs fixing. Silently dropping them at the NAS would make
the next occurrence invisible.

Usage
-----
    python manage.py prune_foreign_radacct              # report only
    python manage.py prune_foreign_radacct --apply
    python manage.py prune_foreign_radacct --apply --include-open
    python manage.py prune_foreign_radacct --apply --older-than-days 7
"""
import json
import os
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Count, Max, Min
from django.utils import timezone

from radiusauth.models import RadAcct
from radiusauth.ownership import known_username_count, not_ours_q

BACKUP_PREFIX = "radacct-foreign-pruned"


class Command(BaseCommand):
    help = "Delete radacct rows whose username belongs to no service on this platform."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually delete. Without this the command only reports.",
        )
        parser.add_argument(
            "--include-open",
            action="store_true",
            help=(
                "Also delete sessions with no stop time. Off by default: an open "
                "foreign session is still receiving interim updates, so deleting it "
                "just means the next update recreates it."
            ),
        )
        parser.add_argument(
            "--older-than-days",
            type=int,
            default=0,
            help="Only touch sessions that started more than this many days ago. 0 = no age limit.",
        )
        parser.add_argument(
            "--no-backup",
            action="store_true",
            help="Skip writing the JSON backup. Not recommended for the first run.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        include_open = options["include_open"]
        older_than_days = options["older_than_days"]

        # The definition of "ours" is shared with the Live Sessions page --
        # see radiusauth/ownership.py for why it is one module and not two
        # copies.
        known = known_username_count()
        if known == 0:
            # Refusing rather than deleting everything. Zero known usernames
            # means the services table didn't load, not that we have no
            # customers -- and the difference is the whole accounting table.
            self.stderr.write(
                self.style.ERROR(
                    "No RADIUS usernames found on any service and none in radcheck. "
                    "Refusing to run -- every session would count as foreign."
                )
            )
            return

        # A row with no username at all is reported as not-ours on screen,
        # but is never deleted here: it can't be matched to a customer and
        # it isn't evidence of another ISP either, so there is nothing to
        # gain from destroying it.
        qs = RadAcct.objects.filter(not_ours_q()).exclude(username__isnull=True)
        if not include_open:
            qs = qs.filter(acctstoptime__isnull=False)
        if older_than_days > 0:
            cutoff = timezone.now() - timedelta(days=older_than_days)
            qs = qs.filter(acctstarttime__lt=cutoff)

        total = qs.count()
        self.stdout.write(f"Known RADIUS usernames on this platform: {known}")
        self.stdout.write(f"Rows in radacct: {RadAcct.objects.count()}")
        self.stdout.write(
            f"Rows matching none of them{'' if include_open else ' (closed sessions only)'}: {total}"
        )

        if total == 0:
            self.stdout.write(self.style.SUCCESS("Nothing to prune."))
            return

        self.stdout.write("")
        self.stdout.write("By NAS:")
        for row in qs.values("nasipaddress").annotate(n=Count("radacctid")).order_by("-n"):
            self.stdout.write(f"  {row['nasipaddress']:<16} {row['n']}")

        self.stdout.write("")
        self.stdout.write("By username prefix:")
        # Prefix, not full username: the point is to recognise whose
        # customers these are at a glance, not to list four thousand names.
        prefixes = {}
        for name in qs.values_list("username", flat=True):
            key = (name or "").split("_")[0] or "(blank)"
            prefixes[key] = prefixes.get(key, 0) + 1
        for key, count in sorted(prefixes.items(), key=lambda kv: -kv[1])[:20]:
            self.stdout.write(f"  {key:<16} {count}")

        span = qs.aggregate(first=Min("acctstarttime"), last=Max("acctstarttime"))
        self.stdout.write("")
        self.stdout.write(f"Session start range: {span['first']} -> {span['last']}")

        if not apply_changes:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING("Dry run -- nothing deleted. Re-run with --apply to delete.")
            )
            return

        backup_path = None
        if not options["no_backup"]:
            backup_path = self._write_backup(qs)
            self.stdout.write("")
            self.stdout.write(f"Backup written: {backup_path}")
            self.stdout.write(
                "  Session data for lines Splynx manages. Keep it only until you're "
                "satisfied the prune was correct, then delete it."
            )

        # Delete by primary key rather than deleting the queryset directly.
        # The queryset is lazy: between counting and deleting, FreeRADIUS
        # writes more rows, and a plain qs.delete() would remove rows that
        # were never counted or backed up.
        ids = list(qs.values_list("radacctid", flat=True))
        deleted, _ = RadAcct.objects.filter(radacctid__in=ids).delete()

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} row(s)."))
        self.stdout.write(
            "More will arrive while the router keeps sending us Splynx lines' accounting. "
            "This command treats the symptom; the cause is that our RADIUS server answers "
            "for usernames it doesn't know instead of staying silent and letting RouterOS "
            "fall through to Splynx."
        )

    def _write_backup(self, qs):
        stamp = timezone.now().strftime("%Y%m%d-%H%M%S")
        directory = getattr(settings, "MEDIA_ROOT", "") or "."
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, f"{BACKUP_PREFIX}-{stamp}.json")

        rows = list(
            qs.values(
                "radacctid", "acctsessionid", "acctuniqueid", "username", "nasipaddress",
                "calledstationid", "callingstationid", "framedipaddress",
                "acctstarttime", "acctupdatetime", "acctstoptime", "acctsessiontime",
                "acctinputoctets", "acctoutputoctets", "acctterminatecause",
            )
        )
        with open(path, "w") as handle:
            json.dump(rows, handle, indent=2, default=str)
        os.chmod(path, 0o600)
        return path
