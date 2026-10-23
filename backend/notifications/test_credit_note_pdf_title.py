"""A credit note's PDF said "Tax invoice" -- fixed to say "Credit note".

Rudolph, 26 Sep, on CN-000001 (Christo's renumbered credit note, see
billing/test_credit_note_invoices.py): "the credit note we worked on last
night is fixed but the document shows Tax Invoice." A credit note (see
CreditRequest.create_credit_note_invoice) is, in every other respect, an
ordinary Invoice at status UNPAID/PAID -- it has no Status of its own, so
render_invoice_pdf's `titles` lookup (keyed by Status) always fell through
to its default, "Tax invoice", for one.

Told apart the same way billing.statements.movements() already does: a
negative total, not a Status and not a CreditRequest link -- so a
historical Splynx-imported credit note (no CreditRequest at all) gets the
same fix, and an ordinary invoice (however small, however it was created)
is never mistaken for one.
"""
from decimal import Decimal
from unittest import mock

from django.test import TestCase

from billing.models import Invoice, InvoiceItem
from customers.models import Customer
from notifications.pdf import render_invoice_pdf


class CreditNotePdfTitleTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            full_name="Title Tess", email="tess@example.com"
        )

    def _pdf_context(self, invoice):
        """Render the document and capture what it was given to print,
        without actually running xhtml2pdf -- same technique as
        test_invoice_pdf_totals.py."""
        captured = {}

        def fake_render(template_name, context):
            captured.update(context)
            return "<html><body></body></html>"

        with mock.patch("notifications.pdf.render_to_string", fake_render):
            render_invoice_pdf(invoice, "Skybre")
        return captured

    def test_a_credit_note_says_credit_note_not_tax_invoice(self):
        invoice = Invoice.objects.create(
            customer=self.customer, status=Invoice.Status.PAID, date_due="2026-09-25",
        )
        InvoiceItem.objects.create(
            invoice=invoice, description="Credit", quantity=1,
            unit_price=Decimal("-978.68"), tax_rate_pct=0,
        )
        invoice.recalc_totals()
        invoice.refresh_from_db()
        self.assertLess(invoice.total, 0)

        context = self._pdf_context(invoice)
        self.assertEqual(context["doc_title"], "Credit note")

    def test_an_ordinary_paid_invoice_still_says_tax_invoice(self):
        invoice = Invoice.objects.create(
            customer=self.customer, status=Invoice.Status.PAID, date_due="2026-09-25",
        )
        InvoiceItem.objects.create(
            invoice=invoice, description="Monthly service", quantity=1,
            unit_price=Decimal("899.00"), tax_rate_pct=15,
        )
        invoice.recalc_totals()
        invoice.refresh_from_db()
        self.assertGreater(invoice.total, 0)

        context = self._pdf_context(invoice)
        self.assertEqual(context["doc_title"], "Tax invoice")

    def test_a_historical_credit_note_with_no_credit_request_link_is_also_fixed(self):
        """The fix keys off the total, not a CreditRequest link -- a
        Splynx-imported credit note (e.g. CN202605000020) has no
        CreditRequest at all, and must get the same corrected title."""
        invoice = Invoice.objects.create(
            customer=self.customer, status=Invoice.Status.PAID, date_due="2026-09-25",
            number="CN202605000020",
        )
        InvoiceItem.objects.create(
            invoice=invoice, description="Historical credit", quantity=1,
            unit_price=Decimal("-500.00"), tax_rate_pct=0,
        )
        invoice.recalc_totals()
        invoice.refresh_from_db()
        self.assertFalse(hasattr(invoice, "credit_request"))

        context = self._pdf_context(invoice)
        self.assertEqual(context["doc_title"], "Credit note")

    def test_a_quote_and_pro_forma_are_unaffected(self):
        quote = Invoice.objects.create(
            customer=self.customer, status=Invoice.Status.QUOTE, date_due="2026-09-25",
        )
        InvoiceItem.objects.create(
            invoice=quote, description="Router", quantity=1,
            unit_price=Decimal("3500.00"), tax_rate_pct=15,
        )
        quote.recalc_totals()
        quote.refresh_from_db()
        self.assertEqual(self._pdf_context(quote)["doc_title"], "Quotation")

        proforma = Invoice.objects.create(
            customer=self.customer, status=Invoice.Status.PROFORMA, date_due="2026-09-25",
        )
        InvoiceItem.objects.create(
            invoice=proforma, description="Router", quantity=1,
            unit_price=Decimal("3500.00"), tax_rate_pct=15,
        )
        proforma.recalc_totals()
        proforma.refresh_from_db()
        self.assertEqual(self._pdf_context(proforma)["doc_title"], "Pro forma invoice")
