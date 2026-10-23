"""One allocation rule for every source of money (Rudolph, 5 Oct 2026):
"Payments should always be auto allocated to oldest invoice with a option
to allocate payments to specific invoices." Payments and approved credits
settle open invoices oldest-due-first whenever money or debt changes;
recording against a specific invoice pins it there first."""
import datetime
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.testing import ALL_SECTIONS
from customers.models import Customer

from .allocation import sweep_customer
from .models import CreditAllocation, CreditRequest, Invoice, Payment, PaymentAllocation
from .statements import IMPORT_NOTE_PREFIX

User = get_user_model()
D = Decimal


class UnifiedAllocationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.boss = User.objects.create_user(
            username="boss-ua", password="x", role=User.Role.MANAGEMENT, allowed_sections=ALL_SECTIONS,
        )
        self.client.force_authenticate(self.boss)
        self.customer = Customer.objects.create(full_name="Ann", email="a@x.com", customer_id="CUS-UA1")
        self.today = datetime.date.today()

    def _invoice(self, total, days_ago, status="unpaid"):
        inv = Invoice.objects.create(customer=self.customer, status=status,
                                     date_due=self.today - datetime.timedelta(days=days_ago))
        Invoice.objects.filter(pk=inv.pk).update(total=D(total))
        inv.refresh_from_db()
        return inv

    def _pay(self, amount, invoice=None):
        body = {"customer": self.customer.pk, "amount": amount, "method": "bank_transfer"}
        if invoice is not None:
            body["invoice"] = invoice.pk
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.post("/api/payments/", body, format="json")
        self.assertEqual(res.status_code, 201, res.content)
        return Payment.objects.get(pk=res.json()["id"])

    def _r(self, *objs):
        for o in objs:
            o.refresh_from_db()

    def test_a_payment_settles_the_oldest_invoice_first(self):
        old, new = self._invoice("100", 30), self._invoice("100", 1)
        self._pay("150")
        self._r(old, new)
        self.assertEqual((old.paid_amount, old.status), (D("100"), "paid"))
        self.assertEqual((new.paid_amount, new.status), (D("50"), "unpaid"))

    def test_recording_against_an_invoice_pins_it_and_the_rest_flows_oldest_first(self):
        old, new = self._invoice("100", 30), self._invoice("100", 1)
        payment = self._pay("130", invoice=new)
        self._r(old, new, payment)
        self.assertIsNone(payment.invoice_id)  # an allocation now, not a direct credit
        self.assertEqual(new.paid_amount, D("100"))
        self.assertEqual(old.paid_amount, D("30"))
        self.assertEqual(PaymentAllocation.objects.filter(payment=payment).count(), 2)

    def test_credit_already_on_account_settles_the_next_invoice_raised(self):
        self._pay("200")  # nothing owed yet: sits as credit
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.post("/api/invoices/", {
                "customer": self.customer.pk, "status": "unpaid", "date_due": str(self.today),
                "items": [{"description": "Plan", "quantity": "1", "unit_price": "100.00", "tax_rate_pct": "0"}],
            }, format="json")
        self.assertEqual(res.status_code, 201, res.content)
        invoice = Invoice.objects.get(number=res.json()["number"])
        self.assertEqual((invoice.paid_amount, invoice.status), (D("100.00"), "paid"))

    def test_cancelling_an_invoice_moves_its_money_to_the_next_one(self):
        first, second = self._invoice("100", 30), self._invoice("100", 1)
        first.balance_debited = True
        Invoice.objects.filter(pk=first.pk).update(balance_debited=True)
        self._pay("100")
        self._r(first)
        self.assertEqual(first.paid_amount, D("100"))
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.patch(f"/api/invoices/{first.pk}/", {"status": "cancelled"}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        self._r(first, second)
        self.assertEqual(first.paid_amount, D("0"))
        self.assertEqual((second.paid_amount, second.status), (D("100"), "paid"))

    def test_an_invoice_with_money_on_it_cannot_be_deleted(self):
        first = self._invoice("100", 30)
        self._pay("40")
        res = self.client.delete(f"/api/invoices/{first.pk}/")
        self.assertEqual(res.status_code, 400)
        self.assertIn("Cancel it instead", str(res.data))

    def test_deleting_an_unpaid_invoice_lets_freed_credit_settle_others(self):
        first, second = self._invoice("100", 30), self._invoice("100", 1)
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.delete(f"/api/invoices/{first.pk}/")
        self.assertEqual(res.status_code, 204)

    def _credit(self, amount, invoice=None):
        credit = CreditRequest.objects.create(customer=self.customer, amount=D(amount), reason="x", invoice=invoice)
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.post(f"/api/credit-requests/{credit.pk}/approve/")
        self.assertEqual(res.status_code, 200, res.data)
        credit.refresh_from_db()
        return credit

    def test_an_unlinked_credit_settles_the_oldest_invoice(self):
        old, new = self._invoice("100", 30), self._invoice("100", 1)
        credit = self._credit("100")
        self._r(old, new)
        self.assertEqual(old.status, "paid")
        self.assertEqual(new.paid_amount, D("0"))
        self.assertTrue(credit.allocation_tracked)
        self.assertEqual(CreditAllocation.objects.get(credit=credit).invoice, old)

    def test_a_linked_credit_settles_its_invoice_first_then_oldest(self):
        old, new = self._invoice("100", 30), self._invoice("100", 1)
        self._credit("150", invoice=new)
        self._r(old, new)
        self.assertEqual(new.paid_amount, D("100"))
        self.assertEqual(old.paid_amount, D("50"))

    def test_deleting_the_credit_note_reverses_what_it_settled(self):
        old = self._invoice("100", 30)
        Customer.objects.filter(pk=self.customer.pk).update(balance=D("100"))
        credit = self._credit("100")
        self._r(old)
        self.assertEqual(old.status, "paid")
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.delete(f"/api/invoices/{credit.credit_note_id}/")
        self.assertEqual(res.status_code, 204)
        self._r(old, credit)
        self.assertEqual((old.paid_amount, old.status), (D("0"), "unpaid"))
        self.assertEqual(credit.status, CreditRequest.Status.REVERSED)
        self.assertEqual(Customer.objects.get(pk=self.customer.pk).balance, D("100"))

    def test_an_invoice_cannot_be_marked_paid_by_hand(self):
        inv = self._invoice("100", 1)
        res = self.client.patch(f"/api/invoices/{inv.pk}/", {"status": "paid"}, format="json")
        self.assertEqual(res.status_code, 400, res.content)

    def test_imported_payments_are_never_applied_in_full(self):
        inv = self._invoice("100", 1)
        for note in (f"{IMPORT_NOTE_PREFIX}INV1", "Imported from previous system (transaction report): 9"):
            Payment.objects.create(customer=self.customer, amount=D("100"), note=note)
        Customer.objects.filter(pk=self.customer.pk).update(balance=D("100"))  # still owes it
        sweep_customer(self.customer.pk)
        self._r(inv)
        self.assertEqual(inv.paid_amount, D("0"))

    def test_imported_money_settles_only_what_the_balance_proves_is_covered(self):
        """CCTV Central: balance R0.00 but its first new invoice R435.85
        Unpaid -- the imported prepayment never reached the invoice."""
        inv = self._invoice("435.85", 4)
        Payment.objects.create(customer=self.customer, amount=D("3958.46"), note=f"{IMPORT_NOTE_PREFIX}INV9")
        Customer.objects.filter(pk=self.customer.pk).update(balance=D("0"))
        sweep_customer(self.customer.pk)
        self._r(inv)
        self.assertEqual((inv.paid_amount, inv.status), (D("435.85"), "paid"))

    def test_legacy_credits_are_not_swept(self):
        inv = self._invoice("100", 1)
        CreditRequest.objects.create(customer=self.customer, amount=D("100"), reason="old",
                                     status="approved", allocation_tracked=False)
        sweep_customer(self.customer.pk)
        self._r(inv)
        self.assertEqual(inv.paid_amount, D("0"))

    def test_a_legacy_direct_payment_cannot_be_reallocated(self):
        inv = self._invoice("100", 1)
        legacy = Payment.objects.create(customer=self.customer, amount=D("50"), invoice=inv)
        res = self.client.post(f"/api/payments/{legacy.pk}/allocations/", {"auto": True}, format="json")
        self.assertEqual(res.status_code, 400, res.content)

    def test_sweep_command_dry_run_changes_nothing(self):
        inv = self._invoice("100", 1)
        p = Payment.objects.create(customer=self.customer, amount=D("100"))  # never allocated (no on_commit)
        out = StringIO()
        call_command("allocation_sweep", "--client-code", "CUS-UA1", stdout=out)
        self.assertIn("Would change 1", out.getvalue())
        self._r(inv)
        self.assertEqual(inv.paid_amount, D("0"))
        call_command("allocation_sweep", "--client-code", "CUS-UA1", "--commit", stdout=StringIO())
        self._r(inv)
        self.assertEqual(inv.status, "paid")
        self.assertEqual(PaymentAllocation.objects.get(payment=p).amount, D("100"))
