"""Report how far each customer's stored balance is from what they owe.

READ-ONLY. This command writes nothing, ever. It exists because the
ledger drifted for a long time before the cause was found, so the size of
the damage has to be measurable before anybody decides what to do about
it.

The drift: Customer.balance was only ever increased by the
recurring-billing engine (recurring._generate_document), but decreased by
EVERY payment and approved credit. So for any invoice raised by hand --
the "+ New invoice" button, which is what the shipped UI uses -- the
customer paid it and their balance went down with nothing having put it
up. The visible symptoms are a customer showing credit they never had,
a statement PDF that disagrees with their invoices, and
blocking_candidate_services' `balance <= minimum_balance` test quietly
exempting them from suspension forever.

Deleted invoices are the other half. Deleting an invoice left its debit
on the balance with nothing to explain it, which drifts the other way --
the customer appears to owe money no invoice claims.

    manage.py balance_drift                  # summary + the 40 worst
    manage.py balance_drift --all            # every customer with drift
    manage.py balance_drift --csv drift.csv  # for a spreadsheet

What "expected" means here: the sum of every issued, non-cancelled
invoice (Invoice.DEBITED_STATUSES), minus every payment, minus every
approved credit request. That is the same arithmetic
Invoice.apply_balance_debit and Payment.reverse_ledger_effect now
maintain going forward, so a customer at zero drift is one whose stored
balance already agrees with the fixed code.
"""
import csv
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import DecimalField, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce

from billing.models import CreditRequest, Invoice, Payment
from customers.models import Customer

_ZERO = Decimal("0.00")
_MONEY = DecimalField(max_digits=14, decimal_places=2)


def _total_per_customer(model, customer_field="customer", value_field="amount", condition=None):
    """A correlated subquery summing one relation per customer.

    Subqueries rather than three Sum()s in one annotate(). Three
    aggregates over three multi-valued relations in a single annotate()
    makes Django emit one query with three LEFT JOINs, and every sum is
    then multiplied by the row counts of the other two -- the standard
    join fan-out. The first version of this command did exactly that, so
    a customer with 2 invoices and 2 payments reported twice the real
    invoiced and paid figures. Because the invoice side inflates faster
    than the payment side, it could also flip the SIGN of the drift --
    in a report whose closing line tells you a negative drift is the
    ledger bug's signature. It was inventing the evidence.

    Each subquery aggregates independently, so nothing is multiplied.
    """
    rows = model.objects.filter(**{customer_field: OuterRef("pk")})
    if condition is not None:
        rows = rows.filter(condition)
    return Coalesce(
        Subquery(
            rows.values(customer_field)
            .annotate(_t=Sum(value_field))
            .values("_t")[:1],
            output_field=_MONEY,
        ),
        _ZERO,
        output_field=_MONEY,
    )


class Command(BaseCommand):
    help = "Read-only: report customers whose stored balance disagrees with their invoices and payments."

    def add_arguments(self, parser):
        parser.add_argument(
            "--all", action="store_true",
            help="List every customer with drift, not just the worst 40.",
        )
        parser.add_argument(
            "--csv", dest="csv_path", default=None,
            help="Also write the full list to this CSV path.",
        )
        parser.add_argument(
            "--threshold", type=Decimal, default=Decimal("0.01"),
            help="Ignore drift smaller than this (default 0.01, i.e. report any cent).",
        )

    def handle(self, *args, **options):
        rows = []
        customers = Customer.objects.annotate(
            invoiced=_total_per_customer(
                Invoice, value_field="total",
                condition=Q(status__in=Invoice.DEBITED_STATUSES),
            ),
            paid=_total_per_customer(Payment),
            credited=_total_per_customer(
                CreditRequest, condition=Q(status=CreditRequest.Status.APPROVED),
            ),
        ).only("id", "full_name", "balance", "status")

        for customer in customers.iterator():
            expected = customer.invoiced - customer.paid - customer.credited
            drift = customer.balance - expected
            if abs(drift) >= options["threshold"]:
                rows.append((customer, expected, drift))

        rows.sort(key=lambda r: abs(r[2]), reverse=True)

        total_customers = Customer.objects.count()
        overstated = [r for r in rows if r[2] > 0]
        understated = [r for r in rows if r[2] < 0]

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Balance drift"))
        self.stdout.write(f"  Customers checked                {total_customers}")
        self.stdout.write(f"  Balances that disagree           {len(rows)}")
        self.stdout.write(
            f"  Owing MORE than their invoices   {len(overstated)}   "
            f"(total R {sum((r[2] for r in overstated), _ZERO):,.2f})"
        )
        self.stdout.write(
            f"  Showing credit they never had    {len(understated)}   "
            f"(total R {sum((-r[2] for r in understated), _ZERO):,.2f})"
        )
        self.stdout.write("")

        if not rows:
            self.stdout.write(self.style.SUCCESS("  Nothing to repair -- every balance agrees."))
            return

        shown = rows if options["all"] else rows[:40]
        header = f"  {'id':>6}  {'customer':<28}  {'stored':>12}  {'expected':>12}  {'drift':>12}"
        self.stdout.write(header)
        self.stdout.write("  " + "-" * (len(header) - 2))
        for customer, expected, drift in shown:
            name = (customer.full_name or "")[:28]
            line = (
                f"  {customer.id:>6}  {name:<28}  {customer.balance:>12,.2f}  "
                f"{expected:>12,.2f}  {drift:>12,.2f}"
            )
            self.stdout.write(self.style.WARNING(line) if drift < 0 else line)

        if not options["all"] and len(rows) > len(shown):
            self.stdout.write(f"  ... and {len(rows) - len(shown)} more (--all to list them)")

        if options["csv_path"]:
            with open(options["csv_path"], "w", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow([
                    "customer_id", "full_name", "status", "stored_balance",
                    "expected_balance", "drift", "invoiced", "paid", "credited",
                ])
                for customer, expected, drift in rows:
                    writer.writerow([
                        customer.id, customer.full_name, customer.status,
                        f"{customer.balance:.2f}", f"{expected:.2f}", f"{drift:.2f}",
                        f"{customer.invoiced:.2f}", f"{customer.paid:.2f}",
                        f"{customer.credited:.2f}",
                    ])
            self.stdout.write("")
            self.stdout.write(self.style.SUCCESS(f"  Full list written to {options['csv_path']}"))

        self.stdout.write("")
        self.stdout.write(
            "  Nothing has been changed. A negative drift is the ledger bug's own signature: "
            "a payment credited against an invoice that never debited."
        )
