"""Package #183 (16 Oct 2026): recurring charges and pro-rata lines on a quote.

WHAT WAS WRONG (quote O-000012, Rudolph's screenshot)

  * "Public IP Rental" was picked as a Recurring charge line, but printed
    under "Once-off" -- kind_of() only treated TARIFF lines as monthly.
  * Both lines were pro rata 16/10 - 31/10 (16 of 31 days), and the quote
    printed the part-month amount (R306.48) as "Monthly thereafter". The
    30 Mbps line is really about R593.79 a month incl. VAT.

THE NUMBERS USED

  30 Mbps   monthly R516.34 excl -> pro rata 16/31 = R266.50
  IP rental monthly R 15.00 excl -> pro rata 16/31 = R  7.74
  router    R3 500.00 excl (once-off)
"""
import datetime
from decimal import Decimal

from django.template.loader import render_to_string
from django.test import TestCase

from customers.models import Customer

from . import quote_totals
from .models import Invoice, InvoiceItem


class QuoteProRataSplitTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            full_name="Pro Rata Test", customer_id="CUS-009183",
            email="prorata@example.com", status=Customer.Status.ACTIVE,
        )

    def _quote(self):
        return Invoice.objects.create(
            customer=self.customer, status=Invoice.Status.QUOTE, total=Decimal("0.00"),
            date_due=datetime.date(2026, 10, 23),
        )

    def _line(self, invoice, description, price, item_type, **kwargs):
        return InvoiceItem.objects.create(
            invoice=invoice, description=description, quantity=Decimal("1"),
            unit_price=Decimal(price), tax_rate_pct=Decimal("15"),
            item_type=item_type, **kwargs
        )

    def _ip_rental(self, quote, **kwargs):
        return self._line(quote, "Public IP Rental", kwargs.pop("price", "15.00"),
                          InvoiceItem.ItemType.RECURRING_CHARGE, **kwargs)

    def _plan(self, quote, **kwargs):
        return self._line(quote, "30mbps BF Vuma (monthly)", kwargs.pop("price", "516.34"),
                          InvoiceItem.ItemType.TARIFF, **kwargs)

    def _o000012(self):
        """The quote from the screenshot, as #183's editor now saves it."""
        quote = self._quote()
        self._ip_rental(quote, price="7.74", monthly_price=Decimal("15.00"))
        self._plan(quote, price="266.50", monthly_price=Decimal("516.34"))
        quote.recalc_totals()
        quote.save()
        return quote

    # --- which pile ---------------------------------------------------------

    def test_a_recurring_charge_line_is_monthly(self):
        """THE REPORTED BUG: IP rental printed under Once-off."""
        line = self._ip_rental(self._quote())
        self.assertEqual(quote_totals.kind_of(line), quote_totals.RECURRING)

    def test_a_recurring_charge_line_can_still_be_forced_once_off(self):
        line = self._ip_rental(self._quote(), billing_kind=InvoiceItem.BillingKind.ONCE_OFF)
        self.assertEqual(quote_totals.kind_of(line), quote_totals.ONCE_OFF)

    def test_a_stock_item_plus_ip_rental_is_split(self):
        """Full-month quote, no pro rata: IP rental lands under Monthly."""
        quote = self._quote()
        self._line(quote, "Router", "3500.00", InvoiceItem.ItemType.PRODUCT)
        self._ip_rental(quote)
        split = quote_totals.split_totals(quote)
        self.assertEqual(split["once_off"]["excl"], Decimal("3500.00"))
        self.assertEqual(split["recurring"]["excl"], Decimal("15.00"))
        self.assertFalse(split["has_pro_rata"])
        self.assertIsNone(split["first_month"])

    # --- pro rata -------------------------------------------------------------

    def test_o000012_first_month_and_monthly_thereafter(self):
        split = quote_totals.split_totals(self._o000012())

        self.assertTrue(split["has_pro_rata"])
        self.assertEqual(split["once_off"]["count"], 0)
        # First month = what the pro-rata lines charge (as the old PDF showed,
        # plus the IP rental that used to sit under Once-off).
        self.assertEqual(split["first_month"]["excl"], Decimal("274.24"))
        self.assertEqual(split["first_month"]["tax"], Decimal("41.14"))
        self.assertEqual(split["first_month"]["incl"], Decimal("315.38"))
        # Monthly thereafter = full monthly prices, not the part month.
        self.assertEqual(split["recurring"]["excl"], Decimal("531.34"))
        self.assertEqual(split["recurring"]["tax"], Decimal("79.70"))
        self.assertEqual(split["recurring"]["incl"], Decimal("611.04"))

    def test_each_pile_adds_up_within_itself(self):
        split = quote_totals.split_totals(self._o000012())
        for pile in ("first_month", "recurring"):
            self.assertEqual(split[pile]["excl"] + split[pile]["tax"], split[pile]["incl"], pile)

    def test_the_stored_document_total_is_untouched(self):
        """The quote still charges the part month; only the reporting changed."""
        quote = self._o000012()
        self.assertEqual(quote.total, Decimal("315.38"))

    def test_a_pro_rata_only_quote_is_split_even_without_once_off_lines(self):
        """Before: one "Total" of the part month, read as the monthly price."""
        quote = self._quote()
        self._plan(quote, price="266.50", monthly_price=Decimal("516.34"))
        self.assertIsNotNone(quote_totals.split_totals(quote))

    def test_a_full_month_service_only_quote_is_still_not_split(self):
        quote = self._quote()
        self._plan(quote)
        self._ip_rental(quote)
        self.assertIsNone(quote_totals.split_totals(quote))

    def test_an_old_line_without_monthly_price_uses_its_unit_price(self):
        """Quotes saved before #183 have no monthly_price: unchanged figures."""
        quote = self._quote()
        self._line(quote, "Router", "3500.00", InvoiceItem.ItemType.PRODUCT)
        self._plan(quote, price="266.50")
        split = quote_totals.split_totals(quote)
        self.assertEqual(split["recurring"]["excl"], Decimal("266.50"))
        self.assertFalse(split["has_pro_rata"])

    # --- the page -------------------------------------------------------------

    def test_the_pdf_prints_first_month_and_full_monthly(self):
        from notifications import pdf

        quote = self._o000012()
        captured = {}
        real = pdf._render_pdf

        def capture(template, context):
            captured["html"] = render_to_string(template, context)
            return b""

        pdf._render_pdf = capture
        try:
            pdf.render_invoice_pdf(quote, "Skybre")
        finally:
            pdf._render_pdf = real
        html = captured["html"]
        self.assertIn("First month total:", html)
        self.assertIn("R315.38", html)
        self.assertIn("Monthly thereafter:", html)
        self.assertIn("R611.04", html)
        self.assertNotIn("Once-off total:", html)


class InvoiceItemMonthlyPriceApiTests(TestCase):
    def test_serializer_accepts_and_returns_monthly_price(self):
        from .serializers import InvoiceItemSerializer

        s = InvoiceItemSerializer(data={
            "item_type": "custom", "description": "Retainer — pro rata",
            "quantity": "1", "unit_price": "100.00", "tax_rate_pct": "15",
            "monthly_price": "200.00", "billing_kind": "recurring",
        })
        self.assertTrue(s.is_valid(), s.errors)
        self.assertEqual(s.validated_data["monthly_price"], Decimal("200.00"))

    def test_negative_monthly_price_is_refused(self):
        from .serializers import InvoiceItemSerializer

        s = InvoiceItemSerializer(data={
            "item_type": "custom", "description": "x", "quantity": "1",
            "unit_price": "100.00", "tax_rate_pct": "15", "monthly_price": "-1",
        })
        self.assertFalse(s.is_valid())
        self.assertIn("monthly_price", s.errors)
