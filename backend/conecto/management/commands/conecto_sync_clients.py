"""Push existing customers' client codes to Conecto.

The signal only fires on customers saved from now on. Every customer
already on the platform predates it, and Conecto REFUSES to register a
subscriber whose client code it has never heard of -- so without this
backfill the app works for new customers and silently rejects everyone
who was here first, which is almost all of them.

Run the towns command before this one: a client code pushed while its
town is unknown is stored with no town, and Conecto answers 200 either
way, so nothing tells you afterwards.

Usage:
    python manage.py conecto_sync_clients                  # report only
    python manage.py conecto_sync_clients --apply
    python manage.py conecto_sync_clients --apply --status active
"""
from django.core.management.base import BaseCommand

from conecto import client
from customers.models import Customer


class Command(BaseCommand):
    help = "Register every existing customer's client code with Conecto."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--status", default="", metavar="STATUS",
                            help='Only customers with this status, e.g. "active".')
        parser.add_argument("--limit", type=int, default=0,
                            help="Stop after N customers. 0 = all.")

    def handle(self, *args, **options):
        customers = Customer.objects.exclude(customer_id="").order_by("customer_id")
        if options["status"]:
            customers = customers.filter(status=options["status"])
        if options["limit"]:
            customers = customers[: options["limit"]]

        total = customers.count() if not options["limit"] else len(customers)
        self.stdout.write(f"{total} customer(s) to register.")
        missing_city = sum(1 for c in customers if not c.city)
        if missing_city:
            self.stdout.write(self.style.WARNING(
                f"  {missing_city} have no city -- they will be registered with no town and "
                "will not receive outage alerts until one is set."
            ))

        if not options["apply"]:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING("Dry run -- nothing sent."))
            return

        sent, failed = 0, 0
        for customer in customers.iterator():
            if client.register_client_code(customer) is None:
                failed += 1
            else:
                sent += 1

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Registered {sent}."))
        if failed:
            self.stdout.write(self.style.WARNING(
                f"{failed} failed -- `conecto_resync --apply` will retry them."
            ))
