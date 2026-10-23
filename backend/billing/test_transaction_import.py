"""billing.transaction_import — the Splynx Transaction Report importer.

See that module's own docstring for the full "why": billing.ledger_import
reads Splynx's per-customer invoice ledger, which is incomplete — some
settlements (a salary deduction, on the account this was built for) never
appear as an importable "Payment" row at all, only as an invoice's own
Status="Paid" word with no recoverable amount. The Transaction Report is
the raw ledger instead: every individual debit/credit with its own running
Account balance, so the whole file can be checked against itself.

The golden test below (TransactionReportGoldenImportTests) is the real
21-row file pulled for Jayden Van Wyk (SMF883) while diagnosing exactly
that gap — her ledger-import balance (R1,326.99) didn't match Splynx's own
live balance (R546.37) for the same account, by exactly R780.62: one
"Deducted from Salary" transaction, categorised in Splynx as ordinary
Service rather than Payment. Every number asserted below (6 invoices, 3
payments — not 2 — 2 credit notes, final balance R546.37) was independently
hand-verified against the file's own running-balance chain before this
test was written; see the module docstring for the reconciliation this
number is provably tied to.
"""
from decimal import Decimal

from django.test import TestCase

from billing.models import Invoice, Payment
from billing.transaction_import import _classify, commit, plan
from customers.models import Customer

HEADER = "#,ID,Transaction date,Debit,Credit,Account balance,Description,Category,Type\n"

# The real file, transcribed exactly as Splynx exported it (newest groups
# first, but NOT simply reverse-chronological within that — #19-21, then
# #15-18, then #12-14, etc.) specifically so _chronological()'s reliance on
# the "#" column, not file order, is actually exercised rather than
# incidentally correct.
JAYDEN_ROWS = [
    (19, 95301, "01/10/2026", "R619.85", "", "R700.47", "MTD 50Mbps", "MTD 50/50", "Debit"),
    (20, 95302, "01/10/2026", "", "R171.35", "R529.12", "Discount for MTD 50Mbps", "MTD 50/50", "Credit"),
    (21, 95303, "01/10/2026", "R17.25", "", "R546.37", "Public IP Rental", "Service", "Debit"),
    (15, 92203, "03/09/2026", "", "R697.68", "R67.07", "Skybre Wireless 10Mbps", "SW 10/10", "Credit"),
    (16, 92206, "03/09/2026", "R599.19", "", "R666.26", "MTD 50Mbps", "MTD 50/50", "Debit"),
    (17, 92207, "03/09/2026", "", "R165.64", "R500.62", "Discount for MTD 50Mbps", "MTD 50/50", "Credit"),
    (18, 92281, "03/09/2026", "", "R420.00", "R80.62", "FNB", "Payment", "Credit"),
    (12, 90672, "01/09/2026", "R918.85", "", "R918.85", "Skybre Wireless 10Mbps", "SW 10/10", "Debit"),
    (13, 90673, "01/09/2026", "", "R171.35", "R747.50", "Discount for Skybre Wireless 10Mbps", "SW 10/10", "Credit"),
    (14, 90674, "01/09/2026", "R17.25", "", "R764.75", "Public IP Rental", "Service", "Debit"),
    (9, 86231, "01/08/2026", "R918.85", "", "R154.10", "Skybre Wireless 10Mbps", "SW 10/10", "Debit"),
    (10, 86232, "01/08/2026", "", "R171.35", "-R17.25", "Discount for Skybre Wireless 10Mbps", "SW 10/10", "Credit"),
    (11, 86233, "01/08/2026", "R17.25", "", "R0.00", "Public IP Rental", "Service", "Debit"),
    (8, 87088, "31/07/2026", "", "R764.75", "-R764.75", "Netcash Pay Now", "Payment", "Credit"),
    (7, 83287, "02/07/2026", "", "R780.62", "R0.00", "Deducted from Salary", "Service", "Credit"),
    (4, 82027, "01/07/2026", "R918.85", "", "R934.72", "Skybre Wireless 10Mbps", "SW 10/10", "Debit"),
    (5, 82028, "01/07/2026", "", "R171.35", "R763.37", "Discount for Skybre Wireless 10Mbps", "SW 10/10", "Credit"),
    (6, 82029, "01/07/2026", "R17.25", "", "R780.62", "Public IP Rental", "Service", "Debit"),
    (2, 77858, "01/06/2026", "R183.77", "", "R12.42", "Skybre Wireless 10Mbps", "SW 10/10", "Debit"),
    (3, 77859, "01/06/2026", "R3.45", "", "R15.87", "Public IP Rental", "Service", "Debit"),
    (1, 78281, "27/05/2026", "", "R171.35", "-R171.35", "Skybre Wireless 10Mbps", "SW 10/10", "Credit"),
]


def _jayden_csv(rows=JAYDEN_ROWS, header=HEADER):
    lines = [",".join(str(c) for c in row) for row in rows]
    return (header + "\n".join(lines)).encode("utf-8")


def _customer(name="Jayden Van Wyk", balance="0.00", customer_id=""):
    return Customer.objects.create(
        full_name=name, email=f"{name.split()[0].lower()}@example.com",
        balance=Decimal(balance), customer_id=customer_id,
    )


class TransactionReportGoldenImportTests(TestCase):
    """The real file. If this drifts, something about the reconciliation
    itself broke, not just a unit somewhere."""

    def test_plan_matches_the_hand_reconciliation(self):
        preview = plan(_customer(), _jayden_csv())
        self.assertEqual(preview["errors"], [])
        summary = preview["summary"]
        self.assertEqual(summary["invoices"], 6)
        self.assertEqual(summary["payments"], 3)
        self.assertEqual(summary["credits"], 2)
        self.assertEqual(summary["errors"], 0)
        self.assertEqual(Decimal(summary["invoiced_total"]), Decimal("3380.77"))
        self.assertEqual(Decimal(summary["paid_total"]), Decimal("1965.37"))
        self.assertEqual(Decimal(summary["credited_total"]), Decimal("869.03"))
        self.assertEqual(Decimal(summary["balance_change"]), Decimal("546.37"))

    def test_commit_lands_the_customer_at_splynx_true_live_balance(self):
        customer = _customer(balance="0.00")
        result = commit(customer, _jayden_csv())
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["written"], {"invoices": 6, "payments": 3, "credits": 2})
        customer.refresh_from_db()
        self.assertEqual(customer.balance, Decimal("546.37"))

    def test_the_previously_uncaptured_salary_deduction_becomes_a_payment(self):
        """The whole reason this importer exists: billing.ledger_import's
        coarse export never surfaces this R780.62 settlement as a Payment
        at all."""
        customer = _customer()
        commit(customer, _jayden_csv())
        payment = Payment.objects.get(customer=customer, amount=Decimal("780.62"))
        self.assertIn("83287", payment.note)
        self.assertIn("Deducted from Salary", payment.note)

    def test_invoice_totals_match_the_original_system_exactly(self):
        customer = _customer()
        commit(customer, _jayden_csv())
        totals = {i.number: i.total for i in Invoice.objects.filter(customer=customer)}
        self.assertEqual(totals["TXN77858"], Decimal("187.22"))   # was INV202601005335
        self.assertEqual(totals["TXN82027"], Decimal("764.75"))   # was INV202601006243
        self.assertEqual(totals["TXN86231"], Decimal("764.75"))   # was INV202601007130
        self.assertEqual(totals["TXN90672"], Decimal("764.75"))   # was INV202601008058
        self.assertEqual(totals["TXN92206"], Decimal("433.55"))   # was INV202601008200
        self.assertEqual(totals["TXN95301"], Decimal("465.75"))   # was INV202606005044
        self.assertEqual(Invoice.objects.filter(customer=customer).count(), 8)  # 6 + 2 credit notes

    def test_credit_notes_match_the_original_system_exactly(self):
        customer = _customer()
        commit(customer, _jayden_csv())
        self.assertEqual(Invoice.objects.get(number="TXN78281").total, Decimal("-171.35"))  # was CN202601000256
        self.assertEqual(Invoice.objects.get(number="TXN92203").total, Decimal("-697.68"))  # was CN202601000385

    def test_per_invoice_paid_status_is_approximated_from_the_running_balance(self):
        """Documented as approximate in the module docstring — the file has
        no per-invoice payment allocation, only one account-wide running
        balance. Hand-verified against each invoice's own trailing balance:
        only the 01/08 invoice is square (balance 0.00) right after its own
        rows, so only it comes in Paid; every other invoice in this
        particular account's history still shows a positive running
        balance immediately after its own date, so lands Unpaid even
        though some of them were, in reality, paid off later."""
        customer = _customer()
        commit(customer, _jayden_csv())
        paid = Invoice.objects.get(number="TXN86231")
        self.assertEqual(paid.status, Invoice.Status.PAID)
        self.assertEqual(paid.paid_amount, paid.total)
        for number in ("TXN77858", "TXN82027", "TXN90672", "TXN92206", "TXN95301"):
            invoice = Invoice.objects.get(number=number)
            self.assertEqual(invoice.status, Invoice.Status.UNPAID, number)

    def test_rerun_is_idempotent(self):
        customer = _customer()
        first = commit(customer, _jayden_csv())
        self.assertEqual(first["written"], {"invoices": 6, "payments": 3, "credits": 2})
        customer.refresh_from_db()
        balance_after_first = customer.balance
        second = commit(customer, _jayden_csv())
        self.assertEqual(second["written"], {"invoices": 0, "payments": 0, "credits": 0})
        customer.refresh_from_db()
        self.assertEqual(customer.balance, balance_after_first)
        self.assertEqual(Invoice.objects.filter(customer=customer).count(), 8)
        self.assertEqual(Payment.objects.filter(customer=customer).count(), 3)


class RunningBalanceConsistencyTests(TestCase):
    def test_broken_chain_refuses_the_whole_import(self):
        rows = list(JAYDEN_ROWS)
        # Corrupt one row's own balance so it no longer follows from the
        # row before it.
        bad = list(rows[0])
        bad[5] = "R999.99"
        rows[0] = tuple(bad)
        customer = _customer()
        result = commit(customer, _jayden_csv(rows))
        self.assertTrue(result["errors"])
        self.assertIn("doesn't add up", result["errors"][0])
        self.assertEqual(Invoice.objects.filter(customer=customer).count(), 0)
        self.assertEqual(Payment.objects.filter(customer=customer).count(), 0)

    def test_unreadable_balance_refuses_the_whole_import(self):
        rows = list(JAYDEN_ROWS)
        bad = list(rows[0])
        bad[5] = ""
        rows[0] = tuple(bad)
        result = plan(_customer(), _jayden_csv(rows))
        self.assertTrue(result["errors"])
        self.assertIn("no readable Account balance", result["errors"][0])


class ReferenceMismatchTests(TestCase):
    # Trailing commas, matching the real column count: a one-cell line with
    # no delimiter at all confuses csv.Sniffer's delimiter detection on a
    # short sample (a quirk shared with billing.ledger_import's own CSV
    # reader, not something to special-case here) -- a real Splynx export's
    # title row is comma-padded the same way its data rows are, so this
    # matches the actual file shape rather than working around a test-only
    # artifact.
    TITLE_ROW = "Jayden Van Wyk (SMF883),,,,,,,,\n"

    def test_wrong_customer_reference_refuses_the_whole_import(self):
        header = self.TITLE_ROW + HEADER
        customer = _customer(customer_id="SMF999")
        result = commit(customer, _jayden_csv(header=header))
        self.assertTrue(result["errors"])
        self.assertIn("SMF883", result["errors"][0])
        self.assertIn("SMF999", result["errors"][0])
        self.assertEqual(Invoice.objects.filter(customer=customer).count(), 0)

    def test_matching_reference_imports_normally(self):
        header = self.TITLE_ROW + HEADER
        customer = _customer(customer_id="SMF883")
        result = commit(customer, _jayden_csv(header=header))
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["written"]["invoices"], 6)


class ClassificationTests(TestCase):
    """_classify() in isolation — one case per branch, independent of the
    golden file above."""

    def test_debit_is_always_a_charge_regardless_of_category(self):
        kind, amount = _classify("service", "Public IP Rental", Decimal("17.25"), None)
        self.assertEqual((kind, amount), ("charge", Decimal("17.25")))

    def test_payment_category_wins_even_over_a_discount_looking_description(self):
        kind, amount = _classify("payment", "Discount for MTD 50Mbps", None, Decimal("10.00"))
        self.assertEqual(kind, "payment")

    def test_discount_for_description_is_a_discount(self):
        kind, amount = _classify("mtd 50/50", "Discount for MTD 50Mbps", None, Decimal("171.35"))
        self.assertEqual(kind, "discount")

    def test_service_category_credit_is_a_payment(self):
        kind, amount = _classify("service", "Deducted from Salary", None, Decimal("780.62"))
        self.assertEqual(kind, "payment")

    def test_uncategorised_standalone_credit_is_a_credit_note(self):
        kind, amount = _classify("sw 10/10", "Skybre Wireless 10Mbps", None, Decimal("697.68"))
        self.assertEqual(kind, "credit_note")


class PreviewCommitAgreementTests(TestCase):
    def test_plan_and_commit_report_the_same_summary(self):
        customer = _customer()
        preview = plan(customer, _jayden_csv())
        result = commit(customer, _jayden_csv())
        self.assertEqual(preview["summary"], result["summary"])
