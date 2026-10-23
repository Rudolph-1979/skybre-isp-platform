"""The statement that goes out on the 2nd.

WHAT THIS IS ABOUT

A document with the customer's name on it, a closing balance, and an
implicit demand. Six ways to get it wrong, one test each:

  * THE WRONG PEOPLE -- the first version keyed on
    CustomerBillingConfig.billing_enabled, which governs whether the
    recurring engine invoices and suspends somebody and says nothing about
    whether they have a ledger. The customer with thirty invoices and two
    years of payments got nothing; two accounts with no activity got blank
    pages;
  * THE WRONG MONTH -- sent on the 2nd covering the month in progress, so
    it is wrong by the 3rd;
  * A FIGURE THAT CONTRADICTS THE ACCOUNT -- the derived closing balance
    disagreeing with what the customer is actually being chased for. That
    is a dispute in writing, and the one case where sending nothing is the
    better outcome;
  * SENT TWICE -- a cron that overlaps, a retry, somebody running it by
    hand afterwards;
  * RECORDED AS SENT WHEN IT WAS NOT -- mark the period done after a mail
    failure and that customer silently never gets that month;
  * SENT AT ALL ON A DRY RUN -- this goes to every customer at once, and
    the first run is the only time that is true for the first time.

Nothing here renders a real PDF or sends real email.
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

from .models import CustomerBillingConfig, Invoice, Payment

ZERO = Decimal("0.00")

# A month safely in the past, so nothing here depends on the day it runs.
AUGUST = "2026-08"


class MonthlyStatementTests(TestCase):
    def setUp(self):
        EmailTemplate.objects.get_or_create(
            key=EmailTemplate.Key.STATEMENT,
            defaults={"name": "Statement", "subject": "Statement", "body_html": "<p>x</p>"},
        )
        self.customer = self._customer("Lucille Slabbert", "CUS-007001")
        self._activity(self.customer)
        self._reconcile(self.customer)

    # --- fixtures ---------------------------------------------------------

    def _customer(self, name, code, *, email=None, billing=True):
        customer = Customer.objects.create(
            full_name=name, customer_id=code,
            email=f"{code.lower()}@example.com" if email is None else email,
            status=Customer.Status.ACTIVE,
        )
        config = CustomerBillingConfig.for_customer(customer)
        config.billing_enabled = billing
        config.auto_create_invoices = False
        config.save()
        return customer

    def _activity(self, customer, total="500.00"):
        """One invoice in August, so this customer has a month to report."""
        return self._invoice(customer, datetime.date(2026, 8, 5), total)

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
        """Make the stored balance agree with the records, which is the
        normal state and the precondition for sending anything."""
        from billing import statements

        expected = statements.reconciliation(customer)["expected"]
        Customer.objects.filter(pk=customer.pk).update(balance=expected)
        customer.refresh_from_db()

    def _spy(self):
        sent = []

        def record(template_key, customer, **kwargs):
            sent.append((template_key, customer.pk, kwargs.get("period")))
            return EmailLog(status=EmailLog.Status.SENT)

        return sent, mock.patch(
            "notifications.services.send_customer_email", side_effect=record
        )

    def _run(self, **flags):
        out, err = StringIO(), StringIO()
        args = []
        for key, value in flags.items():
            flag = "--" + key.replace("_", "-")
            if value is True:
                args.append(flag)
            elif value is not None:
                args.extend([flag, str(value)])
        sent, patcher = self._spy()
        with patcher:
            call_command("send_monthly_statements", *args, stdout=out, stderr=err)
        return sent, out.getvalue() + err.getvalue()

    # --- who gets one -----------------------------------------------------

    def test_a_customer_who_is_not_billing_enabled_still_gets_one(self):
        """THE ONE THAT CHANGED. billing_enabled governs whether the
        recurring engine invoices and suspends somebody. It says nothing
        about whether they have an account worth reporting on, and keying
        statements off it meant the customer with the longest ledger here
        received nothing."""
        manual = self._customer("Manual Marius", "CUS-007002", billing=False)
        self._activity(manual)
        self._reconcile(manual)

        sent, _ = self._run(period=AUGUST, apply=True)

        self.assertIn(manual.pk, [s[1] for s in sent])

    def test_a_customer_with_no_activity_and_no_balance_is_skipped(self):
        """There is nothing on the page. A document saying so is not a
        document."""
        self._customer("Quiet Quinn", "CUS-007003")

        sent, output = self._run(period=AUGUST, apply=True)

        self.assertEqual([s[1] for s in sent], [self.customer.pk])
        self.assertIn("no activity and no balance", output)

    def test_a_quiet_month_with_a_carried_balance_still_gets_one(self):
        """No movements, but they owe money. That is exactly the customer
        who should be reminded what the balance is."""
        carried = self._customer("Carried Carel", "CUS-007004")
        self._invoice(carried, datetime.date(2026, 6, 5), "700.00")
        self._reconcile(carried)

        sent, _ = self._run(period=AUGUST, apply=True)

        self.assertIn(carried.pk, [s[1] for s in sent])

    def test_a_customer_with_no_client_code_is_skipped(self):
        nameless = Customer.objects.create(full_name="No Code", email="n@example.com")
        Customer.objects.filter(pk=nameless.pk).update(customer_id="")
        self._activity(nameless)

        sent, _ = self._run(period=AUGUST, apply=True)

        self.assertEqual([s[1] for s in sent], [self.customer.pk])

    def test_a_customer_with_no_email_is_named_not_silently_dropped(self):
        other = self._customer("No Mail Nandi", "CUS-007005", email="")
        self._activity(other)
        self._reconcile(other)

        sent, output = self._run(period=AUGUST, apply=True)

        self.assertEqual([s[1] for s in sent], [self.customer.pk])
        self.assertIn("no email address", output)
        self.assertIn("No Mail Nandi", output)

    def test_one_customer_can_be_targeted(self):
        other = self._customer("Other Otto", "CUS-007006")
        self._activity(other)
        self._reconcile(other)

        sent, _ = self._run(period=AUGUST, client_code="CUS-007006", apply=True)

        self.assertEqual([s[1] for s in sent], [other.pk])

    # --- the period -------------------------------------------------------

    def test_it_covers_the_month_just_ended(self):
        """Sent on the 2nd. A statement for the month in progress is wrong
        by the 3rd."""
        last_month = timezone.localdate().replace(day=1) - datetime.timedelta(days=1)
        self._invoice(self.customer, last_month, "120.00")
        self._reconcile(self.customer)

        sent, _ = self._run(apply=True)

        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][2], last_month.strftime("%Y-%m"))

    def test_an_explicit_period_wins(self):
        sent, _ = self._run(period=AUGUST, apply=True)

        self.assertEqual(sent[0][2], AUGUST)

    def test_a_malformed_period_is_refused(self):
        _, output = self._run(period="August")

        self.assertIn("2026-09", output)

    # --- the gate ---------------------------------------------------------

    def test_a_customer_whose_records_do_not_reconcile_is_not_sent(self):
        """THE ONE THAT MATTERS MOST. A closing balance that contradicts
        what we are chasing them for is a dispute, in writing, with our
        name on it. Sending nothing is the better outcome."""
        Customer.objects.filter(pk=self.customer.pk).update(balance=Decimal("1250.00"))

        sent, output = self._run(period=AUGUST, apply=True)

        self.assertEqual(sent, [])
        self.assertIn("NOT SENT", output)
        self.assertIn("Lucille Slabbert", output)
        self.assertIn("balance_drift", output)

    def test_one_customer_failing_to_reconcile_does_not_stop_the_rest(self):
        broken = self._customer("Broken Bennie", "CUS-007007")
        self._activity(broken)
        Customer.objects.filter(pk=broken.pk).update(balance=Decimal("99.00"))

        sent, output = self._run(period=AUGUST, apply=True)

        self.assertEqual([s[1] for s in sent], [self.customer.pk])
        self.assertIn("Broken Bennie", output)

    # --- not sending it twice ---------------------------------------------

    def test_a_second_run_for_the_same_period_sends_nothing(self):
        first, _ = self._run(period=AUGUST, apply=True)
        second, output = self._run(period=AUGUST, apply=True)

        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        self.assertIn("already sent", output)

    def test_a_different_period_is_sent(self):
        self._invoice(self.customer, datetime.date(2026, 7, 5), "300.00")
        self._reconcile(self.customer)

        self._run(period=AUGUST, apply=True)
        sent, _ = self._run(period="2026-07", apply=True)

        self.assertEqual(len(sent), 1)

    def test_resend_overrides_the_guard(self):
        self._run(period=AUGUST, apply=True)
        sent, _ = self._run(period=AUGUST, resend=True, apply=True)

        self.assertEqual(len(sent), 1)

    def test_a_failed_send_is_not_recorded_as_sent(self):
        """Mark the period done after a mail outage and that customer
        silently never gets that month."""
        def fail(template_key, customer, **kwargs):
            return EmailLog(status=EmailLog.Status.FAILED, error_message="smtp down")

        with mock.patch("notifications.services.send_customer_email", side_effect=fail):
            call_command(
                "send_monthly_statements", "--period", AUGUST, "--apply",
                stdout=StringIO(), stderr=StringIO(),
            )

        self.assertEqual(
            CustomerBillingConfig.objects.get(customer=self.customer).statement_sent_for, ""
        )

        sent, _ = self._run(period=AUGUST, apply=True)
        self.assertEqual(len(sent), 1, "the failed statement was never retried")

    # --- the dry run ------------------------------------------------------

    def test_a_dry_run_sends_nothing_and_records_nothing(self):
        sent, output = self._run(period=AUGUST)

        self.assertEqual(sent, [])
        self.assertEqual(
            CustomerBillingConfig.objects.get(customer=self.customer).statement_sent_for, ""
        )
        self.assertIn("would send 1", output)

    def test_it_refuses_when_there_is_no_statement_template(self):
        """A run that reports success having sent nothing is worse than a
        refusal."""
        EmailTemplate.objects.filter(key=EmailTemplate.Key.STATEMENT).delete()

        sent, output = self._run(period=AUGUST, apply=True)

        self.assertEqual(sent, [])
        self.assertIn("no Statement email template", output)

    # --- the figures on it ------------------------------------------------

    def test_the_opening_balance_is_last_months_closing(self):
        """The number this whole exercise exists to produce, and the one
        Customer.balance could never answer."""
        from billing import statements

        other = self._customer("Ledger Lena", "CUS-007008")
        self._invoice(other, datetime.date(2026, 7, 5), "500.00")
        self._invoice(other, datetime.date(2026, 8, 5), "400.00")
        self._payment(other, datetime.date(2026, 8, 20), "300.00")
        self._reconcile(other)

        statement = statements.period_statement(other, 2026, 8)

        self.assertEqual(statement["opening_balance"], Decimal("500.00"))
        self.assertEqual(statement["closing_balance"], Decimal("600.00"))
        self.assertTrue(statement["reconciliation"]["reconciles"])
