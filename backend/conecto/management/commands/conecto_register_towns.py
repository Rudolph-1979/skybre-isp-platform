"""Tell Conecto about our towns.

This has to run before anything else is useful. Conecto matches a
client's city against its registered towns EXACTLY and case-sensitively,
and when it doesn't match it stores the client with no town and answers
200 -- so an unregistered town produces no error anywhere, just a
subscriber who never receives an outage alert.

The towns come from the customers themselves (Customer.city), because
those are the strings that will actually be sent, and registering
anything else would guarantee the mismatch this exists to prevent.

Usage:
    python manage.py conecto_register_towns             # report only
    python manage.py conecto_register_towns --apply
    python manage.py conecto_register_towns --apply --province "Mpumalanga"
"""
from django.core.management.base import BaseCommand
from django.db.models import Count

from conecto import client
from customers.models import Customer


class Command(BaseCommand):
    help = "Register every town our customers live in with Conecto."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--province", default="",
                            help="Province to register the towns under. Optional.")
        parser.add_argument("--min-customers", type=int, default=1, metavar="N",
                            help="Skip towns with fewer than N customers. Default 1.")

    def handle(self, *args, **options):
        towns = (
            Customer.objects.exclude(city="")
            .values("city")
            .annotate(customers=Count("pk"))
            .order_by("city")
        )
        towns = [t for t in towns if t["customers"] >= options["min_customers"]]

        if not towns:
            self.stdout.write("No customers have a city set, so there is nothing to register.")
            return

        self.stdout.write(f"{len(towns)} town(s) from customer records:")
        width = max(len(t["city"]) for t in towns)
        for town in towns:
            self.stdout.write(f"  {town['city']:<{width}}  {town['customers']} customer(s)")

        if not options["apply"]:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(
                "Dry run -- nothing sent. Check the spellings above first: Conecto matches "
                "them character for character, so \"Nelspruit \" and \"nelspruit\" are two "
                "different towns to it."
            ))
            return

        sent, failed = 0, 0
        for town in towns:
            if client.register_town(town["city"], province=options["province"]) is None:
                failed += 1
            else:
                sent += 1

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Registered {sent}."))
        if failed:
            self.stdout.write(self.style.WARNING(
                f"{failed} failed -- see the Conecto calls table, then `conecto_resync --apply`."
            ))
