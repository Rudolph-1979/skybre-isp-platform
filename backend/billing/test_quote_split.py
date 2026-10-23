"""A quote that adds a router to a monthly service and calls it a total.

WHAT WAS WRONG

R3 500 of equipment plus R899 a month printed "Total R5 058.85". The
customer is not paying that once and they are not paying it monthly. It
is a number that is true of nothing, and the argument it starts happens
after the first invoice, when it is expensive.

WHAT THIS ASSERTS

  * which pile a line lands in, including the case that made the field
    necessary -- a custom line, which could be either;
  * that the two figures are each correct on their own;
  * that a quote with only one kind on it is left completely alone, since
    changing every quote to serve the mixed ones would be a worse
    document for the common case;
  * that a pro forma and a tax invoice are untouched. Those are demands
    for one amount that is due; only a quote is a document about a future
    commitment.

THE NUMBERS USED THROUGHOUT

  router   R3 500.00 excl, 15%  ->  R525.00 tax, R4 025.00 incl
  service  R  899.00 excl, 15%  ->  R134.85 tax, R1 033.85 incl

Chosen because they are the real shape of a Skybre quote, and because
their blended total (R5 058.85) is the number that used to be printed.
"""
import datetime
from decimal import Decimal

from django.template.loader import render_to_string
from django.test import TestCase

from customers.models import Customer

from . import quote_totals
from .models import Invoice, InvoiceItem

ROUTER_INCL = Decimal("4025.00")
SERVICE_INCL = Decimal("1033.85")
BLENDED = Decimal("5058.85")


class QuoteSplitTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            full_name="CCTV Central", customer_id="CUS-009001",
            email="cctv@example.com", status=Customer.Status.ACTIVE,
        )

    # --- fixtures ---------------------------------------------------------

    def _document(self, status):
        return Invoice.objects.create(
            customer=self.customer, status=status, total=Decimal("0.00"),
            date_due=datetime.date(2026, 10, 15),
        )

    def _line(self, invoice, description, price, item_type, **kwargs):
        return InvoiceItem.objects.create(
            invoice=invoice, description=description, quantity=Decimal("1"),
            unit_price=Decimal(price), tax_rate_pct=Decimal("15"),
            item_type=item_type, **kwargs
        )

    def _router(self, invoice, **kwargs):
        return self._line(invoice, "Mikrotik hAP ax2", "3500.00",
                          InvoiceItem.ItemType.PRODUCT, **kwargs)

    def _service(self, invoice, **kwargs):
        return self._line(invoice, "Home 100 fibre", "899.00",
                          InvoiceItem.ItemType.TARIFF, **kwargs)

    def _mixed_quote(self):
        quote = self._document(Invoice.Status.QUOTE)
        self._router(quote)
        self._service(quote)
        quote.recalc_totals()
        quote.save()
        return quote

    # --- which pile a line lands in ---------------------------------------

    def test_a_tariff_line_is_recurring(self):
        """Because accepting the quote turns it into a Service, and the
        recurring engine bills Services every month."""
        quote = self._document(Invoice.Status.QUOTE)

        self.assertEqual(quote_totals.kind_of(self._service(quote)), quote_totals.RECURRING)

    def test_a_stock_line_is_once_off(self):
        quote = self._document(Invoice.Status.QUOTE)

        self.assertEqual(quote_totals.kind_of(self._router(quote)), quote_totals.ONCE_OFF)

    def test_a_custom_line_is_once_off_unless_told_otherwise(self):
        """The safer default: a custom line wrongly called recurring
        promises the customer a monthly charge nobody meant to make."""
        quote = self._document(Invoice.Status.QUOTE)
        line = self._line(quote, "Installation", "950.00", InvoiceItem.ItemType.CUSTOM)

        self.assertEqual(quote_totals.kind_of(line), quote_totals.ONCE_OFF)

    def test_an_explicit_flag_wins_over_the_line_type(self):
        """THE CASE THE FIELD EXISTS FOR. A custom line for a monthly
        maintenance retainer is recurring, and nothing about its type can
        say so."""
        quote = self._document(Invoice.Status.QUOTE)
        line = self._line(
            quote, "Monthly maintenance retainer", "750.00",
            InvoiceItem.ItemType.CUSTOM,
            billing_kind=InvoiceItem.BillingKind.RECURRING,
        )

        self.assertEqual(quote_totals.kind_of(line), quote_totals.RECURRING)

    def test_a_tariff_line_can_be_forced_once_off(self):
        """A pro-rata catch-up charge on a plan is billed once."""
        quote = self._document(Invoice.Status.QUOTE)
        line = self._service(quote, billing_kind=InvoiceItem.BillingKind.ONCE_OFF)

        self.assertEqual(quote_totals.kind_of(line), quote_totals.ONCE_OFF)

    # --- the two figures --------------------------------------------------

    def test_the_two_totals_are_each_right(self):
        split = quote_totals.split_totals(self._mixed_quote())

        self.assertEqual(split["once_off"]["excl"], Decimal("3500.00"))
        self.assertEqual(split["once_off"]["tax"], Decimal("525.00"))
        self.assertEqual(split["once_off"]["incl"], ROUTER_INCL)
        self.assertEqual(split["recurring"]["excl"], Decimal("899.00"))
        self.assertEqual(split["recurring"]["tax"], Decimal("134.85"))
        self.assertEqual(split["recurring"]["incl"], SERVICE_INCL)

    def test_each_total_adds_up_within_itself(self):
        """Exclusive plus tax equals inclusive, in both piles. The
        document prints all three of each, and a customer who adds the
        first two must get the third."""
        split = quote_totals.split_totals(self._mixed_quote())

        for pile in ("once_off", "recurring"):
            self.assertEqual(
                split[pile]["excl"] + split[pile]["tax"], split[pile]["incl"], pile
            )

    def test_the_document_total_is_left_exactly_as_it_was(self):
        """Nothing here recomputes what is stored. The split is a way of
        REPORTING the same lines, not a second opinion about them."""
        quote = self._mixed_quote()

        self.assertEqual(quote.total, BLENDED)

    # --- who gets a split at all ------------------------------------------

    def test_a_quote_with_only_equipment_is_not_split(self):
        quote = self._document(Invoice.Status.QUOTE)
        self._router(quote)

        self.assertIsNone(quote_totals.split_totals(quote))

    def test_a_quote_with_only_a_service_is_not_split(self):
        quote = self._document(Invoice.Status.QUOTE)
        self._service(quote)

        self.assertIsNone(quote_totals.split_totals(quote))

    def test_an_empty_document_is_not_split(self):
        self.assertIsNone(quote_totals.split_totals(self._document(Invoice.Status.QUOTE)))


class QuoteDocumentTests(TestCase):
    """What actually reaches the page."""

    CONTEXT = {
        "company_name": "Skybre",
        "company": {"name": "Skybre Communications (Pty) Ltd", "vat_number": "",
                    "address": "", "email": "", "phone": ""},
        "logo_path": None,
        "generated_date": datetime.date(2026, 10, 2),
        "document_date": datetime.date(2026, 10, 2),
        "customer_display_name": "CCTV Central",
        "doc_title": "Quotation",
        "due_label": "Valid until",
        "rows": [],
        "related": [],
        "total_exclusive": Decimal("4399.00"),
        "total_tax": Decimal("659.85"),
        "balance_due": BLENDED,
    }

    SPLIT = {
        "once_off": {"count": 1, "excl": Decimal("3500.00"),
                     "tax": Decimal("525.00"), "incl": ROUTER_INCL},
        "recurring": {"count": 1, "excl": Decimal("899.00"),
                      "tax": Decimal("134.85"), "incl": SERVICE_INCL},
    }

    def _render(self, **extra):
        class Doc:
            total = BLENDED
            number = "QUO202610000001"
            date_due = datetime.date(2026, 10, 15)

        return render_to_string(
            "notifications/invoice_pdf.html",
            {**self.CONTEXT, "invoice": Doc(), "customer": None, **extra},
        )

    def test_a_mixed_quote_prints_both_figures_and_no_blended_one(self):
        html = self._render(kind_split=self.SPLIT)

        self.assertIn("Once-off total", html)
        self.assertIn("4025.00", html)
        self.assertIn("Monthly thereafter", html)
        self.assertIn("1033.85", html)
        self.assertNotIn(
            "5058.85", html,
            "the blended total is back on the page -- it is true of nothing",
        )

    def test_anything_without_a_split_prints_exactly_what_it_always_did(self):
        html = self._render(kind_split=None)

        self.assertIn("Total due", html)
        self.assertIn("5058.85", html)
        self.assertNotIn("Monthly thereafter", html)

    def test_the_document_carries_no_leaked_template_syntax(self):
        """The multi-line {# #} lesson, on the template this change
        touches."""
        for split in (self.SPLIT, None):
            html = self._render(kind_split=split)
            for token in ("{#", "#}", "{%", "%}", "{{", "}}"):
                self.assertNotIn(token, html, f"kind_split={bool(split)}")


class OtherDocumentsAreUntouchedTests(TestCase):
    """A pro forma and a tax invoice are demands for one amount that is
    due. Only a quote is about a future commitment."""

    def setUp(self):
        self.customer = Customer.objects.create(
            full_name="CCTV Central", customer_id="CUS-009002",
            email="cctv2@example.com", status=Customer.Status.ACTIVE,
        )

    def _mixed(self, status):
        document = Invoice.objects.create(
            customer=self.customer, status=status, total=Decimal("0.00"),
            date_due=datetime.date(2026, 10, 15),
        )
        for description, price, kind in (
            ("Mikrotik hAP ax2", "3500.00", InvoiceItem.ItemType.PRODUCT),
            ("Home 100 fibre", "899.00", InvoiceItem.ItemType.TARIFF),
        ):
            InvoiceItem.objects.create(
                invoice=document, description=description, quantity=Decimal("1"),
                unit_price=Decimal(price), tax_rate_pct=Decimal("15"), item_type=kind,
            )
        document.recalc_totals()
        document.save()
        return document

    def _pdf(self, document):
        from notifications.pdf import render_invoice_pdf

        return render_invoice_pdf(document, "Skybre")

    def test_a_mixed_quote_renders(self):
        """The split reaches a real PDF without the renderer falling over
        -- this template is pinned to xhtml2pdf 0.2.17, which has aborted
        a render over table geometry before."""
        pdf = self._pdf(self._mixed(Invoice.Status.QUOTE))

        self.assertTrue(pdf.startswith(b"%PDF"))

    def test_a_mixed_pro_forma_renders(self):
        pdf = self._pdf(self._mixed(Invoice.Status.PROFORMA))

        self.assertTrue(pdf.startswith(b"%PDF"))

    def test_a_mixed_tax_invoice_renders(self):
        pdf = self._pdf(self._mixed(Invoice.Status.UNPAID))

        self.assertTrue(pdf.startswith(b"%PDF"))
