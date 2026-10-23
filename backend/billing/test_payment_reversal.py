"""Reversing a payment: POST /api/payments/<id>/reverse/.

The route out when bank-feed money was confirmed to the wrong customer.
It must put everything back -- balance, invoice allocations, the bank
transaction's place in the review queue -- record why, and only be
available to Accounts, Management and Admin.
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.testing import ALL_SECTIONS
from audit.models import AuditEvent
from bankfeeds.models import BankAccount, BankTransaction
from billing.models import Invoice, InvoiceItem, Payment
from customers.models import Customer

User = get_user_model()


class PaymentReversalTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.accounts = self._user("acc1", User.Role.ACCOUNTS)
        self.client.force_authenticate(self.accounts)
        self.wrong = Customer.objects.create(
            full_name="Christo Wrong", email="christo@example.com", balance=Decimal("500.00")
        )
        self.right = Customer.objects.create(
            full_name="Rita Right", email="rita@example.com", balance=Decimal("500.00")
        )
        self.invoice = self._invoice(self.wrong, "500.00")

    # -- helpers -----------------------------------------------------------

    def _user(self, username, role):
        return User.objects.create_user(
            username=username, password="pw-for-tests", role=role, allowed_sections=ALL_SECTIONS,
        )

    def _invoice(self, customer, total):
        invoice = Invoice.objects.create(customer=customer, status=Invoice.Status.UNPAID, date_due="2026-09-01")
        InvoiceItem.objects.create(
            invoice=invoice, description="Monthly service", quantity=1,
            unit_price=Decimal(total), tax_rate_pct=Decimal("0.00"),
        )
        invoice.recalc_totals()
        invoice.refresh_from_db()
        return invoice

    def _bank_payment(self, customer, amount="500.00"):
        """A payment confirmed from a bank feed, auto-allocated the way the
        live signal does it (on commit)."""
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.post(
                "/api/payments/",
                {"customer": customer.pk, "amount": amount, "method": "bank_transfer"},
                format="json",
            )
        self.assertEqual(res.status_code, 201, res.data)
        payment = Payment.objects.get(pk=res.data["id"])
        account = BankAccount.objects.create(name="FNB Cheque")
        txn = BankTransaction.objects.create(
            account=account, source="csv_import", external_id=f"x-{payment.pk}", date=date(2026, 8, 21),
            description="ULTIMATE SW 1381", amount=Decimal(amount),
            status=BankTransaction.Status.CONFIRMED, matched_customer=customer,
            match_method=BankTransaction.MatchMethod.MANUAL, created_payment=payment,
        )
        return payment, txn

    def _balance(self, customer):
        return Customer.objects.get(pk=customer.pk).balance

    # -- the reversal itself ---------------------------------------------

    def test_reversal_restores_balance_invoice_and_review_queue(self):
        payment, txn = self._bank_payment(self.wrong)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.Status.PAID)  # auto-allocation settled it
        self.assertEqual(self._balance(self.wrong), Decimal("0.00"))

        res = self.client.post(
            f"/api/payments/{payment.pk}/reverse/", {"reason": "Wrong customer, belongs to Rita"}, format="json"
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["bank_transaction"], txn.pk)

        self.assertFalse(Payment.objects.filter(pk=payment.pk).exists())
        self.assertEqual(self._balance(self.wrong), Decimal("500.00"))
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid_amount, Decimal("0.00"))
        self.assertEqual(self.invoice.status, Invoice.Status.UNPAID)
        txn.refresh_from_db()
        self.assertEqual(txn.status, BankTransaction.Status.MATCHED)
        self.assertIsNone(txn.created_payment_id)
        self.assertEqual(txn.matched_customer_id, self.wrong.pk)

    def test_reason_is_on_the_audit_trail_for_that_customer(self):
        payment, _ = self._bank_payment(self.wrong)
        self.client.post(
            f"/api/payments/{payment.pk}/reverse/", {"reason": "Wrong customer, belongs to Rita"}, format="json"
        )
        event = AuditEvent.objects.get(target_type="billing.Payment", target_id=str(payment.pk), action="deleted")
        self.assertIn("Wrong customer, belongs to Rita", event.detail)
        self.assertEqual(event.customer_id, self.wrong.pk)
        self.assertEqual(event.actor_id, self.accounts.pk)

    def test_reassign_and_confirm_afterwards_lands_on_the_right_customer(self):
        payment, txn = self._bank_payment(self.wrong)
        self.client.post(f"/api/payments/{payment.pk}/reverse/", {"reason": "Wrong customer"}, format="json")
        res = self.client.post(
            f"/api/bank-transactions/{txn.pk}/assign/", {"customer": self.right.pk}, format="json"
        )
        self.assertEqual(res.status_code, 200, res.data)
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.post(f"/api/bank-transactions/{txn.pk}/confirm/", {}, format="json")
        self.assertIn(res.status_code, (200, 201), res.data)
        self.assertEqual(self._balance(self.right), Decimal("0.00"))
        self.assertEqual(self._balance(self.wrong), Decimal("500.00"))
        txn.refresh_from_db()
        self.assertEqual(txn.status, BankTransaction.Status.CONFIRMED)
        self.assertEqual(Payment.objects.get(pk=txn.created_payment_id).customer_id, self.right.pk)

    def test_payment_recorded_against_an_invoice_reverses_that_invoice(self):
        res = self.client.post(
            "/api/payments/",
            {"customer": self.wrong.pk, "invoice": self.invoice.pk, "amount": "500.00", "method": "cash"},
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        res = self.client.post(f"/api/payments/{res.data['id']}/reverse/", {"reason": "Captured twice"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertIsNone(res.data["bank_transaction"])
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid_amount, Decimal("0.00"))
        self.assertEqual(self.invoice.status, Invoice.Status.UNPAID)
        self.assertEqual(self._balance(self.wrong), Decimal("500.00"))

    # -- refusals -----------------------------------------------------------

    def test_reason_is_required(self):
        payment, _ = self._bank_payment(self.wrong)
        for body in ({}, {"reason": ""}, {"reason": "   "}, {"reason": "abc"}):
            res = self.client.post(f"/api/payments/{payment.pk}/reverse/", body, format="json")
            self.assertEqual(res.status_code, 400, body)
        self.assertTrue(Payment.objects.filter(pk=payment.pk).exists())
        self.assertEqual(self._balance(self.wrong), Decimal("0.00"))

    def test_reversing_twice_is_a_404_not_a_second_credit(self):
        payment, _ = self._bank_payment(self.wrong)
        first = self.client.post(f"/api/payments/{payment.pk}/reverse/", {"reason": "Wrong customer"}, format="json")
        second = self.client.post(f"/api/payments/{payment.pk}/reverse/", {"reason": "Wrong customer"}, format="json")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 404)
        self.assertEqual(self._balance(self.wrong), Decimal("500.00"))

    def test_management_and_admin_may_reverse(self):
        for role in (User.Role.MANAGEMENT, User.Role.ADMIN):
            payment, _ = self._bank_payment(self.wrong)
            self.client.force_authenticate(self._user(f"u-{role}", role))
            res = self.client.post(f"/api/payments/{payment.pk}/reverse/", {"reason": "Wrong customer"}, format="json")
            self.assertEqual(res.status_code, 200, (role, res.data))
            self.client.force_authenticate(self.accounts)

    def test_other_staff_roles_may_not_reverse_or_delete(self):
        payment, _ = self._bank_payment(self.wrong)
        for role in (User.Role.SUPPORT, User.Role.SALES, User.Role.TECHNICIAN):
            self.client.force_authenticate(self._user(f"u-{role}", role))
            res = self.client.post(f"/api/payments/{payment.pk}/reverse/", {"reason": "Wrong customer"}, format="json")
            self.assertEqual(res.status_code, 403, role)
            res = self.client.delete(f"/api/payments/{payment.pk}/")
            self.assertEqual(res.status_code, 403, role)
        self.assertTrue(Payment.objects.filter(pk=payment.pk).exists())
        self.assertEqual(self._balance(self.wrong), Decimal("0.00"))
