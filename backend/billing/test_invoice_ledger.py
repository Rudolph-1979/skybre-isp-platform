"""What an invoice must do to Customer.balance, and what its number must
never do.

Two defects, both about an invoice's effects outliving the row.

THE LEDGER. Only recurring._generate_document ever increased a balance,
with a comment saying manually-created invoices were deliberately
unaffected -- while PaymentSerializer.create decreased it for ANY
payment. So the two halves disagreed for every invoice raised by hand,
which is the path the shipped UI uses: raise R1,000, balance unchanged;
customer pays R1,000, balance -R1,000. The customer's portal, statement
PDF and emails then showed credit that did not exist, and
blocking_candidate_services' `balance <= minimum_balance` test exempted
them from suspension permanently. Deleting or cancelling an invoice
drifted it the other way, leaving a debit with nothing to explain it.

THE NUMBER. _next_number_for_status took one past the highest number on
an existing row, and destroy() permits hard-deleting a real invoice --
so deleting the newest one handed its number to the next invoice created,
while the first customer still held a PDF for it.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from billing.models import Invoice, InvoiceItem, IssuedNumberHighWater, Tariff
from customers.models import Customer

User = get_user_model()


class _InvoiceApiTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff = User.objects.create_user(
            username="finance2", password="pw-for-tests", role=User.Role.ACCOUNTS
        )
        self.client.force_authenticate(self.staff)
        self.customer = Customer.objects.create(
            full_name="Ledger Len", email="len@example.com", balance=Decimal("0.00")
        )

    def _create(self, total="1000.00", status="unpaid", customer=None):
        res = self.client.post(
            "/api/invoices/",
            {
                "customer": (customer or self.customer).id,
                "status": status,
                "date_due": "2026-09-30",
                "items": [{
                    "description": "Monthly service", "quantity": 1,
                    "unit_price": total, "tax_rate_pct": "0.00",
                }],
            },
            format="json",
        )
        return res

    def _balance(self, customer=None):
        target = customer or self.customer
        target.refresh_from_db()
        return target.balance


class InvoiceBalanceDebitTests(_InvoiceApiTestCase):
    def test_a_manually_raised_invoice_debits_the_balance(self):
        """The core of the drift. This is what never happened before."""
        res = self._create("1000.00")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(self._balance(), Decimal("1000.00"))
        self.assertTrue(Invoice.objects.get(pk=res.data["id"]).balance_debited)

    def test_raising_and_paying_an_invoice_nets_to_zero(self):
        """The end-to-end symptom: the customer used to finish this
        sequence showing R1,000 of credit they never had."""
        invoice_id = self._create("1000.00").data["id"]
        self.assertEqual(self._balance(), Decimal("1000.00"))
        res = self.client.post(
            "/api/payments/",
            {"customer": self.customer.id, "invoice": invoice_id,
             "amount": "1000.00", "method": "bank_transfer"},
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(self._balance(), Decimal("0.00"))

    def test_a_quote_does_not_debit_the_balance(self):
        self._create("1000.00", status="quote")
        self.assertEqual(self._balance(), Decimal("0.00"))

    def test_a_proforma_does_not_debit_the_balance(self):
        self._create("1000.00", status="proforma")
        self.assertEqual(self._balance(), Decimal("0.00"))

    def test_a_draft_does_not_debit_the_balance(self):
        """A draft is not issued yet."""
        self._create("1000.00", status="draft")
        self.assertEqual(self._balance(), Decimal("0.00"))

    def test_issuing_a_draft_debits_it(self):
        invoice_id = self._create("750.00", status="draft").data["id"]
        self.assertEqual(self._balance(), Decimal("0.00"))
        res = self.client.patch(f"/api/invoices/{invoice_id}/", {"status": "unpaid"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(self._balance(), Decimal("750.00"))

    def test_converting_a_quote_to_an_invoice_debits_it(self):
        invoice_id = self._create("500.00", status="quote").data["id"]
        self.assertEqual(self._balance(), Decimal("0.00"))
        res = self.client.post(f"/api/invoices/{invoice_id}/convert-to-invoice/")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(self._balance(), Decimal("500.00"))

    def test_cancelling_an_invoice_reverses_the_debit(self):
        invoice_id = self._create("575.00").data["id"]
        self.assertEqual(self._balance(), Decimal("575.00"))
        res = self.client.patch(
            f"/api/invoices/{invoice_id}/", {"status": "cancelled"}, format="json"
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(self._balance(), Decimal("0.00"))
        self.assertFalse(Invoice.objects.get(pk=invoice_id).balance_debited)

    def test_deleting_an_invoice_reverses_the_debit(self):
        """Otherwise the next invoice that goes overdue clears the
        minimum_balance cushion and suspends a line over money the
        customer never owed."""
        invoice_id = self._create("575.00").data["id"]
        res = self.client.delete(f"/api/invoices/{invoice_id}/")
        self.assertEqual(res.status_code, 204)
        self.assertEqual(self._balance(), Decimal("0.00"))

    def test_an_overdue_invoice_stays_debited(self):
        invoice_id = self._create("300.00").data["id"]
        self.client.patch(f"/api/invoices/{invoice_id}/", {"status": "overdue"}, format="json")
        self.assertEqual(self._balance(), Decimal("300.00"))

    def test_applying_the_debit_twice_does_not_double_it(self):
        """Idempotence matters because several paths call it and some of
        them can run over the same invoice more than once."""
        invoice = Invoice.objects.get(pk=self._create("400.00").data["id"])
        invoice.apply_balance_debit()
        invoice.apply_balance_debit()
        self.assertEqual(self._balance(), Decimal("400.00"))

    def test_releasing_a_debit_twice_does_not_double_credit(self):
        invoice = Invoice.objects.get(pk=self._create("400.00").data["id"])
        invoice.release_balance_debit()
        invoice.release_balance_debit()
        self.assertEqual(self._balance(), Decimal("0.00"))

    def test_cancelling_a_never_debited_invoice_credits_nothing(self):
        """The safe direction for historical rows: balance_debited is False
        on every pre-migration invoice, so reversing one must be a no-op
        rather than inventing a credit."""
        invoice = Invoice.objects.get(pk=self._create("900.00").data["id"])
        Invoice.objects.filter(pk=invoice.pk).update(balance_debited=False)
        Customer.objects.filter(pk=self.customer.pk).update(balance=Decimal("0.00"))
        self.client.delete(f"/api/invoices/{invoice.pk}/")
        self.assertEqual(self._balance(), Decimal("0.00"))


class InvoiceStatusTransitionTests(_InvoiceApiTestCase):
    def test_a_real_invoice_cannot_be_turned_back_into_a_quote(self):
        """Number laundering: this made can_convert_to_proforma() true
        again, and converting reissues from the QUO sequence, freeing the
        original INV number for reuse while the customer holds its PDF."""
        invoice_id = self._create("1000.00").data["id"]
        res = self.client.patch(f"/api/invoices/{invoice_id}/", {"status": "quote"}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(Invoice.objects.get(pk=invoice_id).status, Invoice.Status.UNPAID)

    def test_a_real_invoice_cannot_be_turned_back_into_a_proforma(self):
        invoice_id = self._create("1000.00").data["id"]
        res = self.client.patch(f"/api/invoices/{invoice_id}/", {"status": "proforma"}, format="json")
        self.assertEqual(res.status_code, 400)

    def test_a_quote_can_still_be_marked_cancelled(self):
        invoice_id = self._create("1000.00", status="quote").data["id"]
        res = self.client.patch(
            f"/api/invoices/{invoice_id}/", {"status": "cancelled"}, format="json"
        )
        self.assertEqual(res.status_code, 200, res.data)

    def test_an_issued_invoice_cannot_be_moved_to_another_customer(self):
        """It carries a debit against the customer it was issued to, and
        its line items' service FKs point at that customer's services."""
        other = Customer.objects.create(full_name="Someone Else", email="else@example.com")
        invoice_id = self._create("1000.00").data["id"]
        self.client.patch(f"/api/invoices/{invoice_id}/", {"customer": other.id}, format="json")
        self.assertEqual(Invoice.objects.get(pk=invoice_id).customer_id, self.customer.id)
        self.assertEqual(self._balance(other), Decimal("0.00"))
        self.assertEqual(self._balance(), Decimal("1000.00"))


class InvoiceNumberReuseTests(_InvoiceApiTestCase):
    def test_a_deleted_invoices_number_is_never_reissued(self):
        first = self._create("100.00").data
        deleted_number = first["number"]
        self.client.delete(f"/api/invoices/{first['id']}/")

        second = self._create("200.00").data
        self.assertNotEqual(second["number"], deleted_number)

    def test_numbering_stays_gapless_in_normal_use(self):
        """The high-water mark is bumped after a successful save, not
        before, so ordinary creates do not burn numbers."""
        numbers = [self._create("10.00").data["number"] for _ in range(4)]
        seqs = [int(n.rpartition("-")[2]) for n in numbers]
        self.assertEqual(seqs, list(range(seqs[0], seqs[0] + 4)))

    def test_the_high_water_mark_tracks_the_invoice_sequence(self):
        self._create("10.00")
        created = self._create("10.00").data
        mark = IssuedNumberHighWater.objects.get(prefix="INV")
        self.assertEqual(mark.last_seq, int(created["number"].rpartition("-")[2]))

    def test_quotes_keep_their_own_sequence(self):
        quote = self._create("10.00", status="quote").data
        invoice = self._create("10.00").data
        self.assertTrue(quote["number"].startswith("QUO-"))
        self.assertTrue(invoice["number"].startswith("INV-"))
        self.assertTrue(IssuedNumberHighWater.objects.filter(prefix="QUO").exists())
        self.assertTrue(IssuedNumberHighWater.objects.filter(prefix="INV").exists())

    def test_an_unparseable_legacy_number_does_not_break_the_sequence(self):
        """Same tolerance _next_number_for_status already had."""
        legacy = Invoice.objects.create(
            customer=self.customer, status=Invoice.Status.UNPAID, date_due="2026-09-30"
        )
        Invoice.objects.filter(pk=legacy.pk).update(number="OLD-SYSTEM-REF-7")
        res = self._create("10.00")
        self.assertEqual(res.status_code, 201, res.data)


class RecurringEngineStillDebitsTests(TestCase):
    """Regression guard: the engine's own debit moved from inline code
    into Invoice.apply_balance_debit, so it has to still happen."""

    def test_a_recurring_invoice_still_debits_the_balance(self):
        import datetime

        from django.utils import timezone

        from billing.models import CustomerBillingConfig, Service
        from billing.recurring import run_recurring_billing

        today = timezone.localdate()
        tariff = Tariff.objects.create(
            name="Home 20", price=Decimal("500.00"), tax_rate_pct=Decimal("0.00"),
            speed_download_kbps=20480, speed_upload_kbps=10240,
        )
        customer = Customer.objects.create(
            full_name="Recurring Rita", email="rita@example.com",
            status=Customer.Status.ACTIVE, balance=Decimal("0.00"),
        )
        Service.objects.create(
            customer=customer, tariff=tariff, status=Service.Status.ACTIVE,
            start_date=today - datetime.timedelta(days=90),
        )
        config = CustomerBillingConfig.for_customer(customer)
        config.billing_enabled = True
        config.next_billing_date = today
        config.save()

        run_recurring_billing(today, commit=True)
        customer.refresh_from_db()
        self.assertEqual(customer.balance, Decimal("500.00"))
        invoice = customer.invoices.get()
        self.assertTrue(invoice.balance_debited)

    def test_a_recurring_proforma_does_not_debit(self):
        import datetime

        from django.utils import timezone

        from billing.models import CustomerBillingConfig, Service
        from billing.recurring import run_recurring_billing

        today = timezone.localdate()
        tariff = Tariff.objects.create(
            name="Home 10", price=Decimal("300.00"), tax_rate_pct=Decimal("0.00"),
            speed_download_kbps=10240, speed_upload_kbps=5120,
        )
        customer = Customer.objects.create(
            full_name="Proforma Pete", email="pete@example.com",
            status=Customer.Status.ACTIVE, balance=Decimal("0.00"),
        )
        Service.objects.create(
            customer=customer, tariff=tariff, status=Service.Status.ACTIVE,
            start_date=today - datetime.timedelta(days=90),
        )
        config = CustomerBillingConfig.for_customer(customer)
        config.billing_enabled = True
        config.auto_proforma_enabled = True
        config.next_billing_date = today
        config.save()

        run_recurring_billing(today, commit=True)
        customer.refresh_from_db()
        self.assertEqual(customer.balance, Decimal("0.00"))
