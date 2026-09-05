"""Output VAT has to land in the period the invoice was actually issued.

The return bucketed invoices by date_created, which is auto_now_add: it is
stamped when the row is first written and never moves. A document raised as
a quote in one VAT period and converted to a real tax invoice in the next
therefore kept the earlier date -- and its output VAT was declared in
NEITHER return. The earlier period excluded it because it was still a quote
at the time; the later period never saw it because of the date. Quotes that
become invoices later are ordinary business here, so this was live money
going undeclared to SARS.

Invoice.date_issued is stamped the day the document actually becomes a tax
invoice, and the return is built on that.
"""
from decimal import Decimal
from datetime import date
from unittest import mock

from django.test import TestCase

from billing.models import Invoice, InvoiceItem
from customers.models import Customer
from expenses.views import build_vat_return

MARCH = (date(2026, 3, 1), date(2026, 3, 31))
JUNE = (date(2026, 6, 1), date(2026, 6, 30))


class OutputVatPeriodTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            full_name="Quoted Customer", email="qc@example.com"
        )

    def _quote_raised_in_march(self):
        """A quote, row written in March -- date_created is auto_now_add, so
        it has to be forced rather than passed in."""
        invoice = Invoice.objects.create(
            customer=self.customer, status=Invoice.Status.QUOTE, date_due=date(2026, 4, 30)
        )
        InvoiceItem.objects.create(
            invoice=invoice, description="Fibre install", quantity=Decimal("1.00"),
            unit_price=Decimal("1000.00"), tax_rate_pct=Decimal("15.00"),
        )
        invoice.recalc_totals()
        Invoice.objects.filter(pk=invoice.pk).update(date_created=date(2026, 3, 10))
        invoice.refresh_from_db()
        return invoice

    def _convert_in_june(self, invoice):
        with mock.patch("billing.models.timezone.localdate", return_value=date(2026, 6, 15)):
            invoice.convert_to_invoice()
        invoice.refresh_from_db()
        return invoice

    def _output_vat(self, period):
        return build_vat_return(*period)["output"]["output_vat"]

    # ---- the gap --------------------------------------------------------

    def test_a_quote_declares_no_output_vat_while_it_is_still_a_quote(self):
        self._quote_raised_in_march()
        self.assertEqual(self._output_vat(MARCH), Decimal("0.00"))

    def test_converting_declares_the_vat_in_the_period_it_was_converted(self):
        """The whole point: R1000 at 15% must show up in June's return."""
        invoice = self._convert_in_june(self._quote_raised_in_march())
        self.assertEqual(invoice.date_issued, date(2026, 6, 15))
        self.assertEqual(self._output_vat(JUNE), Decimal("150.00"))

    def test_it_does_not_fall_back_into_the_period_the_quote_was_raised(self):
        """March is already filed. The VAT must not appear there after the
        fact, which is what dating by date_created would do."""
        self._convert_in_june(self._quote_raised_in_march())
        self.assertEqual(self._output_vat(MARCH), Decimal("0.00"))

    def test_it_is_declared_exactly_once(self):
        self._convert_in_june(self._quote_raised_in_march())
        both = self._output_vat(MARCH) + self._output_vat(JUNE)
        self.assertEqual(both, Decimal("150.00"))

    # ---- the ordinary case still works ----------------------------------

    def test_an_invoice_raised_directly_is_declared_when_it_was_raised(self):
        with mock.patch("billing.models.timezone.localdate", return_value=date(2026, 6, 3)):
            invoice = Invoice.objects.create(
                customer=self.customer, status=Invoice.Status.UNPAID,
                date_due=date(2026, 6, 30),
            )
            InvoiceItem.objects.create(
                invoice=invoice, description="Monthly", quantity=Decimal("1.00"),
                unit_price=Decimal("200.00"), tax_rate_pct=Decimal("15.00"),
            )
            invoice.recalc_totals()
        invoice.refresh_from_db()
        self.assertEqual(invoice.date_issued, date(2026, 6, 3))
        self.assertEqual(self._output_vat(JUNE), Decimal("30.00"))

    def test_the_issue_date_is_stamped_once_and_does_not_move(self):
        """A later edit, or a status going to PAID, must not shift an
        invoice into a different VAT period after it has been declared."""
        with mock.patch("billing.models.timezone.localdate", return_value=date(2026, 6, 3)):
            invoice = Invoice.objects.create(
                customer=self.customer, status=Invoice.Status.UNPAID,
                date_due=date(2026, 6, 30),
            )
        with mock.patch("billing.models.timezone.localdate", return_value=date(2026, 9, 1)):
            invoice.status = Invoice.Status.PAID
            invoice.save(update_fields=["status"])
        invoice.refresh_from_db()
        self.assertEqual(invoice.date_issued, date(2026, 6, 3))

    def test_a_quote_that_is_never_converted_has_no_issue_date(self):
        invoice = self._quote_raised_in_march()
        self.assertIsNone(invoice.date_issued)

    # ---- the document ----------------------------------------------------

    def test_the_pdf_shows_the_date_it_became_an_invoice(self):
        """Not the date the quote was raised, which is what a converted
        quote's tax invoice used to print."""
        from notifications.pdf import render_invoice_pdf

        invoice = self._convert_in_june(self._quote_raised_in_march())
        captured = {}

        def fake_render(template_name, context):
            captured.update(context)
            return "<html><body></body></html>"

        with mock.patch("notifications.pdf.render_to_string", fake_render):
            render_invoice_pdf(invoice, "Skybre")
        self.assertEqual(captured["document_date"], date(2026, 6, 15))

    def test_a_quotes_own_pdf_still_shows_when_it_was_raised(self):
        from notifications.pdf import render_invoice_pdf

        invoice = self._quote_raised_in_march()
        captured = {}

        def fake_render(template_name, context):
            captured.update(context)
            return "<html><body></body></html>"

        with mock.patch("notifications.pdf.render_to_string", fake_render):
            render_invoice_pdf(invoice, "Skybre")
        self.assertEqual(captured["document_date"], date(2026, 3, 10))
