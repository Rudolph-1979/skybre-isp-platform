"""Does every cell on a statement actually fit the column it is in?

WHAT HAPPENED, TWICE

The Account Statement went to a customer with every date printed over two
lines -- "Sept. 1," above "2026" -- and every amount printed as "R" above
its own figure. The period statement was doing the same thing. Nothing in
the suite said a word, because the tests that existed asserted the bytes
start with %PDF and that the file is big enough to be a page. Both were
true. A document can be perfectly valid and completely unreadable.

WHY A CELL WRAPS

Two numbers have to agree and there was no test holding them together:

  * the column's declared width, in points;
  * the width of the widest string that column can hold, in points.

table.items cells carry `padding: 6px 8px`, and xhtml2pdf renders 1px as
1pt here, so a left-aligned cell loses 2 x padding. A RIGHT-ALIGNED cell
loses THREE x padding: reportlab insets the frame and then right-aligns
the paragraph inside the inset, charging the same padding twice on that
side. That is the whole reason the money columns were wrapping -- a 58pt
column with 8px padding has 34pt of usable room, and "R 435.85" is 38.6pt.
The statement templates override those cells to 4px for exactly this.

WHAT THIS ASSERTS

The arithmetic, not the picture: every column is wide enough for the
worst realistic string it can be asked to print, the header and body rows
agree about the widths, and the columns still fit the page. reportlab's
own metrics do the measuring, so this is the same number the renderer
will use.

It does not need to render anything, which is why it runs in this suite
instead of needing a PDF reader as a test dependency.
"""
import pathlib
import re

from django.template.loader import render_to_string
from django.test import TestCase
from reportlab.pdfbase.pdfmetrics import stringWidth

TEMPLATE_DIR = pathlib.Path(__file__).resolve().parent / "templates" / "notifications"

#: _pdf_base.html: A4 (595.28pt) less @page margin 2cm a side.
CONTENT_WIDTH = 595.28 - 2 * 56.69

#: table.items td font-size.
FONT, SIZE = "Helvetica", 9.5

CELL = re.compile(r"<(th|td)\b([^>]*)>(.*?)</\1>", re.S)
WIDTH = re.compile(r"width:\s*([\d.]+)pt")
PAD_LEFT = re.compile(r"padding-left:\s*([\d.]+)px")
PAD_RIGHT = re.compile(r"padding-right:\s*([\d.]+)px")

#: The inherited padding from table.items in _pdf_base.html, in px == pt.
DEFAULT_PADDING = 8.0

#: The worst realistic content of each column, in order, per template.
#: An entry is (label, sample). Samples are real shapes from the live
#: data, stretched one order of magnitude where a bigger account could
#: plausibly reach it.
COLUMNS = {
    "statement_pdf.html": [
        ("Invoice", "INV202606004199"),
        ("Date", "15/09/2026"),
        ("Due", "15/09/2026"),
        # Quotes and pro formas never reach this document --
        # notifications.services excludes PRE_INVOICE_STATUSES -- so the
        # longest status it can print is "Cancelled", not
        # "Pro Forma Invoice".
        ("Total", "R 123456.78"),
        ("Paid", "R 123456.78"),
        ("Balance", "R -123456.78"),
        ("Status", "Cancelled"),
    ],
    "period_statement_pdf.html": [
        ("Date", "15/09/2026"),
        # Detail is the ONE column deliberately narrower than its longest
        # content: "Payment received - Bank Transfer" is 144pt and is
        # meant to wrap. The sample is the widest thing that must NOT.
        ("Detail", "Balance brought forward"),
        ("Reference", "RCP2026-12-01726"),
        ("Debit", "R 123456.78"),
        ("Credit", "R 123456.78"),
        ("Balance", "R -123456.78"),
    ],
}

#: Django's default date format. If any of these reaches a PDF, a
#: |date:"d/m/Y" has been dropped somewhere.
LONG_MONTHS = re.compile(
    r"\b(Jan|Feb|March|April|May|June|July|Aug|Sept|Oct|Nov|Dec)\.?\s+\d{1,2},"
)


def cells(markup):
    """Every th/td in `markup`, as (tag, attributes, inner)."""
    return [(m.group(1), m.group(2), m.group(3)) for m in CELL.finditer(markup)]


def geometry(attributes):
    """The declared width and horizontal padding of one cell, in points."""
    width = WIDTH.search(attributes)
    left = PAD_LEFT.search(attributes)
    right = PAD_RIGHT.search(attributes)
    return (
        float(width.group(1)) if width else None,
        float(left.group(1)) if left else DEFAULT_PADDING,
        float(right.group(1)) if right else DEFAULT_PADDING,
        'class="num"' in attributes,
    )


def usable(width, pad_left, pad_right, right_aligned):
    """How much of a cell a single line of text may actually occupy.

    The third helping of padding on a right-aligned cell is not a guess:
    it was measured by rendering a probe table under the pinned
    xhtml2pdf 0.2.17 and reading the drawn word boxes back out of the
    PDF."""
    if right_aligned:
        return width - pad_left - 2 * pad_right
    return width - pad_left - pad_right


class ItemsTableGeometryTests(TestCase):
    """The declared widths, against reportlab's own metrics."""

    def _items_table(self, name):
        text = (TEMPLATE_DIR / name).read_text()
        start = text.index('<table class="items"')
        end = text.index("</table>", start)
        return text[start:end]

    def _header_cells(self, table):
        head = table[table.index("<thead>"):table.index("</thead>")]
        return cells(head)

    def _body_rows(self, table):
        body = table[table.index("<tbody>"):]
        rows = []
        for row in re.findall(r"<tr>(.*?)</tr>", body, re.S):
            found = cells(row)
            # The {% empty %} row is one colspan cell, not a column layout.
            if "colspan" not in row:
                rows.append(found)
        return rows

    def test_every_column_fits_its_widest_realistic_cell(self):
        for name, columns in COLUMNS.items():
            table = self._items_table(name)
            header = self._header_cells(table)
            self.assertEqual(
                len(header), len(columns),
                f"{name}: {len(header)} columns in the template, "
                f"{len(columns)} described here -- update COLUMNS",
            )
            for (label, sample), (_, attributes, _) in zip(columns, header):
                width, left, right, num = geometry(attributes)
                self.assertIsNotNone(
                    width, f"{name}: the {label} column declares no width. "
                    "Automatic allocation under xhtml2pdf 0.2.17 is what "
                    "aborted this render once already.",
                )
                room = usable(width, left, right, num)
                needed = stringWidth(sample, FONT, SIZE)
                self.assertGreaterEqual(
                    room, needed,
                    f"{name}: the {label} column is {width}pt, which leaves "
                    f"{room:.1f}pt of room, and {sample!r} is {needed:.1f}pt. "
                    "It will print over two lines on a real customer's "
                    "statement.",
                )

    def test_the_body_rows_declare_the_same_geometry_as_the_header(self):
        """A column that drifts in the body only is invisible until a
        real row is long enough to notice."""
        for name in COLUMNS:
            table = self._items_table(name)
            expected = [geometry(a) for _, a, _ in self._header_cells(table)]
            for index, row in enumerate(self._body_rows(table)):
                self.assertEqual(
                    [geometry(a) for _, a, _ in row], expected,
                    f"{name}: body row {index} does not match the header's "
                    "widths and paddings",
                )

    def test_the_table_still_fits_the_page(self):
        for name in COLUMNS:
            table = self._items_table(name)
            opening = WIDTH.search(table[:table.index(">")])
            self.assertIsNotNone(
                opening, f"{name}: the items table declares no width of its own",
            )
            declared = float(opening.group(1))
            total = sum(geometry(a)[0] for _, a, _ in self._header_cells(table))
            self.assertLessEqual(
                total, declared,
                f"{name}: columns total {total}pt inside a {declared}pt table",
            )
            self.assertLessEqual(
                declared, CONTENT_WIDTH,
                f"{name}: the table is {declared}pt and the page holds "
                f"{CONTENT_WIDTH:.1f}pt",
            )

    def test_every_money_cell_overrides_the_inherited_padding(self):
        """8px on a right-aligned cell costs 24pt of a column that is
        only 68pt wide. Every amount on both statements wrapped."""
        for name in COLUMNS:
            table = self._items_table(name)
            for tag, attributes, _ in cells(table):
                if 'class="num"' not in attributes:
                    continue
                _, left, right, _ = geometry(attributes)
                self.assertEqual(
                    (left, right), (4.0, 4.0),
                    f"{name}: a right-aligned <{tag}> still uses the "
                    "inherited 8px padding",
                )


class RenderedDateFormatTests(TestCase):
    """The rendered page, not the template: no long-form dates anywhere."""

    def _assert_no_long_dates(self, html, name):
        found = LONG_MONTHS.findall(html)
        self.assertEqual(
            found, [],
            f"{name} rendered a long-form date ({found}). Django's default "
            'format is 56.5pt and the date columns hold 50 -- use |date:"d/m/Y".',
        )

    def test_the_account_statement_prints_short_dates(self):
        from .test_pdf_templates import BASE_CONTEXT, Inv, ZERO

        html = render_to_string(
            "notifications/statement_pdf.html",
            {**BASE_CONTEXT, "statement_date": BASE_CONTEXT["generated_date"],
             "invoices": [Inv()], "balance": ZERO},
        )

        self._assert_no_long_dates(html, "statement_pdf.html")
        self.assertIn("22/09/2026", html)

    def test_the_period_statement_prints_short_dates(self):
        from .test_pdf_templates import BASE_CONTEXT, PERIOD

        html = render_to_string(
            "notifications/period_statement_pdf.html",
            {**BASE_CONTEXT, "statement": PERIOD},
        )

        self._assert_no_long_dates(html, "period_statement_pdf.html")
        self.assertIn("01/08/2026", html)

    def test_the_footer_prints_a_short_date(self):
        """The last one found, and only because the PDF text was read
        back: "Generated by Skybre on Sept. 22, 2026." sits on every
        document built on _pdf_base.html."""
        from .test_pdf_templates import BASE_CONTEXT, PERIOD

        html = render_to_string(
            "notifications/period_statement_pdf.html",
            {**BASE_CONTEXT, "statement": PERIOD},
        )

        self.assertIn("Generated by Skybre on 22/09/2026.", html)
