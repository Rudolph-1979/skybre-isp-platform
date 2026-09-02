"""One-time cleanup for serials and MACs captured before they were normalised.

Every unit checked in from now on is stored canonically -- serial upper-cased,
MAC as AA:BB:CC:DD:EE:FF -- because the API normalises on the way in. Units
already on the shelf were stored exactly as typed, which means:

  * a MAC written 'aa-bb-cc-dd-ee-ff' will not be found by someone searching
    'AA:BB:CC:DD:EE:FF', and
  * two units that are really the same MAC in two spellings look like two
    different MACs, so the duplicate check can't see the collision.

Run with --dry-run first. It reports what it would change and, importantly,
any collisions the rewrite would create -- two units whose serials or MACs
only differ by case or punctuation. Those need a human decision (which one is
the typo), so the command refuses to touch them and lists them instead.
"""

from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from inventory.identifiers import InvalidMac, normalise_mac, normalise_serial
from inventory.models import SerializedUnit


class Command(BaseCommand):
    help = "Rewrites existing SerializedUnit serials/MACs into canonical form."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing anything.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        units = list(SerializedUnit.objects.all().order_by("pk"))
        if not units:
            self.stdout.write("No serialized units on file. Nothing to do.")
            return

        planned = []
        unreadable = []
        for unit in units:
            serial = normalise_serial(unit.serial_number)
            try:
                mac = normalise_mac(unit.mac_address)
            except InvalidMac:
                # Deliberately left alone rather than blanked: it is somebody's
                # note-to-self in the wrong field, and deleting it loses
                # information. Reported so it can be fixed by hand.
                unreadable.append(unit)
                mac = unit.mac_address
            if serial != unit.serial_number or mac != unit.mac_address:
                planned.append((unit, serial, mac))

        # Collisions are checked against the post-rewrite values of ALL units,
        # not just the ones being changed -- a unit already stored canonically
        # can be the thing a rewrite collides with.
        final_serials = defaultdict(list)
        final_macs = defaultdict(list)
        changes = {unit.pk: (serial, mac) for unit, serial, mac in planned}
        for unit in units:
            serial, mac = changes.get(unit.pk, (unit.serial_number, unit.mac_address))
            final_serials[serial].append(unit)
            if mac:
                final_macs[mac].append(unit)

        collisions = [
            (label, value, group)
            for label, mapping in (("Serial", final_serials), ("MAC", final_macs))
            for value, group in mapping.items()
            if len(group) > 1
        ]

        for unit, serial, mac in planned:
            bits = []
            if serial != unit.serial_number:
                bits.append(f"serial {unit.serial_number!r} -> {serial!r}")
            if mac != unit.mac_address:
                bits.append(f"MAC {unit.mac_address!r} -> {mac!r}")
            self.stdout.write(f"  #{unit.pk} {unit.product.name}: {'; '.join(bits)}")

        if unreadable:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(f"{len(unreadable)} unit(s) have a MAC that isn't a MAC — left as-is:"))
            for unit in unreadable:
                self.stdout.write(f"  #{unit.pk} {unit.serial_number}: {unit.mac_address!r}")

        if collisions:
            self.stdout.write("")
            self.stdout.write(self.style.ERROR("Refusing to write — these would collide:"))
            for label, value, group in collisions:
                ids = ", ".join(f"#{u.pk} ({u.serial_number})" for u in group)
                self.stdout.write(f"  {label} {value}: {ids}")
            self.stdout.write("")
            self.stdout.write("Decide which of each pair is the typo, fix it under Inventory -> Units, then re-run.")
            return

        if not planned:
            self.stdout.write(self.style.SUCCESS("Every unit is already in canonical form. Nothing to do."))
            return

        if dry_run:
            self.stdout.write("")
            self.stdout.write(self.style.SUCCESS(f"Dry run — {len(planned)} unit(s) would change. Re-run without --dry-run to apply."))
            return

        with transaction.atomic():
            for unit, serial, mac in planned:
                unit.serial_number = serial
                unit.mac_address = mac
                unit.save(update_fields=["serial_number", "mac_address"])
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Updated {len(planned)} unit(s)."))
