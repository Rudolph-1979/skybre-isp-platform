"""Push speed changes to lines that are already connected.

A rate limit is applied by the router AT LOGIN. That is fine for a plan
change, which is rare and can wait for a reconnect -- but a speed window
that opens at 22:00 is worth nothing if the customers already online keep
the daytime limit until they happen to reboot their router. The same goes
for a line crossing its fair-use threshold mid-month.

So this runs on a schedule, works out what each live line SHOULD be
running at, and sends a CoA to the ones that have changed:

    */5 * * * * cd /home/ubuntu/isp-platform && /usr/bin/docker compose exec -T \\
        backend python manage.py apply_speed_policies --commit >> \\
        /home/ubuntu/isp-platform/logs/speed-policies.log 2>&1

Every five minutes rather than exactly on the boundary, because a cron
that fires only at 22:00 has no second chance: a router unreachable for
those thirty seconds keeps the wrong limit for the whole night. Five
minutes means the worst case is a customer getting their boost a few
minutes late, which nobody notices.

THREE THINGS THIS DELIBERATELY WILL NOT DO
------------------------------------------
1. It never disconnects anybody. apply_change's normal fallback, when CoA
   cannot be delivered, is to drop the session so it reconnects on the new
   settings -- fine for a change somebody just made and is watching for,
   catastrophic here. This fires for EVERY connected customer at a window
   boundary, so one network-wide CoA failure (wrong shared secret, UDP
   3799 blocked) would disconnect the entire customer base at 22:00 to
   deliver a boost nobody asked for. It passes allow_disconnect_fallback
   =False and lets the new speed wait for the next reconnect.

2. It will not push more than --limit changes in one run (default 50).
   Every scenario where this job wants to touch hundreds of lines at once
   is a mistake -- a misconfigured window, or a first run against an
   un-seeded database. The cap turns "the whole network at once" into
   "fifty, then look at the log".

3. It will not run twice at the same time. If a run is slow, the next
   cron tick would otherwise start alongside it and push the same changes
   again.

FIRST RUN
---------
last_pushed_rate_limit is blank until something has been pushed, so on a
fresh database EVERY line looks changed. Seed the baseline first, which
records what the ROUTER currently has -- each line's PLAIN PLAN SPEED,
which is what it was handed at login -- WITHOUT touching the router:

    python manage.py apply_speed_policies --seed

Deliberately the plan speed and not the current effective speed. Seeding
while a window happens to be open would otherwise write down the boosted
figure as though it had already been delivered; the next run would see
nothing to do, and the boost would never be pushed at all. Silent, and
indistinguishable from the feature not working.

Reports and changes nothing without --commit or --seed.
"""
from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone

from audit.context import acting_as_system
from billing.models import Service
from radiusauth.speeds import describe, effective_speeds, plan_speeds

# Same namespace as the live-traffic broker's locks, with its own key, so
# the two can never collide.
_LOCK_NAMESPACE = 0x5CB7
_LOCK_KEY = 0x5D01


def _try_lock():
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s, %s)", [_LOCK_NAMESPACE, _LOCK_KEY])
        return bool(cursor.fetchone()[0])


def _unlock():
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_unlock(%s, %s)", [_LOCK_NAMESPACE, _LOCK_KEY])


class Command(BaseCommand):
    help = "Push fair-use and speed-window changes to live sessions."

    def add_arguments(self, parser):
        parser.add_argument("--commit", action="store_true", help="Actually push. Without this, only reports.")
        parser.add_argument(
            "--seed", action="store_true",
            help="Record what each line should be running at WITHOUT contacting any router. "
                 "Run this once on a fresh database so the first real run only pushes genuine changes.",
        )
        parser.add_argument(
            "--limit", type=int, default=50,
            help="Most changes to push in one run (default 50). A run that wants more than this "
                 "is nearly always a misconfiguration; the rest are picked up next tick.",
        )
        parser.add_argument("--username", help="Limit to one RADIUS username, for checking a single line.")

    def handle(self, *args, **options):
        from radiusauth.enforcement import apply_change, find_live_session

        commit, seed, limit = options["commit"], options["seed"], options["limit"]

        if commit and not _try_lock():
            self.stdout.write("Another run is still going. Skipping this tick.")
            return

        try:
            self._run(options, commit, seed, limit, apply_change, find_live_session)
        finally:
            if commit:
                _unlock()

    def _run(self, options, commit, seed, limit, apply_change, find_live_session):
        now = timezone.localtime()
        qs = (
            Service.objects.select_related("tariff", "connection_rule", "device")
            .filter(status=Service.Status.ACTIVE)
            .exclude(radius_username__isnull=True)
            .exclude(radius_username="")
        )
        if options["username"]:
            qs = qs.filter(radius_username=options["username"])

        changed, skipped, offline, failed, seeded, deferred = [], 0, 0, 0, 0, 0
        # Devices whose STATIC simple queues need re-pushing. See the
        # re-sync below for why a CoA on its own is not enough there.
        shaper_devices = set()

        for service in qs:
            if seed:
                # Seeding runs FIRST and UNCONDITIONALLY -- before the
                # "already correct" check below, and overwriting whatever
                # is there.
                #
                # It used to sit after that check, which made it useless
                # for the one job it exists to do. A row already holding
                # the wrong value -- the boosted rate, written by the
                # earlier seeding bug -- matches the target exactly, so
                # the check skipped it and the seed never corrected it.
                # Re-seeding reported success and changed nothing, and the
                # only way out was editing the database by hand.
                #
                # Records the PLAN rate, because seeding records what the
                # ROUTER currently has and the router has whatever it was
                # handed at login. Recording the effective rate instead
                # writes down a boost as though it had already been
                # delivered, and it never gets pushed.
                up, down = plan_speeds(service)
                Service.objects.filter(pk=service.pk).update(last_pushed_rate_limit=f"{up}k/{down}k")
                seeded += 1
                continue

            target = effective_speeds(service, now)
            if service.last_pushed_rate_limit == target.rate_limit:
                skipped += 1
                continue

            # Only lines that are actually up can be changed in place. For
            # the rest the new value is already in radreply and applies at
            # their next connection, so there is nothing to do and nothing
            # wrong -- counted separately so the two are never confused.
            if find_live_session(service.radius_username) is None:
                offline += 1
                continue

            if len(changed) >= limit:
                deferred += 1
                continue

            self.stdout.write(
                f"  {service.radius_username}: {service.last_pushed_rate_limit or '(unknown)'} "
                f"-> {target.rate_limit}  [{describe(target)}]"
            )
            if commit:
                with acting_as_system("apply_speed_policies (scheduled job)"):
                    # Never drops the session -- see this module's docstring.
                    ok = apply_change(service, reason="tariff", allow_disconnect_fallback=False)
                if ok:
                    changed.append(service.radius_username)
                    if getattr(service.device, "enable_shaper", False):
                        shaper_devices.add(service.device_id)
                else:
                    failed += 1
            else:
                changed.append(service.radius_username)

        # There are TWO queues on a Mikrotik. Mikrotik-Rate-Limit from the
        # CoA above updates the DYNAMIC queue for the PPPoE session. On a
        # device with the shaper on, this platform also maintains a STATIC
        # simple queue per service -- and the customer gets the more
        # restrictive of the two. So a CoA alone leaves the old static
        # queue holding the line down at its plan speed, the boost is
        # delivered and accepted, and the customer feels nothing.
        for device_id in shaper_devices:
            from network.models import Device
            from network.router_sync import sync_device_shaper_queues

            device = Device.objects.filter(pk=device_id).first()
            if device is None:
                continue
            try:
                sync_device_shaper_queues(device)
                self.stdout.write(f"  re-synced shaper queues on {device.name}")
            except Exception as exc:                                  # noqa: BLE001
                # Reported, never fatal: the CoA has already landed, and
                # the rest of the run should still finish.
                self.stdout.write(self.style.WARNING(
                    f"  couldn't re-sync shaper queues on {device.name}: {exc}"
                ))

        self.stdout.write("")
        if seed:
            self.stdout.write(
                self.style.SUCCESS(f"Seeded {seeded} line(s); {skipped} already recorded. No router was contacted.")
            )
            return

        self.stdout.write(
            f"{len(changed)} changed · {skipped} already correct · {offline} not connected · {failed} failed"
        )
        if deferred:
            self.stdout.write(
                self.style.WARNING(
                    f"{deferred} more were due but held back by --limit {limit}. If that number is large, "
                    "check your windows before letting it run again — and consider --seed."
                )
            )
        if not commit and changed:
            self.stdout.write("Dry run — nothing was pushed. Re-run with --commit.")
