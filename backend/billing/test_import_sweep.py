"""Imported payments settle open invoices once they land (12 Oct 2026).

Four clients read "Overdue" with a credit balance: each had a payment
brought in by the Splynx transaction-report import AFTER the platform had
raised that client's invoice, and nothing applied it. The import now runs
the allocation sweep for the client it imported, and allocate_payments
recognises transaction-report payments as imported.
"""
import datetime
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from billing.models import Invoice, Payment
from billing.transaction_import import commit
from customers.models import Customer

HEADER = "#,ID,Transaction date,Debit,Credit,Account balance,Description,Category,Type\n"


def _csv(rows):
    return (HEADER + "\n".join(",".join(str(c) for c in r) for r in rows)).encode("utf-8")


class ImportSweepTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(full_name="Thami Guzana", email="thami@example.com",
                                                customer_id="SR2547")
        self.invoice = Invoice.objects.create(customer=self.customer, status=Invoice.Status.UNPAID,
                                              date_due=datetime.date(2026, 10, 8),
                                              date_issued=datetime.date(2026, 10, 8),
                                              total=Decimal("729.55"))
        self.invoice.apply_balance_debit()
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal("729.55"))

    def _import_payment(self):
        rows = [(1, 97391, "05/10/2026", "", "R811.10", "-R811.10", "FNB", "Payment", "Credit")]
        with self.captureOnCommitCallbacks(execute=True):
            result = commit(self.customer, _csv(rows))
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["written"]["payments"], 1)

    def test_an_imported_payment_settles_the_open_invoice(self):
        self._import_payment()
        self.invoice.refresh_from_db()
        self.customer.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.Status.PAID)
        self.assertEqual(self.invoice.paid_amount, Decimal("729.55"))
        self.assertEqual(self.customer.balance, Decimal("-81.55"))          # the rest stays as credit

    def test_allocate_payments_treats_transaction_report_payments_as_imported(self):
        payment = Payment.objects.create(customer=self.customer, amount=Decimal("100.00"),
                                         note="Imported from previous system (transaction report): 1 — FNB")
        out = StringIO()
        call_command("allocate_payments", "--client-code", "SR2547", stdout=out)
        self.assertIn("imported payment(s) skipped", out.getvalue())
        out = StringIO()
        call_command("allocate_payments", "--client-code", "SR2547", "--include-imported", stdout=out)
        self.assertIn(f"payment #{payment.pk}", out.getvalue())
