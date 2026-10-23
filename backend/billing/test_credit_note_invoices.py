"""Approving a credit request now leaves a visible line item.

Rudolph, 25 Sep: Christo van den Heever's R978.68 salary-deduction credit
was approved and the balance moved, but "doesn't show up in his account."
Confirmed it wasn't a bug -- CreditRequestViewSet.approve has always moved
Customer.balance directly and nothing else -- and then, once he'd checked
the balance figure itself really had moved: "The balance did adjust, I
need to see the credit line."

His two follow-up answers shaped this:
  1. Automatic going forward, and also backfill Christo's existing
     approved credit so it shows too.
  2. Originally: share the ordinary "INV" number sequence. Then he saw
     the first one land as INV-000015 next to the historical
     Splynx-imported CN202605000020 and asked for "CN" instead -- a
     credit note now gets its own "CN-" sequence (see
     Invoice._next_number / _CREDIT_NOTE_PREFIX).

The mechanism: CreditRequest.create_credit_note_invoice() creates a real
Invoice with one negative line for the credit's amount, immediately Paid
(nothing is owed on money already given back), with balance_debited=None
so nothing about it can ever move Customer.balance a second time --
that already happened via approve()'s own F() update.

Building a REAL invoice for this, rather than just a cosmetic note, turned
out to have a wider blast radius than the visibility ask alone: several
places in the codebase already independently total up "every invoice" and
"every approved credit" as two SEPARATE numbers that get added together
(the finance dashboard's total income, manage.py balance_drift, a
statement's derived balance, the VAT return's credit-notes figure). Once
a credit has BOTH an invoice and its own CreditRequest row, counting both
unqualified would count the same rand amount twice. Each of those call
sites now excludes a credit that already has its own invoice from ITS
OWN separate credit tally, so the invoice is the single place that amount
is counted. See the comment at each site for why that one and not the
other side of the pair was the one excluded.
"""
import datetime
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.testing import ALL_SECTIONS
from customers.models import Customer

from . import dashboard, statements
from .models import CreditRequest, Invoice

User = get_user_model()


class CreditNoteInvoiceTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.manager = User.objects.create_user(
            username="manager-cn", password="pw-for-tests", role=User.Role.MANAGEMENT,
            allowed_sections=ALL_SECTIONS,
        )
        self.customer = Customer.objects.create(
            full_name="Christo van der Toets", email="christo@example.com",
            balance=Decimal("1000.00"),
        )
        self.credit = CreditRequest.objects.create(
            customer=self.customer, amount=Decimal("978.68"),
            reason="Salary Deduction 02/07/2026",
        )

    def _approve(self):
        self.client.force_authenticate(self.manager)
        res = self.client.post(f"/api/credit-requests/{self.credit.id}/approve/")
        self.assertEqual(res.status_code, 200, res.data)
        return res

    # --- the invoice itself ----------------------------------------------

    def test_approving_creates_a_negative_paid_invoice(self):
        self._approve()
        self.credit.refresh_from_db()
        invoice = self.credit.credit_note
        self.assertIsNotNone(invoice)
        self.assertEqual(invoice.customer_id, self.customer.id)
        self.assertEqual(invoice.total, Decimal("-978.68"))
        self.assertEqual(invoice.paid_amount, Decimal("-978.68"))
        self.assertEqual(invoice.status, Invoice.Status.PAID)
        self.assertIsNone(invoice.balance_debited)

    def test_the_credit_note_gets_its_own_cn_sequence(self):
        """Rudolph, seeing the first one land as INV-000015: "You named the
        credit INV? It should be CN." A credit note now gets its own
        "CN-" sequence, independent of the ordinary invoice numbering."""
        other = Invoice.objects.create(
            customer=self.customer, status=Invoice.Status.DRAFT, date_due=timezone.localdate(),
        )
        self._approve()
        self.credit.refresh_from_db()
        self.assertTrue(self.credit.credit_note.number.startswith("CN-"))
        self.assertNotEqual(self.credit.credit_note.number, other.number)

    def test_the_cn_sequence_is_independent_of_the_inv_sequence(self):
        """A credit note is the FIRST document ever created should still
        start at CN-000001, not carry on from wherever the INV sequence
        happens to be."""
        Invoice.objects.create(
            customer=self.customer, status=Invoice.Status.UNPAID, date_due=timezone.localdate(),
        )  # some INV-000NNN already exists
        self._approve()
        self.credit.refresh_from_db()
        self.assertEqual(self.credit.credit_note.number, "CN-000001")

    def test_a_second_credit_note_continues_the_cn_sequence(self):
        self._approve()
        self.credit.refresh_from_db()
        first_number = self.credit.credit_note.number

        other_credit = CreditRequest.objects.create(
            customer=self.customer, amount=Decimal("50.00"), reason="second credit",
        )
        self.client.force_authenticate(self.manager)
        res = self.client.post(f"/api/credit-requests/{other_credit.id}/approve/")
        self.assertEqual(res.status_code, 200, res.data)
        other_credit.refresh_from_db()

        self.assertEqual(first_number, "CN-000001")
        self.assertEqual(other_credit.credit_note.number, "CN-000002")

    def test_the_reason_becomes_the_line_description(self):
        self._approve()
        self.credit.refresh_from_db()
        item = self.credit.credit_note.items.get()
        self.assertEqual(item.description, "Salary Deduction 02/07/2026")
        self.assertEqual(item.unit_price, Decimal("-978.6800"))
        self.assertEqual(item.tax_rate_pct, Decimal("0.00"))

    def test_the_note_is_dated_to_when_the_credit_was_decided(self):
        """Not to whenever this test (or a backfill) happens to run --
        otherwise a credit approved months ago would look like the newest
        thing on the account."""
        self.credit.status = CreditRequest.Status.APPROVED
        self.credit.decided_at = timezone.make_aware(datetime.datetime(2026, 7, 2, 9, 0))
        self.credit.save(update_fields=["status", "decided_at"])
        invoice = self.credit.create_credit_note_invoice()
        self.assertEqual(invoice.date_issued, datetime.date(2026, 7, 2))
        self.assertEqual(invoice.date_due, datetime.date(2026, 7, 2))

    def test_it_appears_on_the_customers_invoices_list(self):
        self._approve()
        self.client.force_authenticate(self.manager)
        res = self.client.get(f"/api/invoices/?customer={self.customer.id}")
        self.assertEqual(res.status_code, 200)
        numbers = [row["number"] for row in res.json()["results"]]
        self.credit.refresh_from_db()
        self.assertIn(self.credit.credit_note.number, numbers)

    def test_create_credit_note_invoice_requires_a_decision(self):
        with self.assertRaises(ValueError):
            self.credit.create_credit_note_invoice()

    # --- the balance is still moved exactly once --------------------------

    def test_the_balance_still_moves_only_once(self):
        self._approve()
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal("21.32"))  # 1000.00 - 978.68

    def test_rejecting_creates_no_invoice(self):
        self.client.force_authenticate(self.manager)
        res = self.client.post(f"/api/credit-requests/{self.credit.id}/reject/")
        self.assertEqual(res.status_code, 200, res.data)
        self.credit.refresh_from_db()
        self.assertIsNone(self.credit.credit_note)
        self.assertEqual(Invoice.objects.filter(customer=self.customer).count(), 0)

    # --- the double-counting this could have introduced -------------------

    def test_dashboard_total_income_nets_the_credit_exactly_once(self):
        """paid_invoices excludes the credit note (it has its own invoice
        now); credit_notes still carries the CreditRequest amount, same as
        always. If the exclusion in dashboard.py were missing, total
        income would be R978.68 further down than this."""
        Invoice.objects.create(
            customer=self.customer, status=Invoice.Status.PAID, total=Decimal("500.00"),
            date_due=timezone.localdate(),
        )
        self._approve()

        figures = dashboard.FinanceDashboard(
            invoices=Invoice.objects.filter(customer=self.customer),
            payments=self.customer.payments.all(),
            credits=self.customer.credit_requests.all(),
            services=self.customer.services.all(),
            period="lifetime",
        ).tiles()

        self.assertEqual(figures["paid_invoices"]["amount"], Decimal("500.00"))
        self.assertEqual(figures["credit_notes"]["amount"], Decimal("978.68"))
        self.assertEqual(figures["total_income"], Decimal("500.00") - Decimal("978.68"))

    def test_statement_movements_lists_the_credit_once(self):
        """Not once as an invoice row (kind=credit_note) AND once as the
        CreditRequest's own fallback row (kind=credit) -- the fallback is
        for a credit with no invoice counterpart at all."""
        self._approve()
        rows = statements.movements(self.customer)
        kinds = [r["kind"] for r in rows]
        self.assertEqual(kinds.count("credit_note"), 1)
        self.assertEqual(kinds.count("credit"), 0)

    def test_balance_drift_does_not_double_count_the_credit(self):
        """A fully grounded customer (nothing owed, no other invoices) who
        is given a credit should show ZERO drift afterwards -- not R978.68
        of it. Started from a balance backed by nothing (self.customer's
        1000.00 has no invoice behind it) this would already show pre-
        existing drift for a reason that has nothing to do with credits,
        so this uses its own customer grounded at zero instead."""
        grounded = Customer.objects.create(
            full_name="Grounded Gugu", email="gugu@example.com", balance=Decimal("0.00"),
        )
        credit = CreditRequest.objects.create(
            customer=grounded, amount=Decimal("150.00"), reason="goodwill",
        )
        self.client.force_authenticate(self.manager)
        res = self.client.post(f"/api/credit-requests/{credit.id}/approve/")
        self.assertEqual(res.status_code, 200, res.data)

        out = StringIO()
        call_command("balance_drift", "--all", stdout=out)
        self.assertNotIn(f"  {grounded.id}  ", out.getvalue())

    def test_vat_return_credit_notes_figure_excludes_ones_with_an_invoice(self):
        from expenses.views import build_vat_return

        self._approve()
        today = timezone.localdate()
        result = build_vat_return(today.replace(day=1), today)
        self.assertEqual(result["credit_notes"]["count"], 0)


class CreditAppliedToInvoiceTests(TestCase):
    """CreditRequest.invoice -- the CN-00002 / TXN92206 fix.

    Rudolph, 1 Oct: "The Creditnote function needs to change. A credit
    must be allocated to a specific invoice, that will allow the invoice
    to return as paid, and will then not cause this issue again. The
    credit note CN-00002 was for invoices TXN92206 that was partially
    paid. When the creditnote was raised, it could be linked to a
    invoice, and did not change the invoice status."

    Before this, approving a credit only ever moved Customer.balance (and
    created its own separate, always-Paid credit-note invoice) -- the
    invoice the credit was actually FOR never heard about it, so it sat
    at UNPAID (and, once its due date passed, OVERDUE) regardless of
    whether real debt was left on it. That is exactly what let Jayden van
    Wyk's suspension outlive a credit that was meant to clear it: her
    balance looked fine, but billing.recurring.has_overdue_invoice (and
    therefore billing.reactivation.is_clear) only looks at invoices, not
    the balance alone -- see billing/reactivation.py and
    billing/test_reactivation.py for that half of the fix.
    """

    def setUp(self):
        self.client = APIClient()
        self.manager = User.objects.create_user(
            username="manager-cai", password="pw-for-tests", role=User.Role.MANAGEMENT,
            allowed_sections=ALL_SECTIONS,
        )
        self.accounts = User.objects.create_user(
            username="accounts-cai", password="pw-for-tests", role=User.Role.ACCOUNTS,
            allowed_sections=ALL_SECTIONS,
        )
        self.customer = Customer.objects.create(
            full_name="Jayden Stand-in", email="jayden-stand-in@example.com",
            balance=Decimal("1000.00"),
        )

    def _overdue_invoice(self, *, total="500.00", paid_amount="0.00", days_overdue=10):
        due = timezone.localdate() - datetime.timedelta(days=days_overdue)
        invoice = Invoice.objects.create(
            customer=self.customer, status=Invoice.Status.OVERDUE, date_due=due,
            date_issued=due, balance_debited=True,
        )
        Invoice.objects.filter(pk=invoice.pk).update(
            total=Decimal(total), paid_amount=Decimal(paid_amount)
        )
        invoice.refresh_from_db()
        return invoice

    def _request(self, *, amount, invoice=None):
        self.client.force_authenticate(self.accounts)
        payload = {"customer": self.customer.id, "amount": amount, "reason": "test credit"}
        if invoice is not None:
            payload["invoice"] = invoice.id
        res = self.client.post("/api/credit-requests/", payload)
        self.assertEqual(res.status_code, 201, res.data)
        return res.data["id"]

    def _approve(self, credit_id):
        self.client.force_authenticate(self.manager)
        res = self.client.post(f"/api/credit-requests/{credit_id}/approve/")
        self.assertEqual(res.status_code, 200, res.data)
        return res

    def test_a_credit_covering_the_balance_due_settles_the_invoice(self):
        invoice = self._overdue_invoice(total="500.00", paid_amount="100.00")  # 400.00 outstanding
        credit_id = self._request(amount="400.00", invoice=invoice)
        self._approve(credit_id)

        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, Decimal("500.00"))
        self.assertEqual(invoice.status, Invoice.Status.PAID)

    def test_a_smaller_credit_leaves_it_partially_paid_not_settled(self):
        """paid_amount moves, but the stored status does NOT flip to Paid
        -- "Partially Paid" is a frontend-only categorisation computed
        from paid_amount vs total (see InvoiceStatusBadge), not a fifth
        stored Status value, precisely so has_overdue_invoice's
        status__in=[UNPAID, OVERDUE] filter keeps seeing this invoice
        until it is actually settled."""
        invoice = self._overdue_invoice(total="500.00", paid_amount="100.00")  # 400.00 outstanding
        credit_id = self._request(amount="150.00", invoice=invoice)
        self._approve(credit_id)

        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, Decimal("250.00"))
        self.assertEqual(invoice.status, Invoice.Status.OVERDUE)

    def test_a_credit_larger_than_the_balance_due_settles_it_and_the_rest_is_account_credit(self):
        """Mirrors billing.allocation.auto_allocate's own convention for an
        over-sized payment: the invoice is capped at fully paid, and
        nothing about the excess is lost -- the customer's balance was
        already reduced by the FULL credit amount in approve()'s own F()
        update, so the surplus simply sits there as account credit."""
        invoice = self._overdue_invoice(total="500.00", paid_amount="100.00")  # 400.00 outstanding
        credit_id = self._request(amount="1000.00", invoice=invoice)
        self._approve(credit_id)

        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, Decimal("500.00"))
        self.assertEqual(invoice.status, Invoice.Status.PAID)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal("0.00"))  # 1000.00 - 1000.00

    def test_settling_the_invoice_clears_has_overdue_invoice_and_is_clear(self):
        """The actual end-to-end fix: once the credit has settled the
        invoice that was overdue, the shared conjunction both
        blocking_candidate_services and reactivation.is_clear read no
        longer sees anything overdue -- regardless of whether the
        customer's balance alone would already have said so."""
        from . import reactivation
        from .models import CustomerBillingConfig
        from .recurring import has_overdue_invoice

        config = CustomerBillingConfig.for_customer(self.customer)
        config.blocking_period_days = 4
        config.save()

        invoice = self._overdue_invoice(total="500.00", paid_amount="0.00", days_overdue=10)
        # Balance alone would NOT yet say this customer is clear -- the
        # point is that the invoice-level settlement is what does it.
        self.customer.balance = Decimal("500.00")
        self.customer.save(update_fields=["balance"])

        credit_id = self._request(amount="500.00", invoice=invoice)
        self._approve(credit_id)

        self.customer.refresh_from_db()
        self.assertFalse(has_overdue_invoice(self.customer, config, timezone.localdate()))
        self.assertTrue(reactivation.is_clear(self.customer, config))

    def test_with_no_invoice_given_it_settles_the_oldest_invoice(self):
        """Changed 5 Oct 2026 (Rudolph: money is always allocated to the
        oldest invoice): an unlinked credit no longer only moves the
        balance -- it settles the oldest open invoice, here partly."""
        invoice = self._overdue_invoice(total="500.00", paid_amount="0.00")
        credit_id = self._request(amount="200.00")
        self._approve(credit_id)

        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, Decimal("200.00"))
        self.assertEqual(invoice.status, Invoice.Status.OVERDUE)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal("800.00"))  # 1000.00 - 200.00

    def test_an_invoice_belonging_to_a_different_customer_is_refused(self):
        other_customer = Customer.objects.create(full_name="Other Oscar", email="oscar-cai@example.com")
        other_invoice = Invoice.objects.create(
            customer=other_customer, status=Invoice.Status.UNPAID, date_due=timezone.localdate(),
        )
        self.client.force_authenticate(self.accounts)
        res = self.client.post("/api/credit-requests/", {
            "customer": self.customer.id, "amount": "100.00", "reason": "test credit",
            "invoice": other_invoice.id,
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn("different customer", str(res.data))

    def test_a_quote_cannot_be_credited(self):
        quote = Invoice.objects.create(
            customer=self.customer, status=Invoice.Status.QUOTE, date_due=timezone.localdate(),
        )
        self.client.force_authenticate(self.accounts)
        res = self.client.post("/api/credit-requests/", {
            "customer": self.customer.id, "amount": "100.00", "reason": "test credit",
            "invoice": quote.id,
        })
        self.assertEqual(res.status_code, 400)

    def test_a_cancelled_invoice_cannot_be_credited(self):
        cancelled = Invoice.objects.create(
            customer=self.customer, status=Invoice.Status.CANCELLED, date_due=timezone.localdate(),
        )
        self.client.force_authenticate(self.accounts)
        res = self.client.post("/api/credit-requests/", {
            "customer": self.customer.id, "amount": "100.00", "reason": "test credit",
            "invoice": cancelled.id,
        })
        self.assertEqual(res.status_code, 400)

    def test_a_draft_invoice_cannot_be_credited(self):
        draft = Invoice.objects.create(
            customer=self.customer, status=Invoice.Status.DRAFT, date_due=timezone.localdate(),
        )
        self.client.force_authenticate(self.accounts)
        res = self.client.post("/api/credit-requests/", {
            "customer": self.customer.id, "amount": "100.00", "reason": "test credit",
            "invoice": draft.id,
        })
        self.assertEqual(res.status_code, 400)

    def test_the_invoice_number_is_exposed_on_the_credit_request(self):
        invoice = self._overdue_invoice(total="500.00", paid_amount="100.00")
        credit_id = self._request(amount="400.00", invoice=invoice)
        self.client.force_authenticate(self.manager)
        res = self.client.get(f"/api/credit-requests/{credit_id}/")
        self.assertEqual(res.data["invoice"], invoice.id)
        self.assertEqual(res.data["invoice_number"], invoice.number)


class BackfillCreditNoteInvoicesTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            full_name="Backfill Bongani", email="bongani@example.com", balance=Decimal("500.00"),
        )
        self.credit = CreditRequest.objects.create(
            customer=self.customer, amount=Decimal("200.00"), reason="Historical goodwill credit",
            status=CreditRequest.Status.APPROVED,
            decided_at=timezone.make_aware(datetime.datetime(2026, 5, 1, 10, 0)),
        )

    def _run(self, *args):
        out = StringIO()
        call_command("backfill_credit_note_invoices", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_writes_nothing(self):
        output = self._run()
        self.assertIn("1 approved credit request", output)
        self.assertIn("Dry run", output)
        self.credit.refresh_from_db()
        self.assertIsNone(self.credit.credit_note)

    def test_apply_creates_the_invoice_dated_to_the_original_decision(self):
        self._run("--apply")
        self.credit.refresh_from_db()
        self.assertIsNotNone(self.credit.credit_note)
        self.assertEqual(self.credit.credit_note.total, Decimal("-200.00"))
        self.assertEqual(self.credit.credit_note.date_issued, datetime.date(2026, 5, 1))

    def test_a_second_run_is_a_no_op(self):
        self._run("--apply")
        first_invoice_id = CreditRequest.objects.get(pk=self.credit.pk).credit_note_id
        output = self._run("--apply")
        self.assertIn("Nothing to do", output)
        self.assertEqual(
            CreditRequest.objects.get(pk=self.credit.pk).credit_note_id, first_invoice_id
        )

    def test_pending_and_rejected_credits_are_left_alone(self):
        CreditRequest.objects.create(
            customer=self.customer, amount=Decimal("10.00"), reason="pending",
        )
        CreditRequest.objects.create(
            customer=self.customer, amount=Decimal("10.00"), reason="rejected",
            status=CreditRequest.Status.REJECTED, decided_at=timezone.now(),
        )
        output = self._run("--apply")
        self.assertIn("Created 1 credit note", output)

    def test_customer_filter_limits_the_run(self):
        other_customer = Customer.objects.create(full_name="Other Oscar", email="oscar@example.com")
        CreditRequest.objects.create(
            customer=other_customer, amount=Decimal("50.00"), reason="other",
            status=CreditRequest.Status.APPROVED, decided_at=timezone.now(),
        )
        self._run("--apply", "--customer", str(self.customer.id))
        self.credit.refresh_from_db()
        self.assertIsNotNone(self.credit.credit_note)
        other_credit = CreditRequest.objects.get(customer=other_customer)
        self.assertIsNone(other_credit.credit_note)


class RenumberCreditNoteInvoicesTests(TestCase):
    """The one-off fixup for a credit note invoice created by the FIRST
    build of this feature, before the CN- sequence existed -- exactly
    Christo's situation: his note was created under the INV-sharing code
    and landed as INV-000015. Simulated here by creating the credit note
    the old way (Invoice.objects.create() with no _credit_note_prefix
    flag) rather than via create_credit_note_invoice(), which now always
    produces a CN- number and so would never need this command."""

    def setUp(self):
        self.customer = Customer.objects.create(
            full_name="Christo Stand-in", email="christo@example.com", balance=Decimal("1000.00"),
        )
        self.credit = CreditRequest.objects.create(
            customer=self.customer, amount=Decimal("978.68"), reason="Salary Deduction",
            status=CreditRequest.Status.APPROVED, decided_at=timezone.now(),
        )
        # The old (pre-fix) code path: an ordinary Invoice, no
        # _credit_note_prefix flag, so it took the next INV- number.
        self.old_note = Invoice.objects.create(
            customer=self.customer, status=Invoice.Status.UNPAID,
            date_due=timezone.now().date(), date_issued=timezone.now().date(),
            balance_debited=None,
        )
        self.credit.credit_note = self.old_note
        self.credit.save(update_fields=["credit_note"])

    def _run(self, *args):
        out = StringIO()
        call_command("renumber_credit_note_invoices", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_reports_it_and_writes_nothing(self):
        old_number = self.old_note.number
        output = self._run()
        self.assertIn("1 credit note invoice", output)
        self.assertIn(old_number, output)
        self.assertIn("Dry run", output)
        self.old_note.refresh_from_db()
        self.assertEqual(self.old_note.number, old_number)

    def test_apply_moves_it_onto_the_cn_sequence(self):
        self.assertTrue(self.old_note.number.startswith("INV-"))
        self._run("--apply")
        self.old_note.refresh_from_db()
        self.assertEqual(self.old_note.number, "CN-000001")

    def test_apply_touches_only_the_number(self):
        total_before = self.old_note.total
        paid_before = self.old_note.paid_amount
        status_before = self.old_note.status
        customer_before = self.old_note.customer_id
        self._run("--apply")
        self.old_note.refresh_from_db()
        self.assertEqual(self.old_note.total, total_before)
        self.assertEqual(self.old_note.paid_amount, paid_before)
        self.assertEqual(self.old_note.status, status_before)
        self.assertEqual(self.old_note.customer_id, customer_before)

    def test_a_second_run_is_a_no_op(self):
        self._run("--apply")
        self.old_note.refresh_from_db()
        renumbered_to = self.old_note.number
        output = self._run("--apply")
        self.assertIn("Nothing to do", output)
        self.old_note.refresh_from_db()
        self.assertEqual(self.old_note.number, renumbered_to)

    def test_a_credit_note_already_on_the_cn_sequence_is_left_alone(self):
        """A credit approved AFTER the CN- fix shipped needs no fixup --
        create_credit_note_invoice() already gave it a CN- number, so this
        command must not touch it (and must not be confused by it when
        computing the next CN- number for a row that DOES need fixing)."""
        other_customer = Customer.objects.create(full_name="Fixed Fiona", email="fiona@example.com")
        other_credit = CreditRequest.objects.create(
            customer=other_customer, amount=Decimal("50.00"), reason="already correct",
            status=CreditRequest.Status.APPROVED, decided_at=timezone.now(),
        )
        other_credit.create_credit_note_invoice()
        other_credit.save(update_fields=["credit_note"])
        already_correct_number = other_credit.credit_note.number

        output = self._run("--apply")
        self.assertIn("Renumbered 1 credit note", output)
        other_credit.refresh_from_db()
        self.assertEqual(other_credit.credit_note.number, already_correct_number)

    def test_historical_splynx_import_is_never_touched(self):
        """The legacy CN202605000020-style rows have no CreditRequest at
        all, so the query that finds fixup candidates (via
        CreditRequest.credit_note) can never reach them."""
        Invoice.objects.create(
            customer=self.customer, number="CN202605000020", status=Invoice.Status.PAID,
            date_due=timezone.now().date(), date_issued=timezone.now().date(),
            balance_debited=None,
        )
        self._run("--apply")
        legacy = Invoice.objects.get(number="CN202605000020")
        self.assertEqual(legacy.number, "CN202605000020")

    def test_customer_filter_limits_the_run(self):
        other_customer = Customer.objects.create(full_name="Other Oscar", email="oscar@example.com")
        other_credit = CreditRequest.objects.create(
            customer=other_customer, amount=Decimal("50.00"), reason="other",
            status=CreditRequest.Status.APPROVED, decided_at=timezone.now(),
        )
        other_note = Invoice.objects.create(
            customer=other_customer, status=Invoice.Status.UNPAID,
            date_due=timezone.now().date(), date_issued=timezone.now().date(),
            balance_debited=None,
        )
        other_credit.credit_note = other_note
        other_credit.save(update_fields=["credit_note"])

        self._run("--apply", "--customer", str(self.customer.id))
        self.old_note.refresh_from_db()
        other_note.refresh_from_db()
        self.assertEqual(self.old_note.number, "CN-000001")
        self.assertTrue(other_note.number.startswith("INV-"))
