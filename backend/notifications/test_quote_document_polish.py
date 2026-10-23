"""Two small things on the quote/invoice PDF, decided with Rudolph 2026-09-24.

1. NO BILLING PERIOD ON A QUOTE. A tariff-plan line carries a from/till
   period because accepting the quote turns it into a Service starting on
   that date (Invoice.activate_tariff_services) -- the data stays exactly
   as it is. But nothing is billed on a quote, so printing
   "(01/10/2026 - 31/10/2026)" under the line reads like a charge for that
   month. Only the QUOTE document hides it; a pro forma and a tax invoice
   are bills for a period and still show it.

2. NO EMPTY "ID:" / "VAT ID:" LINES. The customer block printed both labels
   with nothing after them for a customer without an ID or VAT number.
   Now each line prints only when there is a value. Applies to every
   document rendered from invoice_pdf.html (quote, pro forma, invoice).
"""
import datetime
from decimal import Decimal
from unittest import mock

from django.template.loader import render_to_string
from django.test import TestCase

from billing.models import Invoice, InvoiceItem, Tariff
from customers.models import Customer


class _Captured(Exception):
    pass


class QuoteDocumentPolishTests(TestCase):
    def setUp(self):
        self.tariff = Tariff.objects.create(
            name="Vuma BF 500/200", price=Decimal("607.83"),
            speed_download_kbps=512000, speed_upload_kbps=204800,
        )
        self.plain = Customer.objects.create(
            full_name="Plain Pieter", customer_id="CUS-009101",
            email="pieter@example.com", status=Customer.Status.ACTIVE,
        )
        self.registered = Customer.objects.create(
            full_name="Registered Rina", customer_id="CUS-009102",
            email="rina@example.com", status=Customer.Status.ACTIVE,
            id_number="8001015009087", vat_number="4999999999",
        )

    def _document(self, status, customer=None):
        doc = Invoice.objects.create(
            customer=customer or self.plain, status=status, total=Decimal("0.00"),
            date_due=datetime.date(2026, 10, 1),
        )
        InvoiceItem.objects.create(
            invoice=doc, description="RB-LHG5XL", quantity=Decimal("1"),
            unit_price=Decimal("1750.00"), tax_rate_pct=Decimal("15"),
            item_type=InvoiceItem.ItemType.PRODUCT,
        )
        InvoiceItem.objects.create(
            invoice=doc, description="Vuma BF 500/200 Mbps (monthly)", quantity=Decimal("1"),
            unit_price=Decimal("607.83"), tax_rate_pct=Decimal("15"),
            item_type=InvoiceItem.ItemType.TARIFF, tariff=self.tariff,
            period_start=datetime.date(2026, 10, 1), period_end=datetime.date(2026, 10, 31),
        )
        doc.recalc_totals()
        doc.save()
        return doc

    def _html(self, document):
        """The HTML render_invoice_pdf would hand to the PDF engine."""
        from notifications import pdf

        seen = {}

        def capture(template_name, context):
            seen["html"] = render_to_string(template_name, context)
            seen["context"] = context
            return b"%PDF-captured"

        with mock.patch.object(pdf, "_render_pdf", side_effect=capture):
            pdf.render_invoice_pdf(document, "Skybre")
        return seen["html"], seen["context"]

    # --- 1. billing period -------------------------------------------------

    def test_a_quote_prints_no_billing_period(self):
        html, context = self._html(self._document(Invoice.Status.QUOTE))
        self.assertNotIn("01/10/2026 - 31/10/2026", html)
        self.assertEqual([r["period"] for r in context["rows"]], ["", ""])

    def test_the_period_data_is_kept_on_the_quote_line(self):
        """Hidden on the page only -- accepting the quote needs it."""
        quote = self._document(Invoice.Status.QUOTE)
        self._html(quote)
        line = quote.items.get(item_type=InvoiceItem.ItemType.TARIFF)
        self.assertEqual(line.period_start, datetime.date(2026, 10, 1))
        self.assertEqual(line.period_end, datetime.date(2026, 10, 31))

    def test_a_pro_forma_still_prints_the_period(self):
        html, _ = self._html(self._document(Invoice.Status.PROFORMA))
        self.assertIn("01/10/2026 - 31/10/2026", html)

    def test_a_tax_invoice_still_prints_the_period(self):
        html, _ = self._html(self._document(Invoice.Status.UNPAID))
        self.assertIn("01/10/2026 - 31/10/2026", html)

    # --- 2. empty ID / VAT ID lines ------------------------------------------

    def test_no_empty_id_lines_for_a_customer_without_them(self):
        for status in (Invoice.Status.QUOTE, Invoice.Status.PROFORMA, Invoice.Status.UNPAID):
            with self.subTest(status=status):
                html, _ = self._html(self._document(status))
                self.assertNotIn("ID:", html)
                self.assertNotIn("VAT ID:", html)

    def test_id_lines_still_print_when_the_customer_has_them(self):
        html, _ = self._html(self._document(Invoice.Status.QUOTE, customer=self.registered))
        self.assertIn("ID: 8001015009087", html)
        self.assertIn("VAT ID: 4999999999", html)

    # --- a real PDF still comes out -------------------------------------------

    def test_real_pdfs_still_render(self):
        from notifications.pdf import render_invoice_pdf

        for status in (Invoice.Status.QUOTE, Invoice.Status.PROFORMA, Invoice.Status.UNPAID):
            with self.subTest(status=status):
                pdf_bytes = render_invoice_pdf(self._document(status), "Skybre")
                self.assertTrue(pdf_bytes.startswith(b"%PDF"))
