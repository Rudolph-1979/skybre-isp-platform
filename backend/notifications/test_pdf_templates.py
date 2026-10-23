"""Template syntax that is not syntax, printed onto a customer's document.

WHAT HAPPENED

`{# ... #}` in a Django template is a SINGLE-LINE comment. Spread it over
two lines and it stops being a comment: it is literal text, and it renders.

Four of them went out across _pdf_base.html, statement_pdf.html and
period_statement_pdf.html, so an Account Statement for a real customer
printed a paragraph of my notes to myself above the heading, and another
one in the middle of their address block. invoice_pdf.html had been using
{% comment %}...{% endcomment %} for its multi-line notes all along.

WHY THE EXISTING TESTS SAID NOTHING

The render tests assert the bytes start with %PDF and that the file is
big enough to be a page. Both were true. A document can be perfectly
valid and completely wrong, and nothing that only weighs the output will
ever notice.

So there are two tests here:

  * a SCAN of every PDF template for the multi-line form, which is cheap,
    needs no rendering, and catches this for templates nobody has thought
    to render in a test yet;
  * a RENDER of each statement, asserting the output carries no template
    syntax at all -- the check that would have caught it.
"""
import datetime
import pathlib
import re
from decimal import Decimal

from django.template.loader import render_to_string
from django.test import TestCase

TEMPLATE_DIR = pathlib.Path(__file__).resolve().parent / "templates" / "notifications"

#: A {# #} that contains a newline. Django does not treat it as a comment.
MULTILINE_COMMENT = re.compile(r"\{#.*?#\}", re.S)

ZERO = Decimal("0.00")


class Cust:
    customer_id = "SMF463"
    full_name = "CCTV Central"
    company_name = "CCTV Central"
    address = "18 Voortrekker Straat"
    city = "Hennenman"
    zip_code = "9445"
    email = "admin@cctvcentral.co.za"
    balance = Decimal("-435.85")


class Inv:
    number = "INV202606004199"
    date_created = datetime.date(2026, 9, 1)
    date_due = datetime.date(2026, 9, 1)
    total = Decimal("435.85")
    paid_amount = Decimal("435.85")
    balance_due = ZERO

    def get_status_display(self):
        return "Paid"


PERIOD = {
    # The template reads `heading` now, so that one file can render both
    # the rolling statement and a named month. billing.statements supplies
    # it; this hand-built fixture has to as well.
    "heading": "Statement for August 2026",
    "period_label": "August 2026",
    "start": datetime.date(2026, 8, 1),
    "end": datetime.date(2026, 8, 31),
    "opening_balance": ZERO,
    "lines": [{
        "date": datetime.date(2026, 8, 1), "description": "Invoice",
        "reference": "INV202606003635", "debit": Decimal("435.85"),
        "credit": ZERO, "balance": Decimal("435.85"),
    }],
    "invoiced": Decimal("435.85"), "paid": ZERO, "credited": ZERO,
    "closing_balance": Decimal("435.85"),
}

BASE_CONTEXT = {
    "company_name": "Skybre",
    "company": {"name": "Skybre Communications (Pty) Ltd"},
    "logo_path": None,
    "generated_date": datetime.date(2026, 9, 22),
    "customer": Cust(),
}


class TemplateCommentScanTests(TestCase):
    def test_no_pdf_template_uses_a_multi_line_hash_comment(self):
        """Cheap, needs no rendering, and covers templates no test renders.

        The single-line form is fine and stays fine. It is the multi-line
        one that silently stops being a comment."""
        offenders = []
        for path in sorted(TEMPLATE_DIR.glob("*.html")):
            text = path.read_text()
            for match in MULTILINE_COMMENT.finditer(text):
                if "\n" in match.group(0):
                    first = match.group(0).strip().splitlines()[0][:60]
                    offenders.append(f"{path.name}: {first}...")

        self.assertEqual(
            offenders, [],
            "multi-line {# #} is not a comment in Django and renders as text. "
            "Use {% comment %}...{% endcomment %}:\n  " + "\n  ".join(offenders),
        )


class RenderedOutputTests(TestCase):
    """The check that would have caught it: look at what came out."""

    def _assert_clean(self, html, template):
        for token in ("{#", "#}", "{%", "%}", "{{", "}}"):
            self.assertNotIn(
                token, html,
                f"{template} rendered template syntax {token!r} into its output",
            )

    def test_the_period_statement_renders_no_template_syntax(self):
        html = render_to_string(
            "notifications/period_statement_pdf.html",
            {**BASE_CONTEXT, "statement": PERIOD},
        )

        self._assert_clean(html, "period_statement_pdf.html")
        self.assertIn("August 2026", html)
        self.assertIn("CCTV Central", html)

    def test_the_account_statement_renders_no_template_syntax(self):
        html = render_to_string(
            "notifications/statement_pdf.html",
            {
                **BASE_CONTEXT,
                "statement_date": datetime.date(2026, 9, 22),
                "invoices": [Inv()],
                "balance": Decimal("-435.85"),
            },
        )

        self._assert_clean(html, "statement_pdf.html")
        self.assertIn("Account Statement", html)

    def test_the_customer_name_is_not_printed_twice(self):
        """CCTV Central's company_name equals their full_name, and the
        address block printed both."""
        html = render_to_string(
            "notifications/statement_pdf.html",
            {
                **BASE_CONTEXT,
                "statement_date": datetime.date(2026, 9, 22),
                "invoices": [Inv()],
                "balance": ZERO,
            },
        )

        self.assertEqual(html.count("CCTV Central"), 1, html)
