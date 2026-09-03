"""What recording, correcting and reversing a payment must do to the ledger.

Every ledger effect of a payment used to live in
PaymentSerializer.create() and nowhere else, which left two holes that
both turn one real payment into money the platform has lost track of:

  * There was no reversal path. Deleting a payment -- the obvious way to
    correct one captured against the wrong invoice -- left the customer's
    balance credited and the invoice still marked Paid. The bank feed then
    put the same bank transaction back in the review queue to be confirmed
    again, so correcting a mistake credited one EFT twice.
  * `customer` and `invoice` were independent writable FKs with no
    validation between them, so a payment could settle a different
    customer's invoice.

Each test below was written against the pre-fix code first and observed to
fail there, in the same way the recurring-billing tests were.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from billing.models import Invoice, InvoiceItem, Payment, Tariff
from customers.models import Customer

User = get_user_model()


class PaymentLedgerTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff = User.objects.create_user(
            username="finance1", password="pw-for-tests", role=User.Role.ACCOUNTS
        )
        self.client.force_authenticate(self.staff)
        self.tariff = Tariff.objects.create(
            name="Home 20", price=Decimal("500.00"),
            speed_download_kbps=20480, speed_upload_kbps=10240,
        )
        self.alice = Customer.objects.create(
            full_name="Alice Ncube", email="alice@example.com", balance=Decimal("1000.00")
        )
        self.bob = Customer.objects.create(
            full_name="Bob Mokoena", email="bob@example.com", balance=Decimal("1000.00")
        )

    def _invoice(self, customer, total="1000.00", status=Invoice.Status.UNPAID):
        invoice = Invoice.objects.create(customer=customer, status=status, date_due="2026-09-30")
        InvoiceItem.objects.create(
            invoice=invoice, description="Monthly service", quantity=1,
            unit_price=Decimal(total), tax_rate_pct=Decimal("0.00"),
        )
        invoice.recalc_totals()
        invoice.refresh_from_db()
        return invoice

    def _pay(self, customer, amount, invoice=None):
        body = {"customer": customer.id, "amount": str(amount), "method": "bank_transfer"}
        if invoice is not None:
            body["invoice"] = invoice.id
        return self.client.post("/api/payments/", body, format="json")

    # ---- reversal --------------------------------------------------------

    def test_deleting_a_payment_puts_the_balance_back(self):
        """The core of the double-credit bug: without this, the money was
        credited to the customer and then the same bank transaction could
        be confirmed a second time."""
        res = self._pay(self.alice, "600.00")
        self.assertEqual(res.status_code, 201, res.data)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.balance, Decimal("400.00"))

        res = self.client.delete(f"/api/payments/{res.data['id']}/")
        self.assertEqual(res.status_code, 204, getattr(res, "data", None))
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.balance, Decimal("1000.00"))

    def test_deleting_a_payment_unpays_its_invoice(self):
        invoice = self._invoice(self.alice, "1000.00")
        res = self._pay(self.alice, "1000.00", invoice=invoice)
        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, Decimal("1000.00"))
        self.assertEqual(invoice.status, Invoice.Status.PAID)

        self.client.delete(f"/api/payments/{res.data['id']}/")
        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, Decimal("0.00"))
        self.assertEqual(invoice.status, Invoice.Status.UNPAID)

    def test_reversing_a_partial_payment_leaves_the_invoice_unpaid(self):
        invoice = self._invoice(self.alice, "1000.00")
        first = self._pay(self.alice, "400.00", invoice=invoice)
        self._pay(self.alice, "600.00", invoice=invoice)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PAID)

        self.client.delete(f"/api/payments/{first.data['id']}/")
        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, Decimal("600.00"))
        self.assertEqual(invoice.status, Invoice.Status.UNPAID)

    def test_reversal_does_not_resurrect_a_cancelled_invoice(self):
        """Only a Paid invoice goes back to Unpaid. A cancelled one stays
        cancelled -- it did not become owed again because a payment
        against it was reversed."""
        invoice = self._invoice(self.alice, "500.00")
        res = self._pay(self.alice, "500.00", invoice=invoice)
        Invoice.objects.filter(pk=invoice.pk).update(status=Invoice.Status.CANCELLED)

        self.client.delete(f"/api/payments/{res.data['id']}/")
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.CANCELLED)

    # ---- the invoice must belong to the customer -------------------------

    def test_a_payment_cannot_settle_another_customers_invoice(self):
        """One request used to debit Alice's balance while flipping Bob's
        invoice to Paid, leaving two ledgers wrong and Bob no longer
        chased for money nobody received."""
        bobs_invoice = self._invoice(self.bob, "4000.00")
        res = self._pay(self.alice, "4000.00", invoice=bobs_invoice)

        self.assertEqual(res.status_code, 400)
        self.assertIn("invoice", res.data)
        bobs_invoice.refresh_from_db()
        self.assertEqual(bobs_invoice.paid_amount, Decimal("0.00"))
        self.assertEqual(bobs_invoice.status, Invoice.Status.UNPAID)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.balance, Decimal("1000.00"))

    def test_a_quote_cannot_be_paid(self):
        """Marking a quote Paid was a one-way trap: it then fails
        can_convert_to_invoice(), so it could never become a real invoice
        while still carrying a QUO- number."""
        quote = self._invoice(self.alice, "500.00", status=Invoice.Status.QUOTE)
        res = self._pay(self.alice, "500.00", invoice=quote)
        self.assertEqual(res.status_code, 400)
        quote.refresh_from_db()
        self.assertEqual(quote.status, Invoice.Status.QUOTE)
        self.assertTrue(quote.can_convert_to_invoice())

    def test_a_cancelled_invoice_cannot_be_paid(self):
        invoice = self._invoice(self.alice, "500.00")
        Invoice.objects.filter(pk=invoice.pk).update(status=Invoice.Status.CANCELLED)
        res = self._pay(self.alice, "500.00", invoice=invoice)
        self.assertEqual(res.status_code, 400)

    def test_a_zero_payment_is_refused(self):
        res = self._pay(self.alice, "0.00")
        self.assertEqual(res.status_code, 400)

    def test_a_payment_against_the_right_customer_still_works(self):
        """The guard must not get in the way of the ordinary case."""
        invoice = self._invoice(self.alice, "1000.00")
        res = self._pay(self.alice, "1000.00", invoice=invoice)
        self.assertEqual(res.status_code, 201, res.data)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PAID)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.balance, Decimal("0.00"))

    def test_a_negative_manual_adjustment_is_still_allowed(self):
        """Deliberately permitted -- it is how staff correct a ledger, and
        Method.MANUAL exists for it. Documented in PaymentSerializer.validate."""
        res = self.client.post(
            "/api/payments/",
            {"customer": self.alice.id, "amount": "-250.00", "method": "manual"},
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.balance, Decimal("1250.00"))

    # ---- editing -------------------------------------------------------

    def test_the_amount_cannot_be_edited_after_the_fact(self):
        """A PATCH used to change the row while leaving the balance and
        paid_amount reflecting the original figure forever."""
        res = self._pay(self.alice, "1000.00")
        payment_id = res.data["id"]
        res = self.client.patch(f"/api/payments/{payment_id}/", {"amount": "100.00"}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("amount", res.data)

        self.alice.refresh_from_db()
        self.assertEqual(self.alice.balance, Decimal("0.00"))
        self.assertEqual(Payment.objects.get(pk=payment_id).amount, Decimal("1000.00"))

    def test_the_note_can_still_be_edited(self):
        res = self._pay(self.alice, "500.00")
        res = self.client.patch(
            f"/api/payments/{res.data['id']}/", {"note": "FNB ref 8812"}, format="json"
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["note"], "FNB ref 8812")
