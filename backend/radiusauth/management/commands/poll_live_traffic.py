"""Read what every line is actually doing right now, straight from the routers.

SUPERSEDED -- and should no longer be in cron.

This polled every router every minute around the clock, whether or not a
single person was looking at a single customer: about 1,440 logins per router
per day, nearly all of them for nobody. A router's own log made that plain --
our username every minute, forever.

network.live_broker replaces it. A router connection is now opened when a
staff member opens a customer page and closed within seconds of them leaving,
and while it is open the figures are read once a SECOND rather than once every
ten. Better numbers, and the router log goes quiet when nobody is watching.

The command is kept because it is still the right tool for a one-off check
from a shell, and for the case the broker cannot cover: warming figures for
every customer at once, without a viewer. Run it by hand; do not schedule it.

Remove the old crontab line:

    crontab -l | grep -v poll_live_traffic | crontab -


Why this exists alongside `sample_session_usage`: accounting figures are
averages over the NAS's reporting interval, so a short burst reads low.
This is the live number -- the router's own interface counters, sampled
often enough to reflect a speed test while it is happening.

The cost is deliberately per-ROUTER, not per-customer: one connection
returns every active session's counters (see
network.mikrotik.get_all_session_traffic), so live figures for a thousand
customers cost the same as for one, and having ten staff members watching
ten customers costs nothing extra.

Cron can only fire once a minute, which is far too coarse for a live
reading, so this loops internally instead:

    * * * * * cd /home/ubuntu/isp-platform && docker compose exec -T backend \\
        python manage.py poll_live_traffic --interval 10 --seconds 55 >/dev/null 2>&1

That polls every 10 seconds continuously, and each run ends before the next
minute starts so runs never overlap.
"""
import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from network import mikrotik
from network.models import Device

from ...models import RouterLiveRate


class Command(BaseCommand):
    help = "Poll routers for live per-session throughput (one call per router)."

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=int, default=10, help="Seconds between polls (default 10).")
        parser.add_argument(
            "--seconds",
            type=int,
            default=0,
            help=(
                "Keep polling for this many seconds, then exit. 0 (default) polls once. "
                "Set slightly under your cron period so runs never overlap."
            ),
        )
        parser.add_argument(
            "--stale-after",
            type=int,
            default=120,
            help="Drop rows not seen for this many seconds (default 120).",
        )

    def handle(self, *args, **options):
        interval = max(1, options["interval"])
        deadline = time.monotonic() + options["seconds"] if options["seconds"] else None

        while True:
            self._poll_once(options["stale_after"])
            if deadline is None or time.monotonic() + interval > deadline:
                break
            time.sleep(interval)

    def _poll_once(self, stale_after):
        now = timezone.now()
        devices = Device.objects.filter(api_enabled=True)
        seen = set()

        for device in devices:
            # One lock per device, shared with the other live-API features,
            # so this can never contend with a staff member clicking
            # "Read now" or a queue sync on the same router.
            with mikrotik.get_device_lock(device.pk):
                try:
                    sessions = mikrotik.get_all_session_traffic(device)
                except mikrotik.MikrotikError as exc:
                    # A router being unreachable is normal and must not stop
                    # the others being polled.
                    self.stderr.write(f"{device.name}: {exc}")
                    continue

            existing = {
                r.username: r
                for r in RouterLiveRate.objects.filter(username__in=list(sessions.keys()))
            }

            for username, counters in sessions.items():
                seen.add(username)
                rx, tx = counters["rx_byte"], counters["tx_byte"]
                row = existing.get(username)

                if row is None:
                    # First sighting: no baseline, so no rate yet. Reporting
                    # one here would divide the session's whole lifetime of
                    # traffic by a single interval.
                    RouterLiveRate.objects.update_or_create(
                        username=username,
                        defaults={
                            "device": device,
                            "interface": counters["interface"],
                            "last_rx_byte": rx,
                            "last_tx_byte": tx,
                            "download_bps": 0,
                            "upload_bps": 0,
                            "sampled_at": now,
                        },
                    )
                    continue

                elapsed = (now - row.sampled_at).total_seconds()
                if rx < row.last_rx_byte or tx < row.last_tx_byte:
                    # Counters went backwards: the session reconnected and
                    # RouterOS created a fresh interface. Re-baseline rather
                    # than reporting a negative or absurd rate.
                    row.download_bps = 0
                    row.upload_bps = 0
                elif elapsed > 0:
                    # tx = router -> client = the customer's download.
                    row.download_bps = int((tx - row.last_tx_byte) * 8 / elapsed)
                    row.upload_bps = int((rx - row.last_rx_byte) * 8 / elapsed)

                row.device = device
                row.interface = counters["interface"]
                row.last_rx_byte = rx
                row.last_tx_byte = tx
                row.sampled_at = now
                row.save()

        # Anything not seen for a while is gone -- a finished session, or a
        # router that has stopped answering. Either way a rate from two
        # minutes ago should not still be presented as "now".
        cutoff = now - timezone.timedelta(seconds=stale_after)
        RouterLiveRate.objects.filter(sampled_at__lt=cutoff).delete()

        self.stdout.write(f"{now:%H:%M:%S} polled {devices.count()} router(s), {len(seen)} session(s)")
