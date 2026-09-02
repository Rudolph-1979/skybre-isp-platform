"""What a recurring-billing run must survive.

A run walks every opted-in customer. The question these tests exist to
answer is what happens when customer number three of eight hundred throws
-- because that is not hypothetical, it is the ordinary case of a dropped
connection, a bad tariff row, or a numbering clash.

Two properties matter, and neither is about the happy path:

  * One customer's failure must not undo another customer's invoice. A run
    that bills 797 people correctly and then rolls all of it back because
    the 798th had a problem is worse than one that bills 797 people.
  * An invoice email must never go out for an invoice that does not exist.
    SMTP does not roll back. Once the customer is holding a PDF for
    INV-000251, that number has to stay theirs.
"""
import datetime
from decimal import Decimal
from unittest import mock

from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from billing.models import (
    CustomerBillingConfig, Invoice, RecurringBillingRun, Service, Tariff,
)
from billing.recurring import run_recurring_billing
from customers.models import Customer


class RecurringBillingRunTestCase(TestCase):
    """Shared setup: customers who are all due to be billed today."""

    def setUp(self):
        self.today = timezone.localdate()
        self.tariff = Tariff.objects.create(
            name="Home 20Mbps", price=Decimal("500.00"), tax_rate_pct=Decimal("15.00"),
            speed_download_kbps=20480, speed_upload_kbps=10240,
        )

    def _billable(self, name):
        """A customer with an active service, opted in, due today."""
        customer = Customer.objects.create(
            full_name=name, email=f"{name.split()[0].lower()}@example.com",
            status=Customer.Status.ACTIVE,
        )
        Service.objects.create(
            customer=customer, tariff=self.tariff,
            status=Service.Status.ACTIVE, start_date=self.today - datetime.timedelta(days=90),
        )
        config = CustomerBillingConfig.for_customer(customer)
        config.billing_enabled = True
        config.next_billing_date = self.today
        config.save()
        return customer

    def _fail_on(self, victim):
        """Blow up partway through `victim`'s invoice, after the invoice
        row, its line items, the balance charge and the email have all
        happened -- i.e. at the latest possible moment, which is the one
        that tells us most about what gets left behind."""
        real_save = CustomerBillingConfig.save

        def maybe_boom(config, *args, **kwargs):
            if config.customer_id == victim.pk:
                raise RuntimeError("connection reset by peer mid-invoice")
            return real_save(config, *args, **kwargs)

        return mock.patch.object(CustomerBillingConfig, "save", maybe_boom)

    def _spy_on_email(self):
        """Records every email the run actually hands to the mailer.

        Deliberately not EmailLog: an EmailLog row written inside a doomed
        transaction disappears with it, which would make a delivered email
        look like it never happened. The whole point is that the mailer
        call does not roll back.
        """
        sent = []

        def record(template_key, customer, **kwargs):
            sent.append((template_key, customer.pk))

        patcher = mock.patch("billing.recurring.send_customer_email", side_effect=record)
        return sent, patcher


class OneCustomerFailingTests(RecurringBillingRunTestCase):
    def test_the_other_customers_invoices_survive(self):
        first = self._billable("Anna Able")
        victim = self._billable("Boris Broke")
        third = self._billable("Carol Clear")

        with self._fail_on(victim):
            run_recurring_billing(self.today, commit=True)

        self.assertEqual(first.invoices.count(), 1, "Anna was billed before the failure and must stay billed")
        self.assertEqual(third.invoices.count(), 1, "Carol comes after the failure and must still be billed")

    def test_the_failing_customer_is_left_with_nothing_half_written(self):
        self._billable("Anna Able")
        victim = self._billable("Boris Broke")

        with self._fail_on(victim):
            run_recurring_billing(self.today, commit=True)

        self.assertEqual(victim.invoices.count(), 0, "a half-written invoice must roll back, not linger")
        victim.refresh_from_db()
        self.assertEqual(victim.balance, Decimal("0.00"), "a rolled-back invoice must not leave a charge behind")

    def test_the_surviving_customers_are_still_charged(self):
        first = self._billable("Anna Able")
        victim = self._billable("Boris Broke")

        with self._fail_on(victim):
            run_recurring_billing(self.today, commit=True)

        first.refresh_from_db()
        self.assertEqual(first.balance, Decimal("575.00"), "500 + 15% VAT, and it must not roll back with Boris")

    def test_no_email_goes_out_for_an_invoice_that_gets_rolled_back(self):
        self._billable("Anna Able")
        victim = self._billable("Boris Broke")

        sent, patcher = self._spy_on_email()
        with patcher, self._fail_on(victim):
            with self.captureOnCommitCallbacks(execute=True):
                run_recurring_billing(self.today, commit=True)

        victim_emails = [t for t, pk in sent if pk == victim.pk]
        self.assertEqual(
            victim_emails, [],
            "Boris's invoice was rolled back, so he must never have been sent a PDF for it",
        )

    def test_the_survivors_still_get_their_email(self):
        first = self._billable("Anna Able")
        victim = self._billable("Boris Broke")

        sent, patcher = self._spy_on_email()
        with patcher, self._fail_on(victim):
            with self.captureOnCommitCallbacks(execute=True):
                run_recurring_billing(self.today, commit=True)

        self.assertIn(("invoice", first.pk), sent, "Anna's invoice committed, so her email must go out")

    def test_the_run_row_counts_only_what_actually_committed(self):
        self._billable("Anna Able")
        victim = self._billable("Boris Broke")
        self._billable("Carol Clear")

        with self._fail_on(victim):
            result = run_recurring_billing(self.today, commit=True)

        run = RecurringBillingRun.objects.get(pk=result["run"].pk)
        self.assertEqual(
            run.invoices_created_count, 2,
            "the history row must not claim an invoice that was rolled back",
        )
        self.assertEqual(run.invoices_created_count, Invoice.objects.count())

    def test_the_run_is_recorded_as_failed_and_says_who(self):
        self._billable("Anna Able")
        victim = self._billable("Boris Broke")

        result = None
        with self._fail_on(victim):
            result = run_recurring_billing(self.today, commit=True)

        run = RecurringBillingRun.objects.get(pk=result["run"].pk)
        self.assertEqual(run.status, RecurringBillingRun.Status.FAILED)
        self.assertIn(victim.customer_id, run.status_message,
                      "a partial failure has to name the customer to be actionable")

    def test_one_failure_does_not_stop_the_run_reaching_later_customers(self):
        victim = self._billable("Anna Able")
        later = self._billable("Zoe Zebra")

        with self._fail_on(victim):
            run_recurring_billing(self.today, commit=True)

        self.assertEqual(later.invoices.count(), 1, "the run must carry on past a bad customer, not abort")


class CleanRunTests(RecurringBillingRunTestCase):
    def test_a_clean_run_bills_everyone_and_is_marked_processed(self):
        for name in ("Anna Able", "Boris Bright", "Carol Clear"):
            self._billable(name)

        result = run_recurring_billing(self.today, commit=True)

        run = RecurringBillingRun.objects.get(pk=result["run"].pk)
        self.assertEqual(run.status, RecurringBillingRun.Status.PROCESSED)
        self.assertEqual(run.status_message, "")
        self.assertEqual(run.invoices_created_count, 3)
        self.assertEqual(Invoice.objects.count(), 3)

    def test_a_clean_run_sends_every_invoice_email(self):
        for name in ("Anna Able", "Boris Bright"):
            self._billable(name)

        sent, patcher = self._spy_on_email()
        with patcher:
            with self.captureOnCommitCallbacks(execute=True):
                run_recurring_billing(self.today, commit=True)

        self.assertEqual(len([t for t, _pk in sent if t == "invoice"]), 2)

    def test_a_preview_still_writes_nothing(self):
        self._billable("Anna Able")

        result = run_recurring_billing(self.today, commit=False)

        self.assertEqual(result["counts"]["invoices_created"], 1)
        self.assertEqual(Invoice.objects.count(), 0, "a preview must never create an invoice")
        self.assertEqual(RecurringBillingRun.objects.count(), 0, "a preview must never log a run")

    def test_invoices_are_linked_to_the_run_that_made_them(self):
        self._billable("Anna Able")

        result = run_recurring_billing(self.today, commit=True)

        invoice = Invoice.objects.get()
        self.assertEqual(invoice.created_by_run_id, result["run"].pk)


class InvoiceNumberingTests(TestCase):
    """Invoice numbers are a tax record, and the column is unique. Two
    things therefore have to hold: the next number is genuinely the next
    one, and a clash is survivable rather than a 500 (or, inside a billing
    run, a dead customer)."""

    def setUp(self):
        self.today = timezone.localdate()
        self.customer = Customer.objects.create(full_name="Nina Number", email="nina@example.com")

    def _invoice(self, **kwargs):
        kwargs.setdefault("status", Invoice.Status.UNPAID)
        kwargs.setdefault("date_due", self.today)
        return Invoice.objects.create(customer=self.customer, **kwargs)

    def test_the_next_number_follows_the_highest_one_issued(self):
        self._invoice()
        second = self._invoice()
        self.assertEqual(second.number, "INV-000002")

    def test_numbering_follows_the_highest_number_not_the_newest_row(self):
        """An imported or backdated invoice carrying a low number must not
        drag the sequence backwards onto numbers that are already taken."""
        self._invoice(number="INV-000500")
        self._invoice(number="INV-000100")  # newest row, lower number

        nxt = self._invoice()

        self.assertEqual(nxt.number, "INV-000501")

    def test_a_clash_is_retried_instead_of_blowing_up(self):
        first = self._invoice()
        calls = []
        real = Invoice._next_number_for_status

        def stale_first_time(invoice, status):
            calls.append(status)
            if len(calls) == 1:
                return first.number  # exactly the race a concurrent create loses
            return real(invoice, status)

        with mock.patch.object(Invoice, "_next_number_for_status", stale_first_time):
            second = self._invoice()

        self.assertNotEqual(second.number, first.number)
        self.assertGreater(len(calls), 1, "it has to actually retry, not just get lucky")

    def test_an_unrelated_integrity_error_is_not_swallowed(self):
        """The retry is specifically for number clashes. Anything else must
        surface, not be quietly retried five times and re-raised as if it
        were a numbering problem."""
        with mock.patch.object(
            Invoice, "_next_number_for_status", side_effect=IntegrityError('null value in column "customer_id"')
        ):
            with self.assertRaises(IntegrityError):
                self._invoice()

    def test_legacy_numbers_that_do_not_parse_are_ignored(self):
        self._invoice(number="INV-LEGACY-IMPORT")
        self._invoice(number="INV-000007")

        nxt = self._invoice()

        self.assertEqual(nxt.number, "INV-000008")

    def test_quotes_and_invoices_keep_separate_sequences(self):
        quote = self._invoice(status=Invoice.Status.QUOTE)
        invoice = self._invoice(status=Invoice.Status.UNPAID)
        self.assertEqual(quote.number, "QUO-000001")
        self.assertEqual(invoice.number, "INV-000001")

    def test_converting_a_quote_survives_a_number_clash(self):
        """convert_to_invoice assigns a number too, so it needs the same
        protection the ordinary create path has."""
        self._invoice(status=Invoice.Status.UNPAID)  # INV-000001
        quote = self._invoice(status=Invoice.Status.QUOTE)

        calls = []
        real = Invoice._next_number_for_status

        def stale_first_time(invoice, status):
            calls.append(status)
            if len(calls) == 1:
                return "INV-000001"  # already taken
            return real(invoice, status)

        with mock.patch.object(Invoice, "_next_number_for_status", stale_first_time):
            quote.convert_to_invoice()

        quote.refresh_from_db()
        self.assertEqual(quote.number, "INV-000002")
        self.assertEqual(quote.status, Invoice.Status.UNPAID)
