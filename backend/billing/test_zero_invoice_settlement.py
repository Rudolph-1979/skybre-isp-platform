"""An invoice that was never owed anything is not an unpaid debt.

`recalc_totals` would only mark an invoice paid when its total was
greater than zero. That condition was standing in for "this document has
lines yet" -- without it, an invoice created empty and saved before its
items are added flashes up as Paid -- but "total is zero" and "has no
lines" are only the same thing until somebody gets a 100% discount.

A customer on a full discount is billed R999 and discounted R999. The
invoice reads R0.00 owed, R0.00 outstanding, and sat at UNPAID: ageing
into Overdue, counted in the unpaid lists, and picked up by the payment
reminder pass, which selects on status alone. Rudolph's INV-000012 was
the live example.

These pin the fix at both ends -- the zero invoice settles, and the empty
draft it was protecting still does not.
"""
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from io import StringIO

from billing.models import Invoice, InvoiceItem, Service, Tariff
from billing.overdue import owed_q
from customers.models import Customer


class ZeroInvoiceSettlementTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.customer = Customer.objects.create(
            full_name="Rudolph Grunder", customer_id="CUS-001597", email="r@example.com",
        )
        self.tariff = Tariff.objects.create(
            name="MTD 250 Mbps", price=Decimal("999.00"), tax_rate_pct=Decimal("15"),
            speed_download_kbps=256000, speed_upload_kbps=256000,
        )

    def _invoice(self, status=Invoice.Status.UNPAID):
        return Invoice.objects.create(
            customer=self.customer, status=status, date_due=self.today,
        )

    def _line(self, invoice, price, description="MTD 250 Mbps (Monthly)"):
        return InvoiceItem.objects.create(
            invoice=invoice, item_type=InvoiceItem.ItemType.TARIFF,
            description=description, quantity=1, unit_price=Decimal(price),
            tax_rate_pct=Decimal("15"),
        )

    # --- the fix ---------------------------------------------------------

    def test_a_fully_discounted_invoice_settles(self):
        invoice = self._invoice()
        self._line(invoice, "999.00")
        self._line(invoice, "-999.00", "Discount — Owner (100%)")

        invoice.recalc_totals()

        invoice.refresh_from_db()
        self.assertEqual(invoice.total, Decimal("0.00"))
        self.assertEqual(invoice.status, Invoice.Status.PAID)

    def test_a_settled_zero_invoice_is_not_owed(self):
        """The consequence that actually reached the customer: the reminder
        pass and every overdue list select on status, so an unpaid R0.00
        invoice was chased like a debt."""
        invoice = self._invoice()
        self._line(invoice, "999.00")
        self._line(invoice, "-999.00", "Discount — Owner (100%)")
        invoice.recalc_totals()

        self.assertFalse(Invoice.objects.filter(owed_q(), pk=invoice.pk).exists())

    # --- what the old guard was protecting, still protected --------------

    def test_an_invoice_with_no_lines_is_left_alone(self):
        """An invoice created empty, before its items are added. This is the
        case the `total > 0` check existed for, and it must keep working."""
        invoice = self._invoice()

        invoice.recalc_totals()

        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.UNPAID)

    def test_a_quote_is_never_flipped(self):
        quote = self._invoice(status=Invoice.Status.QUOTE)
        self._line(quote, "999.00")
        self._line(quote, "-999.00", "Discount")

        quote.recalc_totals()

        quote.refresh_from_db()
        self.assertEqual(quote.status, Invoice.Status.QUOTE)

    def test_a_draft_is_never_flipped(self):
        """A draft has not been issued and carries no balance debit -- the
        platform already refuses payments against one. The old check let a
        fully-paid draft through; a zero-total draft would have walked
        through the widened one."""
        draft = self._invoice(status=Invoice.Status.DRAFT)
        self._line(draft, "999.00")
        self._line(draft, "-999.00", "Discount")

        draft.recalc_totals()

        draft.refresh_from_db()
        self.assertEqual(draft.status, Invoice.Status.DRAFT)

    def test_a_cancelled_invoice_is_never_flipped(self):
        cancelled = self._invoice(status=Invoice.Status.CANCELLED)
        self._line(cancelled, "999.00")
        self._line(cancelled, "-999.00", "Discount")

        cancelled.recalc_totals()

        cancelled.refresh_from_db()
        self.assertEqual(cancelled.status, Invoice.Status.CANCELLED)

    # --- ordinary invoices unchanged -------------------------------------

    def test_an_unpaid_invoice_with_a_balance_stays_unpaid(self):
        invoice = self._invoice()
        self._line(invoice, "999.00")

        invoice.recalc_totals()

        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.UNPAID)

    def test_a_fully_paid_invoice_still_settles(self):
        invoice = self._invoice()
        self._line(invoice, "999.00")
        invoice.recalc_totals()
        invoice.refresh_from_db()
        invoice.paid_amount = Decimal("1148.85")
        invoice.save(update_fields=["paid_amount"])

        invoice.recalc_totals()

        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PAID)


class SettleZeroInvoicesCommandTests(TestCase):
    """The invoices raised before the fix. They will not correct themselves:
    recalc_totals only runs when a document's lines change, and nobody is
    going to edit these."""

    def setUp(self):
        self.customer = Customer.objects.create(
            full_name="Rudolph Grunder", customer_id="CUS-001597", email="r@example.com",
        )
        self.stranded = Invoice.objects.create(
            customer=self.customer, status=Invoice.Status.UNPAID,
            date_due=timezone.localdate(), total=Decimal("0.00"),
        )
        InvoiceItem.objects.create(
            invoice=self.stranded, description="MTD 250 Mbps (Monthly)",
            quantity=1, unit_price=Decimal("999.00"), tax_rate_pct=Decimal("15"),
        )
        InvoiceItem.objects.create(
            invoice=self.stranded, description="Discount — Owner (100%)",
            quantity=1, unit_price=Decimal("-999.00"), tax_rate_pct=Decimal("15"),
        )
        # Put the total back to zero: creating the items above does not
        # recalculate, and the point of this command is invoices whose
        # totals are already right and whose status is not.
        Invoice.objects.filter(pk=self.stranded.pk).update(total=Decimal("0.00"))

    def _run(self, *args):
        out = StringIO()
        call_command("settle_zero_invoices", *args, stdout=out, stderr=out)
        return out.getvalue()

    def test_dry_run_changes_nothing(self):
        output = self._run()

        self.assertIn("Dry run", output)
        self.stranded.refresh_from_db()
        self.assertEqual(self.stranded.status, Invoice.Status.UNPAID)

    def test_apply_settles_them(self):
        self._run("--apply")

        self.stranded.refresh_from_db()
        self.assertEqual(self.stranded.status, Invoice.Status.PAID)

    def test_an_empty_zero_invoice_is_left_alone(self):
        """No lines: an abandoned draft, not a discounted customer."""
        empty = Invoice.objects.create(
            customer=self.customer, status=Invoice.Status.UNPAID,
            date_due=timezone.localdate(), total=Decimal("0.00"),
        )

        self._run("--apply")

        empty.refresh_from_db()
        self.assertEqual(empty.status, Invoice.Status.UNPAID)

    def test_an_invoice_with_money_owing_is_untouched(self):
        owing = Invoice.objects.create(
            customer=self.customer, status=Invoice.Status.UNPAID,
            date_due=timezone.localdate(), total=Decimal("1148.85"),
        )
        InvoiceItem.objects.create(
            invoice=owing, description="MTD 250 Mbps (Monthly)",
            quantity=1, unit_price=Decimal("999.00"), tax_rate_pct=Decimal("15"),
        )

        self._run("--apply")

        owing.refresh_from_db()
        self.assertEqual(owing.status, Invoice.Status.UNPAID)
