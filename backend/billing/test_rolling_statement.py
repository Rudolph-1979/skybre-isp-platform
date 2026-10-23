"""The statement closes at today, not at a month end.

WHAT WAS WRONG WITH THE CALENDAR MONTH

Nothing arithmetically. The figures were right, the period was labelled,
and the document was still misleading: sent on the 2nd, it closed on the
last day of the previous month, and the invoice raised on the 1st was
already sitting on the account by the time it arrived. The customer read
"balance carried forward R516.35" the day after being billed for
something else.

So the closing balance is now the balance AS AT THE SEND DATE, which is
the same figure `reconciliation()` compares against the stored balance.
Where an account reconciles, the number at the foot of the page and the
number we are chasing them for cannot differ. That is the first test
here, and it is the one that matters.

WHAT THE WINDOW IS FOR

Life-to-date never stops growing, so anything older than twelve months is
collapsed into the brought-forward line. The DETAIL is dropped; the
BALANCE is not. A customer whose only invoice is three years old still
has a closing balance, still gets a statement, and still sees what they
owe -- they just don't get thirty-six months of paper to find it in. The
second group of tests is about that distinction, because getting it wrong
would silently drop a debt off a document.

Nothing here renders a PDF or sends real email.
"""
import datetime
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from customers.models import Customer
from notifications.models import EmailLog, EmailTemplate

from . import statements
from .models import CustomerBillingConfig, Invoice, Payment

ZERO = Decimal("0.00")

#: Every date here is pinned. The suite has already been broken once by
#: fixtures computed from the real today drifting across a weekend.
AS_AT = datetime.date(2026, 10, 2)

#: What window_start(AS_AT, 12) must produce.
WINDOW = datetime.date(2025, 11, 1)


class RollingStatementTests(TestCase):
    def setUp(self):
        self.customer = self._customer("Lucille Slabbert", "CUS-008001")

    # --- fixtures ---------------------------------------------------------

    def _customer(self, name, code):
        customer = Customer.objects.create(
            full_name=name, customer_id=code, email=f"{code.lower()}@example.com",
            status=Customer.Status.ACTIVE,
        )
        config = CustomerBillingConfig.for_customer(customer)
        config.auto_create_invoices = False
        config.save()
        return customer

    def _invoice(self, customer, day, total):
        invoice = Invoice.objects.create(
            customer=customer, status=Invoice.Status.UNPAID, total=Decimal(total),
            date_due=day + datetime.timedelta(days=14),
        )
        Invoice.objects.filter(pk=invoice.pk).update(date_issued=day, date_created=day)
        return invoice

    def _payment(self, customer, day, amount):
        payment = Payment.objects.create(customer=customer, amount=Decimal(amount))
        Payment.objects.filter(pk=payment.pk).update(
            date=timezone.make_aware(
                datetime.datetime.combine(day, datetime.time(12, 0)),
                timezone.get_current_timezone(),
            )
        )
        return payment

    def _reconcile(self, customer):
        """Under the pinned date, always. reconciliation() reads the real
        today, so computing it outside the pin sets a balance from a
        different moment than the one under test -- see the note on the
        command tests below, where that cost a failing run."""
        with mock.patch("django.utils.timezone.localdate", return_value=AS_AT):
            Customer.objects.filter(pk=customer.pk).update(
                balance=statements.reconciliation(customer)["expected"]
            )

    def _statement(self, customer=None, as_at=AS_AT, **kwargs):
        return statements.rolling_statement(customer or self.customer, as_at=as_at, **kwargs)

    # --- the window -------------------------------------------------------

    def test_twelve_months_back_from_october_is_the_previous_november(self):
        self.assertEqual(statements.window_start(AS_AT, 12), WINDOW)

    def test_the_window_counts_months_not_days(self):
        """Month-index arithmetic, so month lengths and leap years cannot
        shift the boundary."""
        self.assertEqual(
            statements.window_start(datetime.date(2028, 2, 29), 12),
            datetime.date(2027, 3, 1),
        )

    def test_one_month_is_the_month_in_progress(self):
        self.assertEqual(statements.window_start(AS_AT, 1), datetime.date(2026, 10, 1))

    def test_a_window_of_no_months_is_refused(self):
        with self.assertRaises(ValueError):
            statements.window_start(AS_AT, 0)

    # --- the closing balance ----------------------------------------------

    def test_the_closing_balance_is_the_balance_on_the_day_it_is_sent(self):
        """THE ONE THAT MATTERS. The invoice raised on the 1st is on the
        statement sent on the 2nd -- under the calendar-month version it
        was not, and the customer read a figure their account had already
        moved past."""
        self._invoice(self.customer, datetime.date(2026, 9, 16), "516.35")
        self._invoice(self.customer, datetime.date(2026, 10, 1), "435.85")

        statement = self._statement()

        self.assertEqual(statement["closing_balance"], Decimal("952.20"))
        self.assertIn(
            datetime.date(2026, 10, 1), [line["date"] for line in statement["lines"]]
        )

    def test_the_closing_balance_equals_the_figure_the_gate_checks(self):
        """Not a coincidence -- both are the derived balance as at the
        same moment, so a reconciled account cannot print one number and
        be chased for another."""
        self._invoice(self.customer, datetime.date(2026, 9, 16), "516.35")
        self._payment(self.customer, datetime.date(2026, 9, 20), "100.00")
        self._reconcile(self.customer)

        with mock.patch("django.utils.timezone.localdate", return_value=AS_AT):
            statement = self._statement()

            self.assertEqual(
                statement["closing_balance"], statement["reconciliation"]["expected"]
            )
            self.assertEqual(
                statement["closing_balance"], statement["reconciliation"]["stored"]
            )
            self.assertTrue(statement["reconciliation"]["reconciles"])

    def test_a_movement_dated_today_is_on_it(self):
        """The window ends inclusive: a payment captured this morning
        belongs on a statement sent this afternoon."""
        self._invoice(self.customer, datetime.date(2026, 9, 1), "500.00")
        self._payment(self.customer, AS_AT, "500.00")

        statement = self._statement()

        self.assertEqual(statement["closing_balance"], ZERO)
        self.assertEqual(statement["paid"], Decimal("500.00"))

    def test_a_movement_dated_tomorrow_is_not(self):
        self._invoice(self.customer, AS_AT + datetime.timedelta(days=1), "500.00")

        statement = self._statement()

        self.assertEqual(statement["lines"], [])
        self.assertEqual(statement["closing_balance"], ZERO)

    def test_the_running_balance_adds_up(self):
        self._invoice(self.customer, datetime.date(2026, 5, 1), "1000.00")
        self._payment(self.customer, datetime.date(2026, 6, 1), "400.00")
        self._invoice(self.customer, datetime.date(2026, 7, 1), "250.00")

        statement = self._statement()

        self.assertEqual(
            statement["opening_balance"]
            + sum(line["debit"] - line["credit"] for line in statement["lines"]),
            statement["closing_balance"],
        )
        self.assertEqual(statement["lines"][-1]["balance"], statement["closing_balance"])

    # --- what the window collapses, and what it must never drop -----------

    def test_movements_older_than_the_window_are_collapsed_not_dropped(self):
        """The detail goes; the money does not."""
        self._invoice(self.customer, datetime.date(2025, 6, 1), "700.00")
        self._invoice(self.customer, datetime.date(2026, 9, 1), "300.00")

        statement = self._statement()

        self.assertEqual(statement["opening_balance"], Decimal("700.00"))
        self.assertEqual([line["date"] for line in statement["lines"]],
                         [datetime.date(2026, 9, 1)])
        self.assertEqual(statement["closing_balance"], Decimal("1000.00"))

    def test_an_old_debt_with_no_recent_activity_still_shows_what_is_owed(self):
        """Three years old, nothing since. No lines at all, and the
        balance must still be on the page -- a window that dropped this
        would quietly write the debt off the document."""
        self._invoice(self.customer, datetime.date(2023, 4, 1), "1250.00")

        statement = self._statement()

        self.assertEqual(statement["lines"], [])
        self.assertEqual(statement["opening_balance"], Decimal("1250.00"))
        self.assertEqual(statement["closing_balance"], Decimal("1250.00"))

    def test_the_window_is_configurable_without_changing_the_balance(self):
        self._invoice(self.customer, datetime.date(2026, 1, 15), "900.00")

        wide = self._statement(months=12)
        narrow = self._statement(months=3)

        self.assertEqual(len(wide["lines"]), 1)
        self.assertEqual(narrow["lines"], [])
        self.assertEqual(wide["closing_balance"], narrow["closing_balance"])

    # --- the document's own labelling -------------------------------------

    def test_it_carries_its_own_heading(self):
        self.assertEqual(self._statement()["heading"], "Statement of Account")

    def test_the_window_start_is_what_the_brought_forward_line_is_dated(self):
        self.assertEqual(self._statement()["start"], WINDOW)
        self.assertEqual(self._statement()["end"], AS_AT)

    def test_a_named_month_still_works_and_says_so(self):
        """period_statement is not what goes out any more, but the preview
        and any on-demand use still want it."""
        self._invoice(self.customer, datetime.date(2026, 8, 5), "400.00")

        statement = statements.period_statement(self.customer, 2026, 8)

        self.assertEqual(statement["heading"], "Statement for August 2026")
        self.assertEqual(statement["start"], datetime.date(2026, 8, 1))
        self.assertEqual(statement["end"], datetime.date(2026, 8, 31))
        self.assertEqual(statement["closing_balance"], Decimal("400.00"))


class RollingStatementThroughTheCommandTests(TestCase):
    """The job sends the rolling document, not the named month."""

    def setUp(self):
        EmailTemplate.objects.get_or_create(
            key=EmailTemplate.Key.STATEMENT,
            defaults={"name": "Statement", "subject": "Statement", "body_html": "<p>x</p>"},
        )
        self.customer = Customer.objects.create(
            full_name="Lucille Slabbert", customer_id="CUS-008100",
            email="lucille@example.com", status=Customer.Status.ACTIVE,
        )
        config = CustomerBillingConfig.for_customer(self.customer)
        config.auto_create_invoices = False
        config.save()

    def _invoice(self, day, total):
        invoice = Invoice.objects.create(
            customer=self.customer, status=Invoice.Status.UNPAID, total=Decimal(total),
            date_due=day + datetime.timedelta(days=14),
        )
        Invoice.objects.filter(pk=invoice.pk).update(date_issued=day, date_created=day)
        return invoice

    def _reconcile(self):
        """Set the stored balance UNDER THE SAME PINNED DATE the run uses.

        This is how the first version of this test failed, and the failure
        was worth keeping a note about. Computing the expected balance
        against the real today counted only the invoices up to today; the
        run, pinned to 2 October, counted one more. The gate saw a drift
        and refused to send -- correctly. The fixture was lying to it, not
        the other way round.

        In production both sides of that comparison are "now", so they
        move together and this cannot happen. Only a test that pins one
        date and not the other can produce it.
        """
        with mock.patch("django.utils.timezone.localdate", return_value=AS_AT):
            Customer.objects.filter(pk=self.customer.pk).update(
                balance=statements.reconciliation(self.customer)["expected"]
            )

    def _run(self, *args):
        out, err = StringIO(), StringIO()
        sent = []

        def record(template_key, customer, **kwargs):
            sent.append((customer.pk, kwargs.get("period")))
            return EmailLog(status=EmailLog.Status.SENT)

        with mock.patch("django.utils.timezone.localdate", return_value=AS_AT), \
                mock.patch("notifications.services.send_customer_email", side_effect=record):
            call_command("send_monthly_statements", *args, stdout=out, stderr=err)
        return sent, out.getvalue() + err.getvalue()

    def test_the_run_key_is_last_month_and_the_document_is_not(self):
        """--period says which run this is. The statement still closes
        today, which is the whole point of the change."""
        self._invoice(datetime.date(2026, 9, 16), "516.35")
        self._invoice(datetime.date(2026, 10, 1), "435.85")
        self._reconcile()

        sent, output = self._run("--apply")

        self.assertEqual([s[1] for s in sent], ["2026-09"])
        self.assertIn("as at 02/10/2026", output)
        self.assertIn("952.20", output)

    def test_a_customer_whose_only_debt_predates_the_window_is_still_sent_one(self):
        """No lines, real balance. The skip rule is "no activity AND no
        balance" -- if the window ever swallowed the balance too, this
        customer would silently stop being chased."""
        self._invoice(datetime.date(2023, 4, 1), "1250.00")
        self._reconcile()

        sent, _ = self._run("--apply")

        self.assertEqual([s[0] for s in sent], [self.customer.pk])

    def test_a_customer_with_nothing_at_all_is_still_skipped(self):
        sent, output = self._run("--apply")

        self.assertEqual(sent, [])
        self.assertIn("no activity and no balance", output)
