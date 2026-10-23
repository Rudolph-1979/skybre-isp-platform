"""What a payment row says on a document the customer reads.

THE DEFECT

A statement line's description came straight from `Payment.note`. That
field has no customer-facing purpose: the ledger import writes the old
system's receipt reference into it, because Payment has no reference
column and the import needs somewhere to put its idempotency key.

So CCTV Central's August statement described their own payment as
"Imported from previous system: RCP2026-12-01726", while the Reference
column sat empty next to it. Nobody saw it until the document was
rendered, which is the argument for rendering documents before sending
them.

TWO RULES HERE, and the second matters more than the first:

  * a receipt reference belongs in the reference column;
  * a note somebody TYPED never reaches the customer at all. Notes are
    staff-facing -- who chased whom, what was agreed on the phone, whether
    somebody is disputing the charge -- and a statement is the worst
    possible place to discover one.
"""
import datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from customers.models import Customer

from . import statements
from .models import Invoice, Payment

AUGUST = datetime.date(2026, 8, 3)


class PaymentRowTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            full_name="CCTV Central", customer_id="CUS-009001", city="Kroonstad",
        )

    def _payment(self, note, amount="435.85", method=Payment.Method.BANK_TRANSFER):
        payment = Payment.objects.create(
            customer=self.customer, amount=Decimal(amount), method=method, note=note,
        )
        Payment.objects.filter(pk=payment.pk).update(
            date=timezone.make_aware(
                datetime.datetime.combine(AUGUST, datetime.time(12, 0)),
                timezone.get_current_timezone(),
            )
        )
        return payment

    def _row(self):
        rows = statements.movements(self.customer)
        payments = [r for r in rows if r["kind"] == "payment"]
        self.assertEqual(len(payments), 1, rows)
        return payments[0]

    def test_an_imported_receipt_goes_in_the_reference_column(self):
        """THE ONE THIS EXISTS FOR."""
        self._payment("Imported from previous system: RCP2026-12-01726")

        row = self._row()

        self.assertEqual(row["reference"], "RCP2026-12-01726")
        self.assertEqual(row["description"], "Payment received - Bank Transfer")

    def test_the_import_wording_is_never_shown_to_the_customer(self):
        self._payment("Imported from previous system: RCP2026-13-00838")

        self.assertNotIn("Imported", self._row()["description"])

    def test_a_typed_note_does_not_reach_the_statement(self):
        """Notes are staff-facing. This one would have gone out on a
        document with the customer's name on it."""
        self._payment("Phoned him again, says he will pay Friday. Disputes the router fee.")

        row = self._row()

        self.assertEqual(row["reference"], "")
        self.assertEqual(row["description"], "Payment received - Bank Transfer")

    def test_a_payment_with_no_note_still_reads_properly(self):
        self._payment("", method=Payment.Method.CASH)

        self.assertEqual(self._row()["description"], "Payment received - Cash")

    def test_the_amount_and_direction_are_unchanged(self):
        """The presentation changed; the arithmetic must not have."""
        self._payment("Imported from previous system: RCP2026-12-01726")

        row = self._row()

        self.assertEqual(row["credit"], Decimal("435.85"))
        self.assertEqual(row["debit"], Decimal("0.00"))

    def test_a_prefix_with_nothing_after_it_is_not_a_reference(self):
        self._payment("Imported from previous system: ")

        self.assertEqual(self._row()["reference"], "")


class StatementPdfRenderTests(TestCase):
    """Actually run xhtml2pdf over the template.

    THE GAP THIS CLOSES. Every other test around statements mocks the
    mailer or the attachment builder, so none of them touches reportlab.
    The first version of period_statement_pdf.html left column widths to
    reportlab, which squeezed one of six columns down to 12pt against 6pt
    of padding either side and raised

        flowable given negative availWidth=-1.77e-15

    -- the whole PDF failed to render, with 478 tests green. The suite said
    the figures were right and never once asked whether the document could
    be produced.

    Slow by the standards of the tests around it, and worth every
    millisecond: it is the only one that would have caught that.
    """

    def test_a_statement_with_real_looking_values_renders(self):
        from notifications.pdf import render_period_statement_pdf

        customer = Customer.objects.create(
            full_name="CCTV Central", customer_id="CUS-009002", city="Kroonstad",
            address="12 Voortrekker Street", email="accounts@example.com",
        )
        # Values the length of the real ones. A short invoice number and a
        # two-word description would fit in almost any layout; these are
        # what the columns actually have to hold.
        invoice = Invoice.objects.create(
            customer=customer, status=Invoice.Status.UNPAID, total=Decimal("435.85"),
            date_due=datetime.date(2026, 8, 15),
        )
        Invoice.objects.filter(pk=invoice.pk).update(
            number="INV202606003635",
            date_issued=datetime.date(2026, 8, 1),
            date_created=datetime.date(2026, 8, 1),
        )
        for day, reference in ((3, "RCP2026-12-01726"), (4, "RCP2026-13-00838")):
            payment = Payment.objects.create(
                customer=customer, amount=Decimal("435.85"),
                method=Payment.Method.BANK_TRANSFER,
                note=f"Imported from previous system: {reference}",
            )
            Payment.objects.filter(pk=payment.pk).update(
                date=timezone.make_aware(
                    datetime.datetime.combine(
                        datetime.date(2026, 8, day), datetime.time(12, 0)
                    ),
                    timezone.get_current_timezone(),
                )
            )

        statement = statements.period_statement(customer, 2026, 8)
        pdf = render_period_statement_pdf(customer, statement, "Skybre")

        self.assertTrue(pdf.startswith(b"%PDF"), "not a PDF")
        self.assertGreater(len(pdf), 1000, "suspiciously small for a full page")

    def test_a_month_with_no_activity_renders(self):
        """The empty-table branch has its own colspan row, and an empty
        table is exactly the shape a layout engine gets wrong."""
        from notifications.pdf import render_period_statement_pdf

        customer = Customer.objects.create(
            full_name="Quiet Quinn", customer_id="CUS-009003", city="Kroonstad",
        )

        statement = statements.period_statement(customer, 2026, 8)
        pdf = render_period_statement_pdf(customer, statement, "Skybre")

        self.assertTrue(pdf.startswith(b"%PDF"))
