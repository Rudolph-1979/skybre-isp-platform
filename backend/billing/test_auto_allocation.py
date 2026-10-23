"""R620 in the bank and an invoice reading R0.00 paid.

WHAT WAS BROKEN

Recording a payment and settling an invoice were two separate actions,
and only the first was automatic. A payment captured without naming an
invoice moved Customer.balance and nothing else, so the invoice stayed
UNPAID -- and an UNPAID invoice ages into OVERDUE, collects a reminder on
day 3 and day 4, a cut-off warning on the 5th at 09:00, and gets the
customer suspended at 15:00. All for money already received.

billing.allocation was written to close that gap and had everything
needed. Nothing called it.

THE ONE THAT WOULD HAVE COST REAL MONEY

A payment recorded AGAINST a specific invoice credits that invoice's
paid_amount directly, in PaymentSerializer.create, and writes NO
PaymentAllocation row. allocation.unallocated_amount computes
`amount - allocated_total`, so for such a payment it reports the FULL
amount as unallocated. Auto-allocating it would have put the same rand
onto a second invoice and quietly marked it paid.

That test is first, and it is the reason this is a guard rather than a
loop over every payment.
"""
import datetime
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from customers.models import Customer

from .allocation import unallocated_amount
from .models import Invoice, Payment, PaymentAllocation
from .statements import IMPORT_NOTE_PREFIX


class AutoAllocationTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            full_name="Lucille Slabbert", customer_id="CUS-009400",
            email="lucille4@example.com", status=Customer.Status.ACTIVE,
        )

    # --- fixtures ---------------------------------------------------------

    def _invoice(self, total, due, status=Invoice.Status.UNPAID):
        return Invoice.objects.create(
            customer=self.customer, status=status,
            total=Decimal(total), date_due=due,
        )

    def _pay(self, amount, **kwargs):
        """Straight through the model, inside an on-commit capture so the
        signal actually runs -- TestCase wraps each test in a transaction
        that never commits."""
        with self.captureOnCommitCallbacks(execute=True):
            return Payment.objects.create(
                customer=self.customer, amount=Decimal(amount), **kwargs
            )

    # --- the guard --------------------------------------------------------

    def test_a_payment_against_one_invoice_is_never_auto_allocated(self):
        """THE ONE THAT WOULD HAVE DOUBLE-CREDITED. The serializer has
        already put this money on that invoice without an allocation row,
        so `unallocated_amount` reports the whole payment as spare. There
        is nothing to distribute -- the money is where it belongs."""
        target = self._invoice("516.35", datetime.date(2026, 9, 16))
        other = self._invoice("400.00", datetime.date(2026, 8, 16))
        # What PaymentSerializer.create does for a direct payment.
        Invoice.objects.filter(pk=target.pk).update(
            paid_amount=Decimal("516.35"), status=Invoice.Status.PAID
        )

        payment = self._pay("516.35", invoice=target)

        self.assertEqual(payment.allocations.count(), 0)
        other.refresh_from_db()
        self.assertEqual(other.paid_amount, Decimal("0.00"))
        self.assertEqual(other.status, Invoice.Status.UNPAID)

    def test_an_imported_payment_is_left_alone(self):
        """The ledger import back-dates years of history one row at a
        time, and those invoices may already have been settled in the
        system they came from."""
        invoice = self._invoice("516.35", datetime.date(2026, 9, 16))

        self._pay("620.00", note=f"{IMPORT_NOTE_PREFIX}RCP2024-01-00001")

        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, Decimal("0.00"))

    # --- the normal case --------------------------------------------------

    def test_a_customer_payment_settles_the_open_invoice(self):
        """Lucille, exactly: R620 received against a R516.35 invoice."""
        invoice = self._invoice("516.35", datetime.date(2026, 9, 16))

        payment = self._pay("620.00")

        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, Decimal("516.35"))
        self.assertEqual(invoice.status, Invoice.Status.PAID)
        self.assertEqual(payment.allocations.count(), 1)

    def test_the_remainder_stays_as_credit(self):
        """R620 against R516.35 leaves R103.65 unallocated, which is what
        the balance already says. Nothing extra to explain to anybody."""
        self._invoice("516.35", datetime.date(2026, 9, 16))

        payment = self._pay("620.00")

        self.assertEqual(unallocated_amount(payment), Decimal("103.65"))

    def test_the_oldest_due_invoice_is_settled_first(self):
        newer = self._invoice("300.00", datetime.date(2026, 9, 16))
        older = self._invoice("300.00", datetime.date(2026, 7, 16))

        self._pay("300.00")

        older.refresh_from_db()
        newer.refresh_from_db()
        self.assertEqual(older.status, Invoice.Status.PAID)
        self.assertEqual(newer.paid_amount, Decimal("0.00"))

    def test_a_partial_payment_part_pays_without_marking_it_paid(self):
        invoice = self._invoice("516.35", datetime.date(2026, 9, 16))

        self._pay("200.00")

        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, Decimal("200.00"))
        self.assertEqual(invoice.status, Invoice.Status.UNPAID)

    def test_a_payment_spreads_across_several_invoices(self):
        first = self._invoice("300.00", datetime.date(2026, 7, 16))
        second = self._invoice("300.00", datetime.date(2026, 8, 16))

        self._pay("500.00")

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, Invoice.Status.PAID)
        self.assertEqual(second.paid_amount, Decimal("200.00"))

    def test_a_quote_is_never_settled(self):
        """A quote is not a debt. allocation.UNALLOCATABLE_STATUSES says
        so, and this proves the signal honours it."""
        quote = self._invoice("900.00", datetime.date(2026, 9, 16), Invoice.Status.QUOTE)

        self._pay("900.00")

        quote.refresh_from_db()
        self.assertEqual(quote.paid_amount, Decimal("0.00"))

    def test_allocation_never_moves_the_balance(self):
        """The balance moves exactly once, when the payment is recorded.
        Anything here that touches it is a double-count."""
        self._invoice("516.35", datetime.date(2026, 9, 16))
        Customer.objects.filter(pk=self.customer.pk).update(balance=Decimal("516.35"))

        self._pay("620.00")

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal("516.35"))

    def test_nothing_owing_allocates_nothing(self):
        payment = self._pay("620.00")

        self.assertEqual(PaymentAllocation.objects.filter(payment=payment).count(), 0)


class AllocatePaymentsCommandTests(TestCase):
    """The backlog: every payment recorded before the signal existed."""

    def setUp(self):
        self.customer = Customer.objects.create(
            full_name="Lucille Slabbert", customer_id="CUS-009500",
            email="lucille5@example.com", status=Customer.Status.ACTIVE,
        )
        self.invoice = Invoice.objects.create(
            customer=self.customer, status=Invoice.Status.UNPAID,
            total=Decimal("516.35"), date_due=datetime.date(2026, 9, 16),
        )
        # Created WITHOUT the on-commit capture, so the signal never ran --
        # which is exactly the state every pre-existing payment is in.
        self.payment = Payment.objects.create(
            customer=self.customer, amount=Decimal("620.00")
        )

    def _run(self, *args):
        out, err = StringIO(), StringIO()
        call_command("allocate_payments", *args, stdout=out, stderr=err)
        return out.getvalue() + err.getvalue()

    def test_a_dry_run_changes_nothing(self):
        output = self._run()

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid_amount, Decimal("0.00"))
        self.assertIn("Would allocate 1", output)
        self.assertIn("Lucille Slabbert", output)

    def test_commit_settles_the_invoice(self):
        self._run("--commit")

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid_amount, Decimal("516.35"))
        self.assertEqual(self.invoice.status, Invoice.Status.PAID)

    def test_a_direct_invoice_payment_is_not_in_the_backlog(self):
        Payment.objects.filter(pk=self.payment.pk).update(invoice=self.invoice)

        output = self._run()

        self.assertIn("Nothing to allocate", output)

    def test_imported_payments_are_skipped_unless_asked_for(self):
        Payment.objects.filter(pk=self.payment.pk).update(
            note=f"{IMPORT_NOTE_PREFIX}RCP2024-01-00001"
        )

        self.assertIn("Nothing to allocate", self._run())
        self.assertIn("Would allocate 1", self._run("--include-imported"))

    def test_one_customer_can_be_targeted(self):
        other = Customer.objects.create(
            full_name="Other Otto", customer_id="CUS-009501",
            email="otto2@example.com", status=Customer.Status.ACTIVE,
        )
        theirs = Invoice.objects.create(
            customer=other, status=Invoice.Status.UNPAID,
            total=Decimal("100.00"), date_due=datetime.date(2026, 9, 16),
        )
        Payment.objects.create(customer=other, amount=Decimal("100.00"))

        self._run("--client-code", "CUS-009500", "--commit")

        self.invoice.refresh_from_db()
        theirs.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.Status.PAID)
        self.assertEqual(theirs.paid_amount, Decimal("0.00"))

    def test_running_twice_allocates_nothing_the_second_time(self):
        self._run("--commit")

        self.assertIn("Nothing to allocate", self._run())
