"""Walk the whole enforcement chain and say which link is broken.

Written because the answer to "why didn't that customer's speed change?" took
days to establish, and every step of it was individually checkable. Each link
below can fail on its own, silently, and each has a different fix:

    service -> RADIUS rows -> NAS record -> live session -> CoA -> the router

Read-only by default. --coa actually sends a packet, which is the only way to
prove UDP 3799 is open and the shared secret matches, so it is opt-in.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from billing.models import Service
from radiusauth import dynauth, enforcement
from radiusauth.models import RadCheck, RadReply, RadiusAction, RadiusNasClient
from radiusauth.signals import _mikrotik_rate_limit


class Command(BaseCommand):
    help = "Diagnose why RADIUS changes aren't reaching customers."

    def add_arguments(self, parser):
        parser.add_argument("--service", type=int, help="Check one service by id instead of all of them.")
        parser.add_argument(
            "--coa",
            action="store_true",
            help="Actually send a CoA to each live session, re-applying its CURRENT speed. "
                 "Harmless -- it sets what is already set -- but it proves the path end to end.",
        )

    def ok(self, text):
        self.stdout.write(self.style.SUCCESS(f"    OK    {text}"))

    def bad(self, text):
        self.stdout.write(self.style.ERROR(f"    FAIL  {text}"))

    def warn(self, text):
        self.stdout.write(self.style.WARNING(f"    WARN  {text}"))

    def note(self, text):
        self.stdout.write(f"          {text}")

    def handle(self, *args, **options):
        problems = 0

        # --- NAS records --------------------------------------------------
        self.stdout.write(self.style.MIGRATE_HEADING("RADIUS clients (NAS records)"))
        clients = RadiusNasClient.objects.filter(is_active=True)
        if not clients:
            self.bad("No active NAS records. CoA can't be signed without a shared secret.")
            problems += 1
        for client in clients:
            # The secret itself is never printed -- it is the credential that
            # protects every customer's session from being disconnected by
            # anyone who can reach the router.
            self.ok(f"{client.name} at {client.ip_address} (secret set: {bool(client.secret)})")

        services = Service.objects.select_related("customer", "tariff", "device")
        if options["service"]:
            services = services.filter(pk=options["service"])
        else:
            services = services.exclude(status=Service.Status.TERMINATED)

        for service in services:
            self.stdout.write("")
            self.stdout.write(
                self.style.MIGRATE_HEADING(
                    f"Service #{service.pk} — {service.customer.full_name} "
                    f"({service.radius_username or 'no radius username'}, {service.status})"
                )
            )
            problems += self._check_service(service, send_coa=options["coa"])

        self.stdout.write("")
        if problems:
            self.stdout.write(self.style.WARNING(f"{problems} problem(s) found."))
        else:
            self.stdout.write(self.style.SUCCESS("Every link in the chain is intact."))

    def _check_service(self, service, send_coa):
        problems = 0
        username = (service.radius_username or "").strip()
        if not username:
            self.warn("No RADIUS username, so this service never authenticates. Nothing else to check.")
            return 0

        # --- the rows FreeRADIUS will answer with -------------------------
        checks = {r.attribute: r.value for r in RadCheck.objects.filter(username=username)}
        replies = {r.attribute: r.value for r in RadReply.objects.filter(username=username)}

        if "Auth-Type" in checks:
            self.ok("radcheck says Reject — this service is denied at login, as intended for a suspension.")
        elif "Cleartext-Password" in checks:
            self.ok("radcheck has a password.")
        else:
            self.bad("radcheck has neither a password nor a Reject. This service can't authenticate at all.")
            problems += 1

        # Checked BEFORE the rate limit is compared, because the comparison
        # below can't catch this on its own: both sides of it are built by
        # _mikrotik_rate_limit, so a tariff with no speed produces the same
        # fallback on both sides and reports a happy "matches the tariff"
        # while the customer is on a speed nobody chose for them.
        tariff = service.tariff
        if not tariff.speed_download_kbps or not tariff.speed_upload_kbps:
            self.bad(
                f"Tariff '{tariff.name}' has no speed set "
                f"(down={tariff.speed_download_kbps}, up={tariff.speed_upload_kbps}). "
                "The rate limit below is a FALLBACK of 10 Mbps, not what this customer pays for. "
                "Set the speeds under Billing -> Tariffs and re-save."
            )
            problems += 1

        rate = replies.get("Mikrotik-Rate-Limit")
        if rate:
            expected = _mikrotik_rate_limit(service.tariff)
            if rate == expected:
                self.ok(f"Rate limit {rate} matches the tariff.")
            else:
                self.bad(f"Rate limit is {rate} but the tariff says {expected}. Re-save the service.")
                problems += 1
            if rate.rstrip().endswith("M") or "M/" in rate:
                self.bad(f"Rate limit {rate} is in the old M format — that's no throttle at all. Run resync_radius.")
                problems += 1
        elif "Auth-Type" not in checks:
            self.warn("No Mikrotik-Rate-Limit row, so the router applies its own default, not the tariff.")

        # --- the live session ---------------------------------------------
        session = enforcement.find_live_session(username)
        if session is None:
            self.note("No live session right now — nothing to push to. Changes apply at their next connection.")
            return problems

        age = timezone.now() - (session.acctupdatetime or session.acctstarttime)
        self.ok(
            f"Live session {session.acctsessionid} on NAS {session.nasipaddress}, "
            f"address {session.framedipaddress}, last heard from {int(age.total_seconds())}s ago."
        )

        secret = enforcement._secret_for(session.nasipaddress)
        if not secret:
            self.bad(
                f"No active NAS record for {session.nasipaddress}, so CoA can't be signed. "
                "Add it under Networking -> RADIUS clients; the IP must be the one above."
            )
            problems += 1
        else:
            self.ok(f"NAS record found for {session.nasipaddress}.")

        # --- the router's own IP vs the one accounting reports -------------
        if service.device and service.device.ip_address != session.nasipaddress:
            self.warn(
                f"The Device record says {service.device.ip_address} but accounting says "
                f"{session.nasipaddress}. CoA follows accounting, which is right; the Device "
                "record being wrong will break the RouterOS API fallback."
            )

        # --- the actual round trip ----------------------------------------
        if send_coa and secret:
            current = _mikrotik_rate_limit(service.tariff)
            try:
                dynauth.change_rate_limit(
                    session.nasipaddress, secret, username, current,
                    acct_session_id=session.acctsessionid,
                    framed_ip=session.framedipaddress,
                    port=dynauth.coa_port(),
                )
                self.ok(f"CoA round trip succeeded — the router accepted {current}.")
            except dynauth.DynAuthError as exc:
                self.bad(f"CoA failed: {exc}")
                problems += 1
        elif send_coa:
            self.note("Skipped the CoA test — no secret to sign with.")

        # --- what happened last time --------------------------------------
        last = RadiusAction.objects.filter(username=username).first()
        if last:
            line = f"Last attempt {last.created_at:%Y-%m-%d %H:%M}: {last.action} via {last.transport} — {last.detail}"
            (self.ok if last.ok else self.bad)(line)
            if not last.ok:
                problems += 1

        return problems
