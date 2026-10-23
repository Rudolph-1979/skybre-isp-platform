"""Chasing a customer for an invoice that is already late.

THE DEFECT THIS CLOSES

The engine decides whether a reminder is due with

    (invoice.date_due - run_date).days in offsets

a signed number: positive is before the due date, negative after it. The
arithmetic always handled both. The COLUMN could only hold the first --
reminder_1/2/3_day were PositiveSmallIntegerField -- so an overdue chaser
was not a feature switched off somewhere. It was a number the database
refused to store, while the settings screen offered "before/after due
date" as though it were configurable.

The practical effect on an ISP: the platform could tell a customer a bill
was coming and could never once tell them one was late. The only contact
after the due date was the line going off.

Everything below runs the real engine, not the predicate in isolation --
the field type was the bug, so a test that called _process_reminders
directly with a negative offset would have passed before the fix.
"""
import datetime
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from customers.models import Customer
from notifications.models import EmailLog, EmailTemplate

from .models import CustomerBillingConfig, Invoice, InvoiceItem, ReminderSettings
from .recurring import run_recurring_billing


class ChaserTestCase(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        row = ReminderSettings.load()
        row.reminder_1_enabled = True
        row.reminder_2_enabled = True
        row.save()

    def _customer(self, name, *, offsets, due_in, balance=Decimal("575.00")):
        """A customer owing one invoice, with the given reminder offsets.

        `due_in` is signed the way a human reads it: 7 means the invoice
        falls due in a week, -7 means it fell due a week ago.
        """
        customer = Customer.objects.create(
            full_name=name, email=f"{name.split()[0].lower()}@example.com",
            status=Customer.Status.ACTIVE, balance=balance,
        )
        config = CustomerBillingConfig.for_customer(customer)
        config.billing_enabled = True
        # No invoicing in these tests. auto_create_invoices defaults to
        # True, and a run that raised its own invoice would put a second
        # candidate in front of the reminder loop -- which is a real
        # behaviour with its own tests, and noise in these.
        config.auto_create_invoices = False
        config.next_billing_date = None
        config.reminder_enabled = True
        config.reminder_1_day = offsets[0]
        if len(offsets) > 1:
            config.reminder_2_day = offsets[1]
        config.save()
        self._invoice(customer, due_in=due_in)
        return customer

    def _invoice(self, customer, due_in):
        invoice = Invoice.objects.create(
            customer=customer, status=Invoice.Status.UNPAID,
            date_due=self.today + datetime.timedelta(days=due_in),
            paid_amount=Decimal("0.00"),
        )
        InvoiceItem.objects.create(
            invoice=invoice, item_type=InvoiceItem.ItemType.CUSTOM,
            description="Internet", quantity=1, unit_price=Decimal("500.00"),
            tax_rate_pct=Decimal("15.00"),
        )
        Invoice.objects.filter(pk=invoice.pk).update(
            subtotal=Decimal("500.00"), tax_total=Decimal("75.00"), total=Decimal("575.00"),
        )
        invoice.refresh_from_db()
        return invoice

    def _spy(self):
        sent = []

        def record(template_key, customer, **kwargs):
            invoice = kwargs.get("invoice")
            sent.append((template_key, customer.pk, invoice.number if invoice else None))
            return EmailLog(status=EmailLog.Status.SENT)

        return sent, mock.patch("billing.recurring.send_customer_email", side_effect=record)

    def _run(self, on=None):
        sent, patcher = self._spy()
        with patcher, self.captureOnCommitCallbacks(execute=True):
            result = run_recurring_billing(on or self.today, commit=True)
        reminders = [s for s in sent if s[0] == EmailTemplate.Key.PAYMENT_REMINDER]
        return reminders, result


class OverdueChaserTests(ChaserTestCase):
    def test_a_negative_offset_chases_an_overdue_invoice(self):
        """THE ONE THIS PACKAGE EXISTS FOR. Seven days past due, with a
        -7 offset, the customer hears from us."""
        customer = self._customer("Delia Dunn", offsets=[-7], due_in=-7)

        reminders, result = self._run()

        self.assertEqual(len(reminders), 1, "an overdue customer was not chased")
        self.assertEqual(reminders[0][1], customer.pk)
        self.assertEqual(result["counts"]["reminders_sent"], 1)

    def test_the_column_can_hold_a_negative_offset(self):
        """The field type WAS the bug. Saving -7 and reading it back is the
        thing that was impossible before this migration."""
        customer = self._customer("Elsa Eng", offsets=[-14], due_in=-14)

        config = CustomerBillingConfig.objects.get(customer=customer)

        self.assertEqual(config.reminder_1_day, -14)

    def test_it_does_not_chase_on_the_wrong_day(self):
        """An exact-day match, still. A -7 offset does not fire on day 6 or
        day 8 -- it is a scheduled chaser, not a standing nag."""
        self._customer("Frank Fox", offsets=[-7], due_in=-6)

        reminders, _ = self._run()

        self.assertEqual(reminders, [])

    def test_a_warning_and_a_chaser_can_coexist(self):
        """The realistic setup: warn a few days before, chase a week after.
        Both are offsets on the same customer."""
        customer = self._customer("Gita Gama", offsets=[3, -7], due_in=-7)

        reminders, _ = self._run()

        self.assertEqual(len(reminders), 1)
        self.assertEqual(reminders[0][1], customer.pk)

    def test_the_pre_due_warning_still_works(self):
        """The fix must not become a way to stop warning anybody."""
        customer = self._customer("Hugo Hall", offsets=[3, -7], due_in=3)

        reminders, _ = self._run()

        self.assertEqual(len(reminders), 1)
        self.assertEqual(reminders[0][1], customer.pk)

    def test_zero_fires_on_the_due_date_itself(self):
        """The boundary between a warning and a chaser, and a legitimate
        setting -- "your account falls due today"."""
        self._customer("Ivy Ives", offsets=[0], due_in=0)

        reminders, _ = self._run()

        self.assertEqual(len(reminders), 1)

    def test_a_chased_customer_who_owes_nothing_is_still_not_chased(self):
        """The balance gate is unchanged and must stay unchanged. An
        invoice row left at unpaid on a settled account is exactly the
        case that produced demands for R 0.00."""
        self._customer("Jonas Jack", offsets=[-7], due_in=-7, balance=Decimal("0.00"))

        reminders, _ = self._run()

        self.assertEqual(reminders, [])

    def test_chasing_the_same_invoice_twice_in_one_day_sends_once(self):
        """Re-running a run is a no-op, chaser or not."""
        self._customer("Kobus Kemp", offsets=[-7], due_in=-7)

        first, _ = self._run()
        second, _ = self._run()

        self.assertEqual(len(first), 1)
        self.assertEqual(second, [], "a second run on the same day chased again")

    def test_the_oldest_overdue_invoice_is_the_one_chased(self):
        """One reminder per customer per run. With two overdue invoices
        matching, the older debt is the one to raise -- and the loop reads
        them oldest-due-first for that reason."""
        customer = self._customer("Lena Loots", offsets=[-7, -3], due_in=-7)
        self._invoice(customer, due_in=-3)

        reminders, _ = self._run()

        self.assertEqual(len(reminders), 1)
        oldest = (
            Invoice.objects.filter(customer=customer).order_by("date_due", "pk").first()
        )
        self.assertEqual(reminders[0][2], oldest.number)
