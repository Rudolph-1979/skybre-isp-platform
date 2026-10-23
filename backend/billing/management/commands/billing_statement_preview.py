"""Print what a customer's statement would say. Read-only.

    python manage.py billing_statement_preview --client-code SMF463
    python manage.py billing_statement_preview --client-code SMF463 --period 2026-08

With no --period this prints EXACTLY what the monthly job would send: the
rolling statement, twelve months of movements closing at today's balance.
With one, it prints that single calendar month instead, which is the
on-demand "what happened in August" view and is NOT what goes out.

A preview that shows something other than what will be sent is worse than
no preview, so the default deliberately follows the job rather than the
convenience of a named month.

WRITES NOTHING AND SENDS NOTHING. It exists to be looked at before any of
this is rendered onto a document with a customer's name on it.

WHAT TO LOOK FOR

The closing balance, against what the customer's account actually says.
The last line of the output says whether the two agree. Where they do not,
this month cannot honestly be published for that customer, and the reason
is almost always the ledger drift that `manage.py balance_drift` reports
in bulk -- run that first for the scale of it, this for one account's
detail.

The opening balance is the figure with no record behind it anywhere in the
platform: Customer.balance is a stored running total and has never been
able to say what it was on the 1st. This derives it from dated invoices,
payments and approved credits. Reading a familiar customer's month and
recognising it is the cheapest check available.
"""
import datetime

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Read-only: print one customer's monthly statement figures."

    def add_arguments(self, parser):
        parser.add_argument("--client-code", required=True, help="e.g. SMF463")
        parser.add_argument(
            "--period",
            help="One calendar month as YYYY-MM. Omit for the rolling statement "
                 "the monthly job actually sends.",
        )

    def handle(self, *args, **options):
        from billing import statements
        from customers.models import Customer
        from django.utils import timezone

        code = options["client_code"].strip()
        customer = Customer.objects.filter(customer_id__iexact=code).first()
        if customer is None:
            self.stderr.write(f'No customer with client code "{code}".')
            return

        period = options.get("period")
        if period:
            try:
                when = datetime.datetime.strptime(period, "%Y-%m").date()
            except ValueError:
                self.stderr.write(f'--period must look like 2026-09, not "{period}".')
                return
            s = statements.period_statement(customer, when.year, when.month)
        else:
            # What the job sends, not a named month. See this module's
            # docstring.
            s = statements.rolling_statement(customer)

        self.stdout.write("")
        self.stdout.write(f"{customer.customer_id}  {customer.full_name}")
        self.stdout.write(f"{s['heading']}   {s['start']} to {s['end']}")
        self.stdout.write("")
        self.stdout.write(f"  {'':<10}  {'':<36}  {'debit':>12} {'credit':>12} {'balance':>13}")
        self.stdout.write(
            f"  {'':<10}  {'Balance brought forward':<36}  {'':>12} {'':>12} "
            f"{s['opening_balance']:>13,.2f}"
        )
        for line in s["lines"]:
            reference = line["reference"] or ""
            label = f"{line['description']}"[:36]
            debit = f"{line['debit']:,.2f}" if line["debit"] else ""
            credit = f"{line['credit']:,.2f}" if line["credit"] else ""
            self.stdout.write(
                f"  {line['date'].isoformat():<10}  {label:<36}  "
                f"{debit:>12} {credit:>12} {line['balance']:>13,.2f}"
                + (f"   {reference}" if reference else "")
            )
        self.stdout.write(
            f"  {'':<10}  {'Balance carried forward':<36}  {'':>12} {'':>12} "
            f"{s['closing_balance']:>13,.2f}"
        )
        self.stdout.write("")
        self.stdout.write(
            f"  invoiced {s['invoiced']:,.2f}   paid {s['paid']:,.2f}   "
            f"credited {s['credited']:,.2f}"
        )

        check = s["reconciliation"]
        self.stdout.write("")
        if check["reconciles"]:
            self.stdout.write(
                "  Agrees with the account balance. This month can be published."
            )
        else:
            self.stdout.write(
                f"  DOES NOT AGREE with the account balance. "
                f"Stored {check['stored']:,.2f}, records account for "
                f"{check['expected']:,.2f}, drift {check['drift']:,.2f}."
            )
            self.stdout.write(
                "  This customer's statement must not be published until that is "
                "resolved -- see manage.py balance_drift."
            )
        self.stdout.write("")
