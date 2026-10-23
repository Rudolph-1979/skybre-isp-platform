"""billing.ledger_import had zero test coverage, which is how a one-word
substring bug shipped and stayed unnoticed: `"paid" in status` matches not
only "Paid" but "Unpaid" and "Partially paid" too ("paid" is a literal
substring of both), so every imported invoice came in marked fully Paid
regardless of what its own Status column actually said.

Found by comparing a live customer's statement here against the same
customer's statement in the source system (Splynx): two invoices the
source correctly showed as Unpaid / Partially paid came across as Paid.
Invoice.status doesn't touch Customer.balance directly -- that's driven by
apply_balance_debit() on DEBITED_STATUSES regardless of Paid vs Unpaid, so
the account balance itself stayed right. What broke was everything that
trusts an invoice's OWN status/paid_amount: the "Paid" badge staff see, the
invoice's own balance_due, and -- the one with real money behind it --
billing.recurring's payment-reminder run, which only chases UNPAID/OVERDUE
invoices. A customer who genuinely still owed money silently stopped being
reminded about it.

The fix: prefer the source's own Due column (`paid = total - due`), which
needs no interpretation of what a status WORD means and is the only one of
the two that can express a partial payment at all. The Status word is read
only as a fallback for an older export with no Due column, and read as an
EXACT match against "paid" now, never a substring one.
"""
from decimal import Decimal

from django.test import TestCase

from billing.ledger_import import commit, plan
from billing.models import Invoice
from customers.models import Customer

HEADER = "Type,Number,Date,Total,Due,Status\n"


def _csv(*rows):
    return (HEADER + "\n".join(rows)).encode("utf-8")


class LedgerImportPaidAmountTests(TestCase):
    def _customer(self, name="Jayden Van Wyk", balance="0.00"):
        return Customer.objects.create(
            full_name=name, email=f"{name.split()[0].lower()}@example.com",
            balance=Decimal(balance),
        )

    def _invoice(self, number):
        return Invoice.objects.get(number=number)

    # ---- the regression itself -------------------------------------------

    def test_unpaid_status_word_is_not_treated_as_paid(self):
        """The exact bug: source says Unpaid, no Due column value given.
        `"paid" in "unpaid"` is True -- this used to import as fully Paid."""
        customer = self._customer()
        data = _csv("Recurring invoice,INV900001,01/10/2026,R465.75,,Unpaid")
        result = commit(customer, data)
        self.assertEqual(result["errors"], [])
        invoice = self._invoice("INV900001")
        self.assertEqual(invoice.status, Invoice.Status.UNPAID)
        self.assertEqual(invoice.paid_amount, Decimal("0.00"))

    def test_partially_paid_status_word_alone_is_not_treated_as_paid(self):
        """No Due column value to fall back on and no reliable way to read
        a partial amount out of the word itself, so this lands as fully
        Unpaid (paid_amount 0) rather than -- the old, dangerous
        direction -- fully Paid. Understating what's owed is corrected the
        next time the Due column is actually populated; overstating it
        hides a real debt from the reminder run."""
        customer = self._customer()
        data = _csv("Recurring invoice,INV900002,03/09/2026,R433.55,,Partially paid")
        result = commit(customer, data)
        self.assertEqual(result["errors"], [])
        invoice = self._invoice("INV900002")
        self.assertEqual(invoice.status, Invoice.Status.UNPAID)
        self.assertEqual(invoice.paid_amount, Decimal("0.00"))

    def test_paid_status_word_with_no_due_column_value_is_fully_paid(self):
        customer = self._customer()
        data = _csv("Recurring invoice,INV900003,01/09/2026,R764.75,,Paid")
        result = commit(customer, data)
        self.assertEqual(result["errors"], [])
        invoice = self._invoice("INV900003")
        self.assertEqual(invoice.status, Invoice.Status.PAID)
        self.assertEqual(invoice.paid_amount, invoice.total)

    # ---- the Due column, the actual fix ----------------------------------

    def test_due_column_gives_the_exact_partial_amount(self):
        """R433.55 total, R80.62 still due -- R352.93 already paid. This is
        the real row from the customer whose statement surfaced this bug."""
        customer = self._customer()
        data = _csv("Recurring invoice,INV900004,03/09/2026,R433.55,R80.62,Partially paid")
        result = commit(customer, data)
        self.assertEqual(result["errors"], [])
        invoice = self._invoice("INV900004")
        self.assertEqual(invoice.status, Invoice.Status.UNPAID)
        self.assertEqual(invoice.paid_amount, Decimal("352.93"))

    def test_due_column_zero_means_fully_paid_regardless_of_status_word(self):
        """The Due column is authoritative -- it wins even over a stale or
        contradictory Status word."""
        customer = self._customer()
        data = _csv("Recurring invoice,INV900005,01/07/2026,R187.22,R0.00,Unpaid")
        result = commit(customer, data)
        self.assertEqual(result["errors"], [])
        invoice = self._invoice("INV900005")
        self.assertEqual(invoice.status, Invoice.Status.PAID)
        self.assertEqual(invoice.paid_amount, invoice.total)

    def test_due_column_full_total_means_fully_unpaid(self):
        customer = self._customer()
        data = _csv("Recurring invoice,INV900006,01/10/2026,R465.75,R465.75,Unpaid")
        result = commit(customer, data)
        self.assertEqual(result["errors"], [])
        invoice = self._invoice("INV900006")
        self.assertEqual(invoice.status, Invoice.Status.UNPAID)
        self.assertEqual(invoice.paid_amount, Decimal("0.00"))

    def test_paid_amount_never_exceeds_total_even_on_bad_due_data(self):
        """A negative Due (a source data error -- "more than fully paid")
        must not leave paid_amount above the invoice's own total."""
        customer = self._customer()
        data = _csv("Recurring invoice,INV900007,01/09/2026,R100.00,-R5.00,Paid")
        result = commit(customer, data)
        self.assertEqual(result["errors"], [])
        invoice = self._invoice("INV900007")
        self.assertEqual(invoice.paid_amount, invoice.total)

    # ---- unaffected by the fix, confirmed unaffected ----------------------

    def test_credit_note_is_always_fully_settled(self):
        customer = self._customer()
        data = _csv("Credit note,CN900008,25/05/2026,R171.35,,Closed")
        result = commit(customer, data)
        self.assertEqual(result["errors"], [])
        invoice = self._invoice("CN900008")
        self.assertEqual(invoice.status, Invoice.Status.PAID)
        self.assertEqual(invoice.paid_amount, invoice.total)

    def test_balance_is_debited_the_full_total_regardless_of_paid_amount(self):
        """apply_balance_debit() must not care how much of the invoice is
        paid -- DEBITED_STATUSES includes PAID precisely because the debit
        stays and a payment/paid_amount offsets it separately. A partially-
        paid import should not silently shrink what the customer owes."""
        customer = self._customer(balance="0.00")
        data = _csv("Recurring invoice,INV900009,03/09/2026,R433.55,R80.62,Partially paid")
        commit(customer, data)
        customer.refresh_from_db()
        self.assertEqual(customer.balance, Decimal("433.55"))

    def test_rerun_is_idempotent(self):
        customer = self._customer()
        data = _csv("Recurring invoice,INV900010,01/09/2026,R764.75,,Paid")
        first = commit(customer, data)
        self.assertEqual(first["written"]["invoices"], 1)
        second = commit(customer, data)
        self.assertEqual(second["written"]["invoices"], 0)
        self.assertEqual(Invoice.objects.filter(number="INV900010").count(), 1)

    # ---- plan() previews the same figures before anything is written -----

    def test_plan_reports_the_same_paid_amount_it_will_commit(self):
        customer = self._customer()
        data = _csv("Recurring invoice,INV900011,03/09/2026,R433.55,R80.62,Partially paid")
        preview = plan(customer, data)
        self.assertEqual(preview["errors"], [])
        row = preview["rows"][0]
        self.assertEqual(row["action"], "create_invoice")
        self.assertEqual(Decimal(row["paid_amount"]), Decimal("352.93"))
        self.assertIn("Partially paid", row["message"])
        self.assertEqual(Invoice.objects.filter(number="INV900011").count(), 0)
