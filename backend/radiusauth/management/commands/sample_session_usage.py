"""Work out live throughput per session from the accounting counters.

`radacct` holds cumulative bytes and FreeRADIUS overwrites the row on every
interim update, so the table can say how much a session has used but never
how fast it is going. This command remembers the previous counters
(SessionUsageSnapshot) and derives a rate from the difference.

Run it on a schedule on the VPS -- every minute is plenty:

    * * * * * cd /home/ubuntu/isp-platform && docker compose exec -T backend \\
        python manage.py sample_session_usage >/dev/null 2>&1

It touches no router. The NAS pushes counters to FreeRADIUS on its own
accounting interval; this only reads the database. So the resolution of the
figures is set by the NAS's interim interval, NOT by how often this runs --
sampling every second against a 5-minute interim would just report the same
number 300 times.

    /ppp aaa set interim-update=1m     (on the router, for 1-minute figures)

It also BANKS each delta it observes into UsageBucket, which is what makes
per-day and per-hour usage answerable at all -- see that model's docstring.
The rate and the usage figure therefore come from the same measurement, so
they can never disagree about how many bytes moved.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from ...models import RadAcct, SessionUsageSnapshot
from ...usage import bank_usage


class Command(BaseCommand):
    help = "Derive live per-session throughput from radacct's cumulative counters."

    def add_arguments(self, parser):
        parser.add_argument(
            "--stale-after",
            type=int,
            default=900,
            help=(
                "Seconds without the counters advancing before a session's rate is "
                "reported as 0 rather than the last known value (default 900). Should "
                "be comfortably more than the NAS's interim-update interval."
            ),
        )

    def handle(self, *args, **options):
        now = timezone.now()
        stale_after = options["stale_after"]

        live = RadAcct.objects.filter(acctstoptime__isnull=True).values(
            "acctuniqueid", "username", "acctinputoctets", "acctoutputoctets"
        )
        live_ids = set()
        snapshots = {
            s.acctuniqueid: s
            for s in SessionUsageSnapshot.objects.filter(
                acctuniqueid__in=[r["acctuniqueid"] for r in live]
            )
        }

        created = updated = 0
        banked = 0
        for row in live:
            uid = row["acctuniqueid"]
            live_ids.add(uid)
            in_octets = row["acctinputoctets"] or 0
            out_octets = row["acctoutputoctets"] or 0
            snap = snapshots.get(uid)

            if snap is None:
                # First sighting: no baseline, so no rate yet. Reporting a
                # rate here would mean dividing a session's whole lifetime
                # of traffic by nothing.
                #
                # Nothing is banked either, deliberately. The counters at this
                # moment are the session's traffic SO FAR, and banking them
                # would put an already-running session's entire history into
                # the hour we happened to first see it -- a spike on the day
                # this feature was switched on, and again after any outage of
                # this command. The cost is under-counting the first partial
                # interval of each session, at most one interim-update's
                # worth. Under-counting a few minutes beats inventing a spike.
                SessionUsageSnapshot.objects.create(
                    acctuniqueid=uid,
                    username=row["username"] or "",
                    last_input_octets=in_octets,
                    last_output_octets=out_octets,
                    last_change_at=now,
                    input_bps=0,
                    output_bps=0,
                    sampled_at=now,
                )
                created += 1
                continue

            if in_octets < snap.last_input_octets or out_octets < snap.last_output_octets:
                # Counters went backwards. FreeRADIUS reuses acctuniqueid
                # only per session, so this means the NAS restarted its
                # counters mid-session. Re-baseline rather than reporting a
                # nonsensical negative or enormous rate.
                #
                # The post-reset values ARE real traffic since the reset and
                # have never been banked, so they are banked now. Small by
                # definition: the reset just happened.
                bank_usage(row["username"] or "", now, download=out_octets, upload=in_octets)
                banked += out_octets + in_octets
                snap.last_input_octets = in_octets
                snap.last_output_octets = out_octets
                snap.last_change_at = now
                snap.input_bps = 0
                snap.output_bps = 0
            elif in_octets == snap.last_input_octets and out_octets == snap.last_output_octets:
                # No new accounting data since last time. Keep the last known
                # rate -- it is the correct answer for the period it was
                # measured over -- unless it has gone stale, in which case the
                # session is genuinely idle (or accounting has stopped) and 0
                # is more honest than a number from ten minutes ago.
                elapsed = (now - snap.last_change_at).total_seconds()
                if elapsed > stale_after:
                    snap.input_bps = 0
                    snap.output_bps = 0
            else:
                # The counters advanced. This difference is the ONLY place the
                # platform ever sees how many bytes moved in a bounded window,
                # so it feeds both the rate and the usage bucket.
                up_delta = in_octets - snap.last_input_octets
                down_delta = out_octets - snap.last_output_octets
                bank_usage(row["username"] or "", now, download=down_delta, upload=up_delta)
                banked += down_delta + up_delta

                seconds = (now - snap.last_change_at).total_seconds()
                if seconds > 0:
                    snap.input_bps = int(up_delta * 8 / seconds)
                    snap.output_bps = int(down_delta * 8 / seconds)
                snap.last_input_octets = in_octets
                snap.last_output_octets = out_octets
                snap.last_change_at = now

            snap.username = row["username"] or snap.username
            snap.sampled_at = now
            snap.save()
            updated += 1

        # Drop snapshots for sessions that have ended, so this table tracks
        # live sessions and nothing else.
        stale = SessionUsageSnapshot.objects.exclude(acctuniqueid__in=live_ids)
        removed = stale.count()
        stale.delete()

        self.stdout.write(
            f"{len(live_ids)} live session(s): {created} new, {updated} updated, "
            f"{removed} finished session(s) cleared. Banked {banked} byte(s)."
        )
