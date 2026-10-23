"""Imported credit notes settle the invoices they cancelled (6 Oct 2026).

Dawid Griesel (SWF2581): every month Splynx invoiced him and credited the
same amount on the same day. Both came over -- the credit note as a negative
invoice marked Paid -- but nothing set one against the other, so his balance
was R0.00 while 24 invoices read Unpaid on the dashboard, his statement and
the Conecto app."""
import datetime
from decimal import Decimal

from django.test import TestCase

from customers.models import Customer

from .allocation import sweep_customer
from .models import CreditNoteAllocation, Invoice

D = Decimal


class ImportedCreditNoteTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(full_name="Dawid Griesel")

    def _inv(self, number, total, due, status="unpaid", paid=D("0")):
        inv = Invoice.objects.create(customer=self.customer, status=status, date_due=due)
        Invoice.objects.filter(pk=inv.pk).update(number=number, total=total, paid_amount=paid)
        return Invoice.objects.get(pk=inv.pk)

    def _month(self, n, amount=D("286.35")):
        due = datetime.date(2026, n, 1)
        inv = self._inv(f"TXN{n}0", amount, due)
        self._inv(f"TXN{n}1", -amount, due, status="paid", paid=-amount)
        return inv

    def _balance(self, value):
        Customer.objects.filter(pk=self.customer.pk).update(balance=value)

    def test_credit_notes_settle_the_invoices_when_the_balance_says_nothing_is_owed(self):
        invoices = [self._month(n) for n in (7, 8, 9)]
        self._balance(D("0.00"))
        sweep_customer(self.customer.pk)
        for inv in invoices:
            inv.refresh_from_db()
            self.assertEqual((inv.status, inv.paid_amount), ("paid", D("286.35")))
        self.assertEqual(CreditNoteAllocation.objects.count(), 3)
        # Idempotent.
        sweep_customer(self.customer.pk)
        self.assertEqual(CreditNoteAllocation.objects.count(), 3)

    def test_only_up_to_what_the_balance_proves(self):
        july, aug = self._month(7), self._month(8)
        self._balance(D("286.35"))  # one month genuinely owed
        sweep_customer(self.customer.pk)
        july.refresh_from_db()
        aug.refresh_from_db()
        self.assertEqual(july.status, "paid")
        self.assertEqual(aug.status, "unpaid")

    def test_a_client_who_owes_it_all_is_left_alone(self):
        inv = self._month(7)
        Invoice.objects.filter(total__lt=0).delete()
        self._balance(D("286.35"))
        sweep_customer(self.customer.pk)
        inv.refresh_from_db()
        self.assertEqual(inv.status, "unpaid")

    def test_deleting_the_credit_note_takes_it_back(self):
        inv = self._month(7)
        self._balance(D("0.00"))
        sweep_customer(self.customer.pk)
        note = Invoice.objects.get(total__lt=0)
        note.release_balance_debit()
        note.delete()
        inv.refresh_from_db()
        self.assertEqual((inv.status, inv.paid_amount), ("unpaid", D("0.00")))

    def test_platform_credit_notes_are_not_used(self):
        inv = self._month(7)
        Invoice.objects.filter(total__lt=0).update(number="CN-000001")
        self._balance(D("0.00"))
        sweep_customer(self.customer.pk)
        inv.refresh_from_db()
        self.assertEqual(inv.status, "unpaid")
