"""Regenerates every service's RADIUS rows from the current models.

Needed after any change to how those rows are DERIVED, as opposed to a change
in the data behind them. A service's rows are only rewritten when the service
itself is saved, so a fix to the derivation logic leaves every untouched
service carrying the old values indefinitely.

The case this was written for: tariff speeds moved from being read as Mbps to
Kbps, which changed Mikrotik-Rate-Limit from "4096M/4096M" (four terabits --
no throttle at all) to "4096k/4096k". Existing rows kept the old value, so
those customers stayed unthrottled until something happened to re-save them.
An `M` suffix in the report below means exactly that.

Reports before/after per service and touches nothing without --commit.
"""

from django.core.management.base import BaseCommand

from billing.models import Service
from radiusauth.models import RadiusAction, RadReply
from radiusauth.signals import sync_service_radius


class Command(BaseCommand):
    help = "Rewrites every RADIUS-enabled service's radcheck/radreply rows from current data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--commit", action="store_true",
            help="Actually rewrite. Without this, only reports what would change.",
        )
        parser.add_argument(
            "--kick", action="store_true",
            help=(
                "Also push the new rate limit to each active service's LIVE session. Normally a "
                "CoA, which applies it without disconnecting anyone; only falls back to dropping "
                "the session if CoA can't reach the router."
            ),
        )

    def _rate_limit(self, username):
        row = RadReply.objects.filter(username=username, attribute="Mikrotik-Rate-Limit").first()
        return row.value if row else None

    def handle(self, *args, **options):
        commit = options["commit"]
        kick = options["kick"]
        services = (
            Service.objects.exclude(radius_username="")
            .exclude(radius_username__isnull=True)
            .select_related("customer", "tariff", "device")
            .order_by("customer__full_name")
        )
        if not services:
            self.stdout.write("No RADIUS-enabled services.")
            return

        stale = 0
        for service in services:
            before = self._rate_limit(service.radius_username)
            # An "M" suffix is the old, wrong format -- flagged because it
            # means that customer currently has no effective speed limit.
            suspicious = bool(before and before.rstrip().endswith("M"))
            if suspicious:
                stale += 1

            if commit:
                sync_service_radius(service)
                after = self._rate_limit(service.radius_username)
            else:
                after = "(dry run)"

            flag = "  <-- unthrottled, old format" if suspicious else ""
            self.stdout.write(
                f"  {service.customer.full_name} / {service.radius_username} "
                f"[{service.status}]: {before} -> {after}{flag}"
            )

            if commit and kick and service.status == Service.Status.ACTIVE:
                # Goes through the same enforcement path a save does, rather
                # than reaching for the router API directly. Two reasons: a
                # rate change is a CoA, so nobody gets dropped for it -- this
                # command used to disconnect a customer to give them a speed
                # they could have had without noticing -- and the attempt is
                # recorded, so a failure here shows up on the service like any
                # other instead of only in this terminal.
                from radiusauth.enforcement import apply_change

                apply_change(service, "tariff")
                last = RadiusAction.objects.filter(username=service.radius_username).first()
                if last:
                    marker = "      " if last.ok else "      !! "
                    self.stdout.write(f"{marker}{last.detail}")

        self.stdout.write("")
        if stale:
            self.stdout.write(
                self.style.WARNING(
                    f"{stale} service(s) had the old 'M' rate-limit format — those customers have "
                    "no effective speed limit until their rows are rewritten."
                )
            )
        if not commit:
            self.stdout.write("Dry run. Re-run with --commit to write, and add --kick to apply the new limits now.")
        else:
            self.stdout.write(self.style.SUCCESS(f"Rewrote {len(services)} service(s)."))
            if not kick:
                self.stdout.write(
                    "Live sessions keep their OLD rate limit until they reconnect — re-run with --kick "
                    "to push it to them now (a CoA; it does not disconnect anyone)."
                )
