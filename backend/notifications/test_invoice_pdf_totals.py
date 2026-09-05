"""A tax invoice has to add up.

The document printed its totals from two different calculations.
"Total exclusive" and "Total tax" were re-added in render_invoice_pdf,
which rounded each line's tax to cents before summing; "Total" and
"Balance due" came off the invoice row, where Invoice.recalc_totals added
at full precision and let each DecimalField round itself on save. On the
wrong numbers the two disagree by a cent, so the customer's tax invoice
states an exclusive amount and a VAT amount that do not sum to the total
they are being asked to pay -- and the VAT figure on the document is not
the one the VAT return declares.

0.25 x R50.10 at 15% is the shape that does it, and it is not exotic:
a quarter month of pro-rata on an ordinary line. Its subtotal R12.525
stores as R12.53 and its tax R1.87875 as R1.88, while the total R14.40375
stores as R14.40 -- so the invoice's own two halves sum to a cent more
than its total, before the PDF adds a second disagreement of its own.
"""
from decimal import Decimal
from unittest import mock

from django.test import TestCase

from billing.models import Invoice, InvoiceItem
from customers.models import Customer
from notifications.pdf import render_invoice_pdf

# Breaks the stored invoice AND the document. Verified against the real
# rounding, not assumed: Postgres rounds a numeric(12,2) half-up.
AWKWARD_QTY = Decimal("0.25")
AWKWARD_PRICE = Decimal("50.10")
# Breaks only the document -- the stored three happen to agree here, so this
# pins the PDF half of the fix on its own.
PRORATA_QTY = Decimal("1.50")
PRORATA_PRICE = Decimal("150.15")
VAT = Decimal("15.00")


class InvoiceTotalsAddUpTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            full_name="Pro Rata Customer", email="pr@example.com"
        )

    def _invoice(self, quantity, unit_price, rate=VAT):
        invoice = Invoice.objects.create(
            customer=self.customer, status=Invoice.Status.UNPAID, date_due="2026-09-30"
        )
        InvoiceItem.objects.create(
            invoice=invoice, description="Fibre 50/50 (pro rata)",
            quantity=quantity, unit_price=unit_price, tax_rate_pct=rate,
        )
        invoice.recalc_totals()
        invoice.refresh_from_db()
        return invoice

    def _pdf_context(self, invoice):
        """Render the document and capture what it was given to print."""
        captured = {}

        def fake_render(template_name, context):
            captured.update(context)
            return "<html><body></body></html>"

        with mock.patch("notifications.pdf.render_to_string", fake_render):
            render_invoice_pdf(invoice, "Skybre")
        return captured

    # ---- the stored invoice --------------------------------------------

    def test_the_stored_totals_add_up(self):
        invoice = self._invoice(AWKWARD_QTY, AWKWARD_PRICE)
        self.assertEqual(
            invoice.subtotal + invoice.tax_total, invoice.total,
            "the invoice's own exclusive and VAT amounts must sum to its total",
        )

    def test_ordinary_round_numbers_are_untouched(self):
        """The rounding change must not move a normal invoice by a cent."""
        invoice = self._invoice(Decimal("1.00"), Decimal("500.00"))
        self.assertEqual(invoice.subtotal, Decimal("500.00"))
        self.assertEqual(invoice.tax_total, Decimal("75.00"))
        self.assertEqual(invoice.total, Decimal("575.00"))

    def test_a_zero_rated_line_still_works(self):
        invoice = self._invoice(Decimal("2.00"), Decimal("99.99"), rate=Decimal("0.00"))
        self.assertEqual(invoice.tax_total, Decimal("0.00"))
        self.assertEqual(invoice.total, Decimal("199.98"))

    def test_an_invoice_with_no_items_is_zero_not_an_error(self):
        invoice = Invoice.objects.create(
            customer=self.customer, status=Invoice.Status.UNPAID, date_due="2026-09-30"
        )
        invoice.recalc_totals()
        invoice.refresh_from_db()
        self.assertEqual(invoice.total, Decimal("0.00"))

    # ---- the document ---------------------------------------------------

    def test_the_pdf_prints_totals_that_add_up(self):
        invoice = self._invoice(AWKWARD_QTY, AWKWARD_PRICE)
        ctx = self._pdf_context(invoice)
        self.assertEqual(
            ctx["total_exclusive"] + ctx["total_tax"], invoice.total,
            "the document's own exclusive + VAT must equal the Total it prints",
        )

    def test_the_pdf_shows_the_invoices_own_figures(self):
        """Not a second opinion computed in the view -- the same numbers the
        ledger, the VAT return and the balance are built from."""
        invoice = self._invoice(AWKWARD_QTY, AWKWARD_PRICE)
        ctx = self._pdf_context(invoice)
        self.assertEqual(ctx["total_exclusive"], invoice.subtotal)
        self.assertEqual(ctx["total_tax"], invoice.tax_total)

    def test_the_printed_totals_match_the_balance_due(self):
        """Nothing has been paid, so what the customer owes is exactly what
        the totals block adds up to."""
        invoice = self._invoice(AWKWARD_QTY, AWKWARD_PRICE)
        ctx = self._pdf_context(invoice)
        self.assertEqual(ctx["total_exclusive"] + ctx["total_tax"], ctx["balance_due"])

    def test_a_pro_rata_line_the_stored_totals_survive_still_prints_right(self):
        """1.5 x R150.15: the invoice row itself adds up, and the document
        used to disagree with it anyway by re-adding the lines."""
        invoice = self._invoice(PRORATA_QTY, PRORATA_PRICE)
        ctx = self._pdf_context(invoice)
        self.assertEqual(ctx["total_exclusive"] + ctx["total_tax"], invoice.total)

    def test_a_quote_adds_up_too(self):
        """Same renderer, same arithmetic -- quotes and pro formas become
        invoices later and must not change value on the way."""
        invoice = self._invoice(AWKWARD_QTY, AWKWARD_PRICE)
        invoice.status = Invoice.Status.QUOTE
        invoice.save(update_fields=["status"])
        ctx = self._pdf_context(invoice)
        self.assertEqual(ctx["total_exclusive"] + ctx["total_tax"], invoice.total)

    def test_the_pdf_still_renders(self):
        """Guarding that capturing context in the tests above is not the only
        thing that works -- the real template still produces a PDF."""
        invoice = self._invoice(AWKWARD_QTY, AWKWARD_PRICE)
        pdf = render_invoice_pdf(invoice, "Skybre")
        self.assertTrue(pdf.startswith(b"%PDF"), "expected a PDF back")
