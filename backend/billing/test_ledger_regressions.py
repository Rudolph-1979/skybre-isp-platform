"""The paths the first version of the ledger fix got wrong.

Three defects, all found by an adversarial review of the commits that
introduced the ledger, all reproduced before being fixed. They share a
shape: the original tests covered the direction the author was thinking
about and not its opposite.

  1. A legacy invoice (balance_debited left False by migration 0019) was
     RE-DEBITED on any update, because False means "not yet debited" and
     apply_balance_debit runs on every save, not only a status change.
     The migration's own reasoning only considered the release direction.
     Fixed by making pre-existing rows NULL, which is genuinely inert.
  2. Deleting an invoice released its debit but left its PAYMENTS
     crediting the balance (Payment.invoice is SET_NULL), so the customer
     ended up in credit for money they owed. The original test deleted an
     invoice with no payments.
  3. `draft` sat outside DEBITED_STATUSES but was refused by neither the
     payment validator nor the status-transition validator, so the
     original drift bug survived one status value away.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from billing.models import Invoice, InvoiceItem, Payment, Tariff
from customers.models import Customer

User = get_user_model()


class _Base(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff = User.objects.create_user(
            username="finance3", password="pw-for-tests", role=User.Role.ACCOUNTS
        )
        self.client.force_authenticate(self.staff)
        self.customer = Customer.objects.create(
            full_name="Regression Rita", email="rita-r@example.com", balance=Decimal("0.00")
        )

    def _create(self, total="1000.00", status="unpaid"):
        return self.client.post(
            "/api/invoices/",
            {
                "customer": self.customer.id, "status": status, "date_due": "2026-09-30",
                "items": [{
                    "description": "Service", "quantity": 1,
                    "unit_price": total, "tax_rate_pct": "0.00",
                }],
            },
            format="json",
        )

    def _balance(self):
        self.customer.refresh_from_db()
        return self.customer.balance

    def _legacy_invoice(self, total="1000.00", status=Invoice.Status.UNPAID):
        """An invoice as it exists on the live database before this
        feature: the old recurring code put its total into the balance,
        and the flag is NULL because nothing can tell after the fact
        whether that happened."""
        invoice = Invoice.objects.create(
            customer=self.customer, status=status, date_due="2026-09-30"
        )
        InvoiceItem.objects.create(
            invoice=invoice, description="Legacy", quantity=1,
            unit_price=Decimal(total), tax_rate_pct=Decimal("0.00"),
        )
        invoice.recalc_totals()
        # What migration 0020 leaves behind, and the old debit it reflects.
        Invoice.objects.filter(pk=invoice.pk).update(balance_debited=None)
        Customer.objects.filter(pk=self.customer.pk).update(balance=Decimal(total))
        invoice.refresh_from_db()
        return invoice


class LegacyInvoicesAreInertTests(_Base):
    """Defect 1. Every one of these used to move the balance."""

    def test_patching_a_legacy_invoice_does_not_re_debit_it(self):
        invoice = self._legacy_invoice("1000.00")
        res = self.client.patch(
            f"/api/invoices/{invoice.pk}/", {"note": "phoned the customer"}, format="json"
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(self._balance(), Decimal("1000.00"))

    def test_marking_a_legacy_invoice_overdue_does_not_re_debit_it(self):
        """The workflow the commit message itself advertises as the reason
        `status` stays writable."""
        invoice = self._legacy_invoice("1000.00")
        self.client.patch(f"/api/invoices/{invoice.pk}/", {"status": "overdue"}, format="json")
        self.assertEqual(self._balance(), Decimal("1000.00"))

    def test_paying_a_legacy_invoice_credits_once(self):
        invoice = self._legacy_invoice("1000.00")
        res = self.client.post(
            "/api/payments/",
            {"customer": self.customer.id, "invoice": invoice.pk,
             "amount": "1000.00", "method": "bank_transfer"},
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(self._balance(), Decimal("0.00"))

    def test_cancelling_a_legacy_invoice_credits_nothing(self):
        """Inert in both directions -- the balance already reflects
        whatever the old code did, and this cannot tell which."""
        invoice = self._legacy_invoice("1000.00")
        self.client.patch(f"/api/invoices/{invoice.pk}/", {"status": "cancelled"}, format="json")
        self.assertEqual(self._balance(), Decimal("1000.00"))

    def test_deleting_a_legacy_invoice_credits_nothing(self):
        invoice = self._legacy_invoice("1000.00")
        res = self.client.delete(f"/api/invoices/{invoice.pk}/")
        self.assertEqual(res.status_code, 204)
        self.assertEqual(self._balance(), Decimal("1000.00"))

    def test_a_new_invoice_is_still_managed_normally(self):
        """The fix must not make the feature inert for everything."""
        self._create("500.00")
        self.assertEqual(self._balance(), Decimal("500.00"))


class DeletingAPaidInvoiceTests(_Base):
    """Defect 2. Payment.invoice is SET_NULL, so the payments outlive the
    invoice with their credit still applied."""

    def test_an_invoice_with_a_payment_cannot_be_deleted(self):
        invoice_id = self._create("1000.00").data["id"]
        self.client.post(
            "/api/payments/",
            {"customer": self.customer.id, "invoice": invoice_id,
             "amount": "1000.00", "method": "bank_transfer"},
            format="json",
        )
        self.assertEqual(self._balance(), Decimal("0.00"))

        res = self.client.delete(f"/api/invoices/{invoice_id}/")
        self.assertEqual(res.status_code, 400)
        self.assertTrue(Invoice.objects.filter(pk=invoice_id).exists())
        self.assertEqual(self._balance(), Decimal("0.00"))

    def test_a_part_paid_invoice_cannot_be_deleted_either(self):
        invoice_id = self._create("1000.00").data["id"]
        self.client.post(
            "/api/payments/",
            {"customer": self.customer.id, "invoice": invoice_id,
             "amount": "400.00", "method": "bank_transfer"},
            format="json",
        )
        res = self.client.delete(f"/api/invoices/{invoice_id}/")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self._balance(), Decimal("600.00"))

    def test_the_refusal_names_the_amount_and_the_way_out(self):
        invoice_id = self._create("1000.00").data["id"]
        self.client.post(
            "/api/payments/",
            {"customer": self.customer.id, "invoice": invoice_id,
             "amount": "1000.00", "method": "bank_transfer"},
            format="json",
        )
        res = self.client.delete(f"/api/invoices/{invoice_id}/")
        detail = str(res.data)
        self.assertIn("1 payment", detail)
        self.assertIn("Reverse the payment", detail)

    def test_reversing_the_payment_then_deleting_works_and_balances(self):
        """The documented route out, end to end."""
        invoice_id = self._create("1000.00").data["id"]
        payment = self.client.post(
            "/api/payments/",
            {"customer": self.customer.id, "invoice": invoice_id,
             "amount": "1000.00", "method": "bank_transfer"},
            format="json",
        ).data
        self.client.delete(f"/api/payments/{payment['id']}/")
        self.assertEqual(self._balance(), Decimal("1000.00"))
        res = self.client.delete(f"/api/invoices/{invoice_id}/")
        self.assertEqual(res.status_code, 204, getattr(res, "data", None))
        self.assertEqual(self._balance(), Decimal("0.00"))
        self.assertEqual(Payment.objects.filter(customer=self.customer).count(), 0)

    def test_an_unpaid_invoice_is_still_deletable(self):
        invoice_id = self._create("1000.00").data["id"]
        res = self.client.delete(f"/api/invoices/{invoice_id}/")
        self.assertEqual(res.status_code, 204)
        self.assertEqual(self._balance(), Decimal("0.00"))


class DraftIsNotALedgerHoleTests(_Base):
    """Defect 3. `draft` is outside DEBITED_STATUSES, so a payment against
    one reproduced the original drift exactly, and reverting an issued
    invoice to draft wrote the debt off."""

    def test_a_draft_invoice_cannot_be_paid(self):
        invoice_id = self._create("1000.00", status="draft").data["id"]
        self.assertEqual(self._balance(), Decimal("0.00"))
        res = self.client.post(
            "/api/payments/",
            {"customer": self.customer.id, "invoice": invoice_id,
             "amount": "1000.00", "method": "bank_transfer"},
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self._balance(), Decimal("0.00"))

    def test_an_issued_invoice_cannot_be_reverted_to_draft(self):
        invoice_id = self._create("1000.00").data["id"]
        self.assertEqual(self._balance(), Decimal("1000.00"))
        res = self.client.patch(
            f"/api/invoices/{invoice_id}/", {"status": "draft"}, format="json"
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self._balance(), Decimal("1000.00"))
        self.assertEqual(Invoice.objects.get(pk=invoice_id).status, Invoice.Status.UNPAID)

    def test_a_draft_can_still_be_issued(self):
        invoice_id = self._create("1000.00", status="draft").data["id"]
        res = self.client.patch(f"/api/invoices/{invoice_id}/", {"status": "unpaid"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(self._balance(), Decimal("1000.00"))
