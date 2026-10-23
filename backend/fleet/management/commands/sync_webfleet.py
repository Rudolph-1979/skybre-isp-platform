"""Read Webfleet into Vexar (#186): link vehicles, positions + odometer,
trips (into the vehicle's Trips list) and (hourly) OptiDrive. Read-only
towards Webfleet.

Cron on the host, every 15 minutes (Webfleet asks trips to be read no more
often than that):

    */15 * * * * cd ~/isp-platform && docker compose exec -T backend python manage.py sync_webfleet
"""
from django.core.management.base import BaseCommand

from fleet import webfleet


class Command(BaseCommand):
    help = "Sync vehicles, odometer, trips and OptiDrive from Webfleet."

    def add_arguments(self, parser):
        parser.add_argument("--kpis", action="store_true", help="Refresh OptiDrive now, not only hourly.")
        parser.add_argument("--backfill-odometer", action="store_true",
                            help="Once: add km readings for Webfleet trips synced before #189, then exit.")
        parser.add_argument("--recheck-trips", action="store_true",
                            help="Re-read the last 2 weeks of trips and correct the km readings (#190), then exit.")

    def handle(self, *args, **opts):
        if not webfleet.configured():
            self.stdout.write("Webfleet isn't set up (WEBFLEET_* in backend/.env) -- nothing to do.")
            return
        if opts["backfill_odometer"]:
            self.stdout.write(f"{webfleet.backfill_trip_odometers()} odometer reading(s) added from earlier Webfleet trips.")
            return
        if opts["recheck_trips"]:
            try:
                self.stdout.write(webfleet.recheck_trips())
            except webfleet.WebfleetError as exc:
                self.stderr.write(str(exc))
            return
        ok, message = webfleet.sync(force_kpis=opts["kpis"])
        (self.stdout.write if ok else self.stderr.write)(message)
