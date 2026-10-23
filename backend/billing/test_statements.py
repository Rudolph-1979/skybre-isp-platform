"""Deriving what a customer owed on a date, from records that never said.

WHAT THESE TESTS ARE REALLY ABOUT

`Customer.balance` is a stored running total. It knows what is owed now
and nothing about what was owed on the 1st, which is the one number a
monthly statement cannot do without. billing.statements reconstructs it
from the dated records -- and a reconstruction that is subtly wrong is
worse than no statement, because it arrives on a document with the
customer's name on it and an amount they are being asked to pay.

Four ways to get it wrong, and a test for each:

  * COUNTING THE MONTH'S OWN MOVEMENTS IN THE OPENING BALANCE, which
    double-counts everything and is invisible unless you add the column up;
  * COUNTING SOMETHING THAT IS NOT A DEBT -- a quote, a pro forma, a draft
    -- and chasing a customer for a number they never agreed to;
  * USING THE WRONG DATE, so an invoice raised in August but issued in
    September lands in the wrong month and both statements are wrong;
  * A CREDIT NOTE AS A NEGATIVE DEBIT, which is arithmetically identical
    and reads as a mistake.

And the fifth, which is the reason the module exists at all: a derived
balance that disagrees with the stored one must SAY SO. Anything else
publishes a document that contradicts what the customer is being chased
for.

Nothing here renders a PDF or sends anything.
"""
import datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from customers.models import Customer

from . import statements
from .models import CreditRequest, Invoice, Payment

ZERO = Decimal("0.00")


class StatementDerivationTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            full_name="Lucille Slabbert", customer_id="CUS-005500", city="Kroonstad",
        )

    # --- helpers ---------------------------------------------------------

    def _invoice(self, day, total, status=Invoice.Status.UNPAID, number=""):
        invoice = Invoice.objects.create(
            customer=self.customer, status=status, total=Decimal(total),
            date_due=day + datetime.timedelta(days=14),
        )
        fields = {"date_issued": day, "date_created": day}
        if number:
            fields["number"] = number
        Invoice.objects.filter(pk=invoice.pk).update(**fields)
        return Invoice.objects.get(pk=invoice.pk)

    def _payment(self, day, amount):
        payment = Payment.objects.create(customer=self.customer, amount=Decimal(amount))
        # `date` is auto_now_add, so it can only be back-dated by UPDATE --
        # the same trick the ledger import uses for imported history.
        Payment.objects.filter(pk=payment.pk).update(
            date=timezone.make_aware(
                datetime.datetime.combine(day, datetime.time(12, 0)),
                timezone.get_current_timezone(),
            )
        )
        return payment

    def _credit(self, day, amount, status=CreditRequest.Status.APPROVED):
        return CreditRequest.objects.create(
            customer=self.customer, amount=Decimal(amount), reason="Outage",
            status=status,
            decided_at=timezone.make_aware(
                datetime.datetime.combine(day, datetime.time(12, 0)),
                timezone.get_current_timezone(),
            ),
        )

    def _september(self):
        return statements.period_statement(self.customer, 2026, 9)

    # --- the opening balance ---------------------------------------------

    def test_the_opening_balance_is_what_came_before(self):
        self._invoice(datetime.date(2026, 7, 5), "500.00")
        self._invoice(datetime.date(2026, 8, 5), "500.00")
        self._payment(datetime.date(2026, 8, 20), "300.00")

        self.assertEqual(self._september()["opening_balance"], Decimal("700.00"))

    def test_the_months_own_movements_are_not_in_the_opening_balance(self):
        """The double-count. Invisible unless somebody adds the column up,
        and by then it is on a document the customer is holding."""
        self._invoice(datetime.date(2026, 8, 5), "500.00")
        self._invoice(datetime.date(2026, 9, 5), "499.00")

        statement = self._september()

        self.assertEqual(statement["opening_balance"], Decimal("500.00"))
        self.assertEqual(statement["closing_balance"], Decimal("999.00"))

    def test_a_movement_on_the_first_belongs_to_the_month(self):
        self._invoice(datetime.date(2026, 9, 1), "100.00")

        statement = self._september()

        self.assertEqual(statement["opening_balance"], ZERO)
        self.assertEqual(len(statement["lines"]), 1)

    def test_a_movement_on_the_last_day_belongs_to_the_month(self):
        self._invoice(datetime.date(2026, 9, 30), "100.00")

        self.assertEqual(len(self._september()["lines"]), 1)

    def test_a_movement_on_the_first_of_october_does_not(self):
        self._invoice(datetime.date(2026, 10, 1), "100.00")

        statement = self._september()

        self.assertEqual(statement["lines"], [])
        self.assertEqual(statement["closing_balance"], ZERO)

    # --- what counts as a debt -------------------------------------------

    def test_a_quote_a_pro_forma_and_a_draft_are_not_debts(self):
        """A customer chased for a quote they never accepted."""
        self._invoice(datetime.date(2026, 9, 3), "100.00", status=Invoice.Status.QUOTE)
        self._invoice(datetime.date(2026, 9, 4), "200.00", status=Invoice.Status.PROFORMA)
        self._invoice(datetime.date(2026, 9, 5), "300.00", status=Invoice.Status.DRAFT)

        statement = self._september()

        self.assertEqual(statement["lines"], [])
        self.assertEqual(statement["closing_balance"], ZERO)

    def test_a_cancelled_invoice_is_not_a_debt(self):
        self._invoice(datetime.date(2026, 9, 3), "100.00", status=Invoice.Status.CANCELLED)

        self.assertEqual(self._september()["closing_balance"], ZERO)

    def test_a_paid_invoice_is_still_a_debit(self):
        """It was billed and then settled, and a statement showing only the
        payment would have money arriving against nothing."""
        self._invoice(datetime.date(2026, 9, 3), "400.00", status=Invoice.Status.PAID)
        self._payment(datetime.date(2026, 9, 20), "400.00")

        statement = self._september()

        self.assertEqual(len(statement["lines"]), 2)
        self.assertEqual(statement["closing_balance"], ZERO)
        self.assertEqual(statement["invoiced"], Decimal("400.00"))
        self.assertEqual(statement["paid"], Decimal("400.00"))

    def test_the_issue_date_wins_over_the_creation_date(self):
        """An invoice drafted in August and issued in September belongs to
        September -- otherwise both months are wrong at once."""
        invoice = self._invoice(datetime.date(2026, 9, 2), "250.00")
        Invoice.objects.filter(pk=invoice.pk).update(date_created=datetime.date(2026, 8, 20))

        self.assertEqual(self._september()["invoiced"], Decimal("250.00"))

    # --- credits ----------------------------------------------------------

    def test_a_credit_note_is_a_credit_not_a_negative_debit(self):
        """Arithmetically identical; a minus sign in the debit column is
        not."""
        self._invoice(datetime.date(2026, 9, 3), "500.00")
        self._invoice(
            datetime.date(2026, 9, 10), "-150.00",
            status=Invoice.Status.PAID, number="CN000001",
        )

        statement = self._september()
        note = [line for line in statement["lines"] if line["kind"] == "credit_note"][0]

        self.assertEqual(note["debit"], ZERO)
        self.assertEqual(note["credit"], Decimal("150.00"))
        self.assertEqual(statement["closing_balance"], Decimal("350.00"))

    def test_an_approved_credit_request_is_a_credit(self):
        self._invoice(datetime.date(2026, 9, 3), "500.00")
        self._credit(datetime.date(2026, 9, 12), "80.00")

        statement = self._september()

        self.assertEqual(statement["credited"], Decimal("80.00"))
        self.assertEqual(statement["closing_balance"], Decimal("420.00"))

    def test_a_pending_credit_request_is_not(self):
        """It has not happened yet, and it has not moved the balance."""
        self._invoice(datetime.date(2026, 9, 3), "500.00")
        self._credit(datetime.date(2026, 9, 12), "80.00", status=CreditRequest.Status.PENDING)

        self.assertEqual(self._september()["closing_balance"], Decimal("500.00"))

    # --- ordering ---------------------------------------------------------

    def test_lines_run_oldest_first_with_a_running_balance(self):
        self._invoice(datetime.date(2026, 9, 3), "500.00")
        self._payment(datetime.date(2026, 9, 10), "200.00")
        self._invoice(datetime.date(2026, 9, 20), "100.00")

        lines = self._september()["lines"]

        self.assertEqual([line["date"].day for line in lines], [3, 10, 20])
        self.assertEqual(
            [line["balance"] for line in lines],
            [Decimal("500.00"), Decimal("300.00"), Decimal("400.00")],
        )

    def test_an_invoice_precedes_a_payment_made_the_same_day(self):
        """Otherwise a same-day settlement shows the money arriving before
        the bill it settles."""
        self._invoice(datetime.date(2026, 9, 9), "500.00")
        self._payment(datetime.date(2026, 9, 9), "500.00")

        self.assertEqual(
            [line["kind"] for line in self._september()["lines"]], ["invoice", "payment"]
        )

    def test_a_quiet_month_opens_and_closes_the_same(self):
        self._invoice(datetime.date(2026, 6, 3), "500.00")

        statement = self._september()

        self.assertEqual(statement["lines"], [])
        self.assertEqual(statement["opening_balance"], statement["closing_balance"])

    # --- the gate ---------------------------------------------------------

    def test_a_derivation_that_matches_the_stored_balance_reconciles(self):
        self._invoice(datetime.date(2026, 9, 3), "500.00")
        self._payment(datetime.date(2026, 9, 10), "200.00")
        Customer.objects.filter(pk=self.customer.pk).update(balance=Decimal("300.00"))

        result = statements.reconciliation(Customer.objects.get(pk=self.customer.pk))

        self.assertTrue(result["reconciles"])
        self.assertEqual(result["drift"], ZERO)

    def test_a_derivation_that_does_not_match_says_so(self):
        """THE ONE THAT DECIDES WHETHER ANY OF THIS CAN BE PUBLISHED.

        Migration 0020 set balance_debited to NULL on every invoice then in
        existence, because what the old code had already done to the
        balance was not knowable. Those debits are in the stored figure by
        means nobody can reconstruct, and a statement that quietly
        disagrees with what a customer is being chased for is a dispute we
        would lose."""
        self._invoice(datetime.date(2026, 9, 3), "500.00")
        Customer.objects.filter(pk=self.customer.pk).update(balance=Decimal("1250.00"))

        result = statements.reconciliation(Customer.objects.get(pk=self.customer.pk))

        self.assertFalse(result["reconciles"])
        self.assertEqual(result["expected"], Decimal("500.00"))
        self.assertEqual(result["stored"], Decimal("1250.00"))
        # Same sign convention as manage.py balance_drift: stored minus
        # expected, so positive means owing more than the invoices account
        # for. A second convention in a second place is how two reports end
        # up disagreeing about the direction of a customer's problem.
        self.assertEqual(result["drift"], Decimal("750.00"))

    def test_it_agrees_with_balance_drifts_definition_of_expected(self):
        """ONE DEFINITION OF WHAT SOMEBODY OWES, not two.

        balance_drift computes expected as invoiced minus paid minus
        credited, in bulk. This walks dated movements one at a time. If the
        two ever drift apart, two reports start disagreeing about the same
        customer, and the one on the customer's statement is the one that
        gets argued about."""
        self._invoice(datetime.date(2026, 7, 5), "500.00")
        self._invoice(datetime.date(2026, 9, 3), "499.00")
        self._invoice(
            datetime.date(2026, 9, 10), "-150.00",
            status=Invoice.Status.PAID, number="CN000002",
        )
        self._invoice(datetime.date(2026, 9, 4), "999.00", status=Invoice.Status.QUOTE)
        self._payment(datetime.date(2026, 8, 20), "300.00")
        self._credit(datetime.date(2026, 9, 12), "80.00")

        invoiced = sum(
            (i.total for i in Invoice.objects.filter(
                customer=self.customer, status__in=Invoice.DEBITED_STATUSES
            )), ZERO,
        )
        paid = sum((p.amount for p in Payment.objects.filter(customer=self.customer)), ZERO)
        credited = sum(
            (c.amount for c in CreditRequest.objects.filter(
                customer=self.customer, status=CreditRequest.Status.APPROVED
            )), ZERO,
        )

        self.assertEqual(
            statements.reconciliation(self.customer)["expected"],
            invoiced - paid - credited,
        )

    def test_the_statement_carries_the_reconciliation(self):
        """So a caller cannot publish one without having been told."""
        self._invoice(datetime.date(2026, 9, 3), "500.00")
        Customer.objects.filter(pk=self.customer.pk).update(balance=ZERO)

        self.assertFalse(self._september()["reconciliation"]["reconciles"])

    # --- shape -----------------------------------------------------------

    def test_the_period_label_and_dates_describe_the_month(self):
        statement = self._september()

        self.assertEqual(statement["period"], "2026-09")
        self.assertEqual(statement["start"], datetime.date(2026, 9, 1))
        # Inclusive for display: the 30th, not the 1st of October.
        self.assertEqual(statement["end"], datetime.date(2026, 9, 30))

    def test_february_in_a_leap_year_ends_on_the_29th(self):
        self.assertEqual(
            statements.period_statement(self.customer, 2028, 2)["end"],
            datetime.date(2028, 2, 29),
        )
