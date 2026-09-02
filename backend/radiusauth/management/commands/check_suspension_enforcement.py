"""Is a suspension actually cutting anyone off?

Three separate things have to be true for "suspended" to mean no internet,
and each of them used to be able to fail silently:

  1. The customer's SERVICES have to be suspended, not just the customer.
     Nothing in this platform enforces anything off Customer.status -- every
     mechanism keys off Service.status.
  2. The suspended service's RADIUS rows have to actually deny it. A suspended
     PPPoE service is allowed to authenticate ON PURPOSE, so it can land on a
     walled-garden address; but with no walled-garden address to give it, it
     used to authenticate with no Framed-IP-Address at all and the router
     would hand it a normal one. Full internet.
  3. The LIVE session has to be dropped. RADIUS is only consulted at login,
     so an established PPPoE session is never re-checked against anything.

This reports on all three, and with --fix re-saves the affected services so
the current (correct) logic runs over them: RADIUS rows rewritten, router
block list updated, live session dropped.

Report first. --fix will disconnect people.
"""

from django.core.management.base import BaseCommand

from billing.models import Service
from customers.models import Customer
from network.models import Device, IPAddress, IPPool
from radiusauth.models import RadCheck, RadReply


class Command(BaseCommand):
    help = "Reports (and optionally repairs) customers who are suspended but still able to get online."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fix",
            action="store_true",
            help="Re-save the affected services, which rewrites RADIUS, updates the router and drops live sessions.",
        )

    def handle(self, *args, **options):
        fix = options["fix"]
        problems = 0

        # --- 1. walled-garden capacity -------------------------------------
        self.stdout.write(self.style.MIGRATE_HEADING("Walled Garden pools"))
        pools = IPPool.objects.filter(category=IPPool.Category.WALLED_GARDEN)
        if not pools.exists():
            self.stdout.write(
                "  none configured — a suspended PPPoE customer is REJECTED outright.\n"
                "  That does cut them off. Add a Walled Garden pool under Networking → IP Pools\n"
                "  if you'd rather they landed on a 'please pay' page than just failed to connect."
            )
        else:
            for pool in pools:
                free = IPAddress.objects.filter(pool=pool, status=IPAddress.Status.FREE).count()
                total = IPAddress.objects.filter(pool=pool).count()
                note = "" if free else "  <-- exhausted: further suspensions get rejected instead"
                self.stdout.write(f"  {pool.name} ({pool.network_cidr}): {free} free of {total}{note}")

        # --- 2. router enforcement ----------------------------------------
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Routers"))
        devices = Device.objects.all()
        if not devices:
            self.stdout.write("  no devices on file.")
        for device in devices:
            bits = []
            bits.append("API on" if device.api_enabled else "API OFF — sessions can't be dropped")
            bits.append(
                "block-list on" if device.block_disabled_customers
                else "block-list off — no firewall backstop"
            )
            self.stdout.write(f"  {device.name} ({device.ip_address}): {', '.join(bits)}")

        # --- 3. customers suspended but with live services -----------------
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Suspended customers with services still active"))
        stale = (
            Service.objects.select_related("customer", "device")
            .filter(customer__status=Customer.Status.SUSPENDED, status=Service.Status.ACTIVE)
            .order_by("customer__full_name")
        )
        if not stale:
            self.stdout.write("  none.")
        for service in stale:
            problems += 1
            self.stdout.write(
                f"  {service.customer.full_name} ({service.customer.customer_id}) — "
                f"service #{service.pk} '{service.radius_username or 'no radius user'}' is still ACTIVE"
            )

        if stale:
            # Worth spelling out, because this contradiction has two honest
            # resolutions and --fix only performs one of them. Restoring a
            # service by hand now lifts the customer with it, so the usual
            # reason to find rows here is that they predate that change -- and
            # for those, cutting the customer off is the wrong half to keep.
            self.stdout.write(
                "\n  --fix resolves these DOWNWARD: it suspends the service and disconnects them.\n"
                "  If the line was restored deliberately and it is the customer record that is stale,\n"
                "  set the customer to Active on their own page instead and leave --fix alone."
            )

        if stale and fix:
            for service in stale:
                service.status = Service.Status.SUSPENDED
                service.auto_suspended_with_customer = True
                service._customer_cascade = True
                service.save()
            self.stdout.write(self.style.SUCCESS(f"  fixed: suspended {len(stale)} service(s)."))

        # --- 4. suspended services whose RADIUS rows still let them on -----
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Suspended services whose RADIUS rows still let them online"))
        # Judged on the rows FreeRADIUS will actually answer with, not on
        # whether a walled-garden address happens to be held -- a suspended
        # service with no walled-garden address is perfectly safe if it is
        # being rejected, which is now what happens.
        #
        # The dangerous shape is precisely: a usable password AND no
        # Framed-IP-Address AND no reject. That authenticates, and the router
        # then hands out an address from its own pool. Full internet.
        exposed = []
        for service in Service.objects.select_related("customer", "device").exclude(
            status=Service.Status.ACTIVE
        ):
            username = (service.radius_username or "").strip()
            if not username:
                continue
            checks = set(RadCheck.objects.filter(username=username).values_list("attribute", flat=True))
            if "Auth-Type" in checks:
                continue                      # rejected — fine
            if "Cleartext-Password" not in checks:
                continue                      # can't authenticate at all — fine
            has_ip = RadReply.objects.filter(
                username=username, attribute="Framed-IP-Address"
            ).exists()
            if not has_ip:
                exposed.append(service)
        if not exposed:
            self.stdout.write("  none.")
        for service in exposed:
            problems += 1
            self.stdout.write(
                f"  {service.customer.full_name} — service #{service.pk} "
                f"'{service.radius_username}' ({service.status}): password accepted, no address pinned"
            )

        if exposed and fix:
            for service in exposed:
                # A plain re-save is all it takes: sync_service_radius runs off
                # the post_save signal and now fails closed, so each of these
                # ends up either on a walled-garden address or rejected.
                service.save()
            self.stdout.write(self.style.SUCCESS(f"  fixed: re-synced {len(exposed)} service(s)."))

        self.stdout.write("")
        if not problems:
            self.stdout.write(self.style.SUCCESS("Nothing to fix — every suspension is being enforced."))
        elif fix:
            self.stdout.write(self.style.SUCCESS(f"Repaired {problems} problem(s)."))
        else:
            self.stdout.write(
                self.style.WARNING(f"{problems} problem(s) found. Re-run with --fix to repair them.")
            )
            self.stdout.write("--fix will disconnect people who are currently online.")
