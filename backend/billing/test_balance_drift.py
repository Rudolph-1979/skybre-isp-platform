"""The drift report has to be right, because decisions get made off it.

This command had no tests, which is how it shipped computing three
Sum()s over three multi-valued relations in one annotate(). Django emits
that as a single query with three LEFT JOINs, so every sum is multiplied
by the row counts of the other two -- the standard join fan-out. A
customer with two invoices and two payments reported double the real
invoiced and paid figures, and because the invoice side inflates faster
than the payment side, the drift could come out with the WRONG SIGN.

That matters more than an ordinary reporting bug: this is the tool that
decides how ~1,592 real balances get repaired by hand, and its closing
line tells the reader that a negative drift is the ledger bug's
signature. It was manufacturing that signature.

The fan-out only appears with MORE THAN ONE row in more than one
relation, which is exactly what a single-invoice-single-payment test
would have missed. Every case below has at least two of something.
"""
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from billing.models import CreditRequest, Invoice, InvoiceItem, Payment
from customers.models import Customer


class BalanceDriftTests(TestCase):
    def _customer(self, name, balance="0.00"):
        return Customer.objects.create(
            full_name=name, email=f"{name.split()[0].lower()}@example.com",
            balance=Decimal(balance),
        )

    def _invoice(self, customer, total, status=Invoice.Status.UNPAID):
        invoice = Invoice.objects.create(
            customer=customer, status=status, date_due="2026-09-30"
        )
        InvoiceItem.objects.create(
            invoice=invoice, description="Service", quantity=1,
            unit_price=Decimal(total), tax_rate_pct=Decimal("0.00"),
        )
        invoice.recalc_totals()
        return invoice

    def _run(self, *args):
        out = StringIO()
        call_command("balance_drift", *args, stdout=out)
        return out.getvalue()

    def _drift_for(self, customer):
        """Re-derive what the command should print, from the report line."""
        output = self._run("--all")
        for line in output.splitlines():
            if line.strip().startswith(str(customer.id) + " ") or f" {customer.id}  " in line:
                return line
        return ""

    # ---- the fan-out ----------------------------------------------------

    def test_two_invoices_and_two_payments_are_not_multiplied(self):
        """The exact reproduction. Truth: invoiced 200, paid 100,
        expected 100, drift -100. The fan-out reported -200."""
        customer = self._customer("Fanout Fred", "0.00")
        self._invoice(customer, "100.00")
        self._invoice(customer, "100.00")
        Payment.objects.create(customer=customer, amount=Decimal("50.00"))
        Payment.objects.create(customer=customer, amount=Decimal("50.00"))

        # The report prints stored / expected / drift. Expected is
        # 200 invoiced - 100 paid = 100; drift is 0 - 100 = -100.
        # The fan-out doubled both sides, giving expected 200 and drift -200.
        line = self._drift_for(customer)
        self.assertIn("100.00", line)
        self.assertIn("-100.00", line)
        self.assertNotIn("-200.00", line)
        self.assertNotIn("200.00", line)

    def test_three_relations_at_once_do_not_multiply(self):
        """Invoices, payments AND credits together -- the worst case for a
        three-way join."""
        customer = self._customer("Triple Thandi", "0.00")
        self._invoice(customer, "300.00")
        self._invoice(customer, "300.00")
        Payment.objects.create(customer=customer, amount=Decimal("100.00"))
        Payment.objects.create(customer=customer, amount=Decimal("100.00"))
        CreditRequest.objects.create(
            customer=customer, amount=Decimal("50.00"), reason="a",
            status=CreditRequest.Status.APPROVED,
        )
        CreditRequest.objects.create(
            customer=customer, amount=Decimal("50.00"), reason="b",
            status=CreditRequest.Status.APPROVED,
        )
        # invoiced 600 - paid 200 - credited 100 = expected 300, stored 0.
        line = self._drift_for(customer)
        self.assertIn("300.00", line)
        self.assertIn("-300.00", line)

    def test_the_sign_is_not_flipped_by_the_join(self):
        """The dangerous consequence: a customer genuinely OWING more than
        the ledger says must not be reported as being in credit."""
        customer = self._customer("Sign Sipho", "1000.00")
        self._invoice(customer, "100.00")
        self._invoice(customer, "100.00")
        Payment.objects.create(customer=customer, amount=Decimal("10.00"))
        Payment.objects.create(customer=customer, amount=Decimal("10.00"))
        # expected 180, stored 1000 -> drift +820, overstated.
        line = self._drift_for(customer)
        self.assertIn("820.00", line)
        self.assertNotIn("-", line.split("820.00")[0].split(str(customer.id))[-1])

    # ---- the arithmetic itself ------------------------------------------

    def test_a_correct_balance_shows_no_drift(self):
        customer = self._customer("Correct Cathy", "150.00")
        self._invoice(customer, "100.00")
        self._invoice(customer, "100.00")
        Payment.objects.create(customer=customer, amount=Decimal("25.00"))
        Payment.objects.create(customer=customer, amount=Decimal("25.00"))
        output = self._run("--all")
        self.assertNotIn(f"  {customer.id}  ", output)

    def test_cancelled_and_draft_invoices_are_excluded(self):
        """Only DEBITED_STATUSES count toward what is owed."""
        customer = self._customer("Excluded Ethan", "0.00")
        self._invoice(customer, "500.00", status=Invoice.Status.CANCELLED)
        self._invoice(customer, "500.00", status=Invoice.Status.DRAFT)
        output = self._run("--all")
        self.assertNotIn(f"  {customer.id}  ", output)

    def test_a_rejected_credit_does_not_count(self):
        customer = self._customer("Rejected Rose", "100.00")
        self._invoice(customer, "100.00")
        CreditRequest.objects.create(
            customer=customer, amount=Decimal("100.00"), reason="no",
            status=CreditRequest.Status.REJECTED,
        )
        output = self._run("--all")
        self.assertNotIn(f"  {customer.id}  ", output)

    def test_a_customer_with_nothing_at_all_shows_no_drift(self):
        self._customer("Empty Emma", "0.00")
        output = self._run("--all")
        self.assertIn("every balance agrees", output)

    def test_the_summary_counts_both_directions_separately(self):
        owing = self._customer("Owing Olive", "500.00")
        self._invoice(owing, "100.00")
        credit = self._customer("Credited Carl", "-300.00")
        self._invoice(credit, "100.00")
        output = self._run()
        self.assertIn("Owing MORE than their invoices", output)
        self.assertIn("Showing credit they never had", output)

    # ---- it must not write anything -------------------------------------

    def test_the_command_writes_nothing(self):
        customer = self._customer("Readonly Rita", "999.00")
        invoice = self._invoice(customer, "100.00")
        Payment.objects.create(customer=customer, amount=Decimal("10.00"))
        before = (customer.balance, invoice.paid_amount, invoice.status)
        self._run("--all")
        customer.refresh_from_db()
        invoice.refresh_from_db()
        self.assertEqual((customer.balance, invoice.paid_amount, invoice.status), before)
