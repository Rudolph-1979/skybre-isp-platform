"""Find and free IP Pool addresses that are assigned to a service which
can no longer use them.

An address is "stranded" when its pool's category doesn't match what the
service's current configuration would ever ask for:

  * a Net Pool address held by a service that isn't OVPN
  * a Customer Pool address held by a service that isn't PPPoE in
    pool/auto mode -- EXCEPT a suspended PPPoE service, which deliberately
    keeps its customer address while sitting on the walled garden so it
    comes straight back on reactivation (see
    radiusauth.signals._allocate_walled_garden_ip)
  * a Walled Garden address held by a service that isn't a suspended PPPoE
    service

Stranding was possible because the release logic was asymmetric: switching
a service away from PPPoE freed its customer address, but switching away
from OVPN never freed its Net Pool address. Fixed in signals.py
(release_network_ip); this command cleans up anything stranded before that
fix, and doubles as an audit afterwards.

Dry run by default -- it prints what it would free and changes nothing.
Pass --apply to actually free them.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from billing.models import Service
from network.models import IPAddress, IPPool


def _permitted_categories(service):
    """The pool categories this service's current config can legitimately
    hold an address from."""
    if service.radius_connection_type == Service.ConnectionType.OVPN:
        return {IPPool.Category.NETWORK}

    if service.radius_connection_type == Service.ConnectionType.PPPOE:
        allowed = set()
        if service.ip_assignment_mode in (
            Service.IPAssignmentMode.POOL,
            Service.IPAssignmentMode.AUTO,
        ):
            allowed.add(IPPool.Category.CUSTOMER)
        if service.status == Service.Status.SUSPENDED:
            # A suspended PPPoE service sits on the walled garden AND keeps
            # its customer address, on purpose, so reactivation is instant.
            allowed.add(IPPool.Category.WALLED_GARDEN)
            allowed.add(IPPool.Category.CUSTOMER)
        return allowed

    return set()


class Command(BaseCommand):
    help = "Free IP Pool addresses assigned to services that can no longer use them."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually free the stranded addresses. Without this, only reports.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]

        held = (
            IPAddress.objects.exclude(assigned_service=None)
            .select_related("pool", "assigned_service")
            .order_by("address")
        )

        stranded = []
        for addr in held:
            service = addr.assigned_service
            if addr.pool.category not in _permitted_categories(service):
                stranded.append(addr)

        if not stranded:
            self.stdout.write(self.style.SUCCESS("No stranded addresses. Nothing to do."))
            return

        self.stdout.write(f"{len(stranded)} stranded address(es):")
        for addr in stranded:
            s = addr.assigned_service
            self.stdout.write(
                f"  {addr.address:<18} pool={addr.pool.name!r} ({addr.pool.category})"
                f"  service={s.pk} {s.radius_username!r}"
                f" type={s.radius_connection_type} mode={s.ip_assignment_mode} status={s.status}"
            )

        if not apply_changes:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING("Dry run -- nothing changed. Re-run with --apply to free these.")
            )
            return

        with transaction.atomic():
            freed = IPAddress.objects.filter(pk__in=[a.pk for a in stranded]).update(
                status=IPAddress.Status.FREE, assigned_service=None
            )
        self.stdout.write(self.style.SUCCESS(f"Freed {freed} address(es)."))
