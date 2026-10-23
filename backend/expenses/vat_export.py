"""The VAT return as a spreadsheet -- .xlsx to read, .csv to import.

WHY THIS IS NOT JUST THE PDF WITH COMMAS.

The PDF is the thing you keep on file; it is a picture of a return. What
somebody filing actually does is different: they key six or seven numbers
into eFiling and then, when a figure looks wrong, they need the lines
behind it. So this exports both -- the summary boxes AND the documents
each box was built from, in a form you can sort, filter and total
yourself.

WHY IT SHARES THE SUMMARY'S QUERIES RATHER THAN REPEATING THEM.

The failure mode for an export like this is quiet: it grows its own copy
of "which invoices count", the two copies drift by one status or one date
boundary, and from then on the spreadsheet and the return disagree with
nobody noticing which is right. Every row here comes from vat_sources.py
-- the same filters the on-screen figures are summed from -- and
test_vat_export.py asserts the exported detail totals to exactly what the
return says. If those ever part company, the test fails rather than the
accountant finding out.

NUMBERS, NOT FORMATTED TEXT.

Cells carry real numbers with a display format applied, and the CSV
carries plain unformatted decimals -- no R, no thousands separators. A
spreadsheet full of "R 1 234,56" strings cannot be summed, which defeats
the point of exporting it, and a CSV with thousands separators re-imports
as text or, worse, as the wrong number.
"""
import csv
import io
from decimal import Decimal

CENTS = "#,##0.00"

SUMMARY_NOTE = (
    "Figures are on the invoice (accrual) basis. Output VAT counts invoices by the date they "
    "became tax invoices, not the date they were raised. Input VAT counts supplier invoices by "
    "the supplier's own invoice date. Check against your own records before filing."
)


def _money(value):
    """Decimals stay Decimals. openpyxl writes them as numbers, and the
    CSV writer renders them without separators -- both are what the
    receiving program wants."""
    return Decimal(value or 0)


# --------------------------------------------------------------------------
# The rows. One function per sheet, each returning (headers, rows) so the
# xlsx and the csv writers below cannot show different columns.
# --------------------------------------------------------------------------

def summary_rows(vat_return):
    """The boxes somebody keys into eFiling, in the order they read them.

    Labelled in words rather than VAT201 box numbers on purpose: the box
    numbering is SARS's and changes with their form, and a stale box
    number on a spreadsheet is worse than no box number -- it invites
    keying a figure into the wrong field with apparent authority.
    """
    output = vat_return["output"]
    input_ = vat_return["input"]
    rows = [
        ("Period start", vat_return["period_start"], None),
        ("Period end", vat_return["period_end"], None),
        ("Company", vat_return["company_name"], None),
        ("VAT number", vat_return["vat_number"] or "Not set", None),
        ("Basis", "Invoice (accrual)", None),
        ("", "", None),
        ("Standard-rated supplies (excl VAT)", _money(output["standard_rated_supplies"]), CENTS),
        ("Zero-rated supplies", _money(output["zero_rated_supplies"]), CENTS),
        ("Output VAT", _money(output["output_vat"]), CENTS),
        ("Invoices counted", output["invoice_count"], None),
        ("", "", None),
        ("Purchases (excl VAT) — expenses", _money(input_["expenses"]["purchases_excl_vat"]), CENTS),
        ("Input VAT — expenses", _money(input_["expenses"]["input_vat"]), CENTS),
        ("Expenses counted", input_["expenses"]["count"], None),
        ("Purchases (excl VAT) — stock receipts", _money(input_["stock"]["purchases_excl_vat"]), CENTS),
        ("Input VAT — stock receipts", _money(input_["stock"]["input_vat"]), CENTS),
        ("Stock receipts counted", input_["stock"]["count"], None),
        ("Purchases (excl VAT) — total", _money(input_["purchases_excl_vat"]), CENTS),
        ("Input VAT — total", _money(input_["input_vat"]), CENTS),
        ("", "", None),
        ("Net VAT", _money(vat_return["net_vat"]), CENTS),
        ("Net VAT direction", vat_return["net_vat_direction"], None),
    ]

    # The two caveats that change what the number means. Carried in the
    # file rather than left on the screen it was exported from, because
    # the file is what gets emailed to the accountant.
    if input_["stock"]["receipts_missing_vat"]:
        rows += [
            ("", "", None),
            (
                "Stock receipts with no VAT recorded",
                input_["stock"]["receipts_missing_vat"],
                None,
            ),
            (
                "Note",
                "Those receipts contribute cost but no VAT, so Input VAT above may be understated.",
                None,
            ),
        ]
    credit = vat_return["credit_notes"]
    if credit["count"]:
        rows += [
            ("", "", None),
            ("Approved credit notes in period", credit["count"], None),
            ("Credit note value", _money(credit["total_amount"]), CENTS),
            (
                "Note",
                "Credit notes carry no VAT breakdown, so they are NOT netted off Output VAT above.",
                None,
            ),
        ]
    if vat_return["duplicate_claims"]:
        rows += [
            ("", "", None),
            (
                "Possible double claims",
                len(vat_return["duplicate_claims"]),
                None,
            ),
            (
                "Note",
                "See the Warnings sheet. These are NOT deducted from any figure above.",
                None,
            ),
        ]
    return ["Item", "Value"], [(label, value) for label, value, _fmt in rows], [
        fmt for _label, _value, fmt in rows
    ]


OUTPUT_HEADERS = [
    "Invoice number", "Date issued", "Customer", "Status",
    "Standard-rated (excl VAT)", "Zero-rated", "Output VAT",
]


def output_detail_rows(period_start, period_end):
    """One row per invoice, not per line -- an invoice is the document
    being reconciled against, and a per-line export of a year's invoicing
    is unreadable. The split between standard- and zero-rated is kept
    because that is the one thing a reader checks per invoice."""
    from .vat_sources import output_vat_items

    items = output_vat_items(period_start, period_end).select_related("invoice", "invoice__customer")

    by_invoice = {}
    for item in items:
        invoice = item.invoice
        bucket = by_invoice.setdefault(invoice.pk, {
            "invoice": invoice,
            "standard": Decimal("0"),
            "zero": Decimal("0"),
            "vat": Decimal("0"),
        })
        line_excl = item.quantity * item.unit_price
        if item.tax_rate_pct and item.tax_rate_pct > 0:
            bucket["standard"] += line_excl
            bucket["vat"] += line_excl * item.tax_rate_pct / Decimal("100")
        else:
            bucket["zero"] += line_excl

    rows = []
    cents = Decimal("0.01")
    for bucket in by_invoice.values():
        invoice = bucket["invoice"]
        rows.append([
            invoice.number or f"#{invoice.pk}",
            invoice.date_issued or invoice.date_created,
            getattr(invoice.customer, "full_name", "") or "",
            invoice.get_status_display(),
            bucket["standard"].quantize(cents),
            bucket["zero"].quantize(cents),
            bucket["vat"].quantize(cents),
        ])
    rows.sort(key=lambda r: (r[1], str(r[0])))
    return OUTPUT_HEADERS, rows


INPUT_HEADERS = [
    "Source", "Date", "Supplier", "Reference", "Description",
    "Amount (excl VAT)", "VAT rate %", "Input VAT",
]


def input_detail_rows(period_start, period_end):
    """Expenses and stock receipts in one list, with a Source column.

    One list rather than two sheets because the question being asked of
    it -- "what did we claim input VAT on" -- does not care which screen
    the document was captured on, and splitting them means adding two
    numbers by hand to check a total.
    """
    from .vat_sources import input_vat_expenses, input_vat_stock_receipts

    rows = []
    for expense in input_vat_expenses(period_start, period_end).select_related("supplier"):
        supplier = expense.supplier.name if expense.supplier else (expense.supplier_name or "")
        rows.append([
            "Expense",
            expense.date,
            supplier,
            expense.invoice_number or "",
            expense.description,
            _money(expense.amount_excl_vat),
            _money(expense.vat_rate_pct),
            _money(expense.vat_amount),
        ])

    for receipt in input_vat_stock_receipts(period_start, period_end).select_related("supplier"):
        excl, vat, _incl = receipt.totals
        rows.append([
            "Stock receipt",
            receipt.invoice_date,
            receipt.supplier.name if receipt.supplier else "",
            receipt.invoice_number or "",
            # A receipt has lines rather than one description; the line
            # count is the honest summary of it at this level. len() over
            # .count() so it uses the prefetch the shared queryset already
            # does -- .count() would re-query once per receipt.
            f"{len(receipt.lines.all())} line(s)",
            _money(excl),
            # Deliberately blank rather than 15: a receipt can mix rates,
            # and printing one rate for a mixed document is a lie that
            # reconciles to nothing.
            "",
            _money(vat),
        ])

    rows.sort(key=lambda r: (r[1], r[0]))
    return INPUT_HEADERS, rows


WARNING_HEADERS = [
    "Supplier", "Invoice number", "Stock receipt date", "Stock receipt VAT",
    "Expense date", "Expense description", "Expense VAT",
]


def warning_rows(vat_return):
    rows = []
    for warning in vat_return["duplicate_claims"]:
        rows.append([
            warning["supplier"],
            warning["invoice_number"],
            warning["receipt_date"],
            _money(warning["receipt_vat"]),
            warning["expense_date"],
            warning["expense_description"],
            _money(warning["expense_vat"]),
        ])
    return WARNING_HEADERS, rows


def reconciliation_rows(title, rows, vat_return):
    """The totals line at the foot of a detail sheet -- and, when it does
    not agree with the return, a line saying so.

    THIS IS NOT PEDANTRY, AND THE DIFFERENCE IS REAL.

    Each invoice stores its own VAT rounded once, per invoice: that is the
    number printed on the document a customer holds, and the number the
    detail rows here show. The return's headline Output VAT is summed
    across line items and rounded once at the very end -- see
    _sum_output_vat. Those two are not the same arithmetic, and on
    fractional-cent lines they part company: three invoices whose VAT is
    R5.0055 each are R15.02 summed-then-rounded and R15.03 as the three
    documents actually read.

    A cent. But a spreadsheet whose rows visibly do not add up to its own
    summary is a spreadsheet nobody trusts again, and the reader has no
    way to tell which figure is the one that was filed. So both are
    printed, labelled, with the one that was declared named as such.
    Neither figure is altered to make them match -- the headline is what
    went to SARS and the rows are what the documents say, and both of
    those are true.
    """
    if not rows:
        return []
    if title == "Output VAT":
        declared = _money(vat_return["output"]["output_vat"])
        from_rows = sum((row[6] for row in rows), Decimal("0"))
        label = "Output VAT"
    elif title == "Input VAT":
        declared = _money(vat_return["input"]["input_vat"])
        from_rows = sum((row[7] for row in rows), Decimal("0"))
        label = "Input VAT"
    else:
        return []

    out = [(f"{label} — total of the rows above", from_rows)]
    if from_rows != declared:
        out.append((f"{label} — as declared on the return", declared))
        out.append((
            "Difference is rounding: each document rounds its own VAT, the return rounds once at "
            "the end. The declared figure is the one that was filed.",
            declared - from_rows,
        ))
    return out


# --------------------------------------------------------------------------
# Writers
# --------------------------------------------------------------------------

SECTIONS = ("summary", "output", "input", "warnings")


def build_csv(vat_return, section):
    """One section per file. A CSV holding several stacked tables with
    blank lines between them looks fine in Excel and imports into nothing
    -- and importing is the only reason to choose CSV over the xlsx."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    if section == "summary":
        headers, rows, _formats = summary_rows(vat_return)
        writer.writerow(headers)
        for label, value in rows:
            writer.writerow([label, value])
    elif section == "output":
        headers, rows = output_detail_rows(vat_return["period_start"], vat_return["period_end"])
        writer.writerow(headers)
        writer.writerows(rows)
        for label, value in reconciliation_rows("Output VAT", rows, vat_return):
            writer.writerow([label] + [""] * (len(headers) - 2) + [value])
    elif section == "input":
        headers, rows = input_detail_rows(vat_return["period_start"], vat_return["period_end"])
        writer.writerow(headers)
        writer.writerows(rows)
        for label, value in reconciliation_rows("Input VAT", rows, vat_return):
            writer.writerow([label] + [""] * (len(headers) - 2) + [value])
    else:
        headers, rows = warning_rows(vat_return)
        writer.writerow(headers)
        writer.writerows(rows)

    return buffer.getvalue().encode("utf-8-sig")
    # utf-8-sig: Excel on Windows opens a plain UTF-8 CSV as Latin-1 and
    # turns every supplier name with an accent in it into mojibake. The
    # BOM is what makes it read the file correctly, and every other tool
    # ignores it.


def build_xlsx(vat_return):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter

    workbook = Workbook()

    summary_headers, summary, formats = summary_rows(vat_return)
    sheet = workbook.active
    sheet.title = "Summary"
    sheet.append(summary_headers)
    for (label, value), number_format in zip(summary, formats):
        sheet.append([label, value])
        if number_format:
            sheet.cell(row=sheet.max_row, column=2).number_format = number_format
    _style(sheet, widths=[44, 30])
    # The caveat travels with the file, below the figures, where somebody
    # reading the numbers will actually pass it.
    sheet.append([])
    sheet.append([SUMMARY_NOTE])
    sheet.cell(row=sheet.max_row, column=1).alignment = Alignment(wrap_text=True, vertical="top")
    sheet.cell(row=sheet.max_row, column=1).font = Font(italic=True, size=9)

    for title, (headers, rows), money_from in (
        ("Output VAT", output_detail_rows(vat_return["period_start"], vat_return["period_end"]), 4),
        ("Input VAT", input_detail_rows(vat_return["period_start"], vat_return["period_end"]), 5),
        ("Warnings", warning_rows(vat_return), 3),
    ):
        sheet = workbook.create_sheet(title)
        sheet.append(headers)
        for row in rows:
            sheet.append(row)
        for label, value in reconciliation_rows(title, rows, vat_return):
            sheet.append([label] + [""] * (len(headers) - 2) + [value])
        for row in sheet.iter_rows(min_row=2, min_col=money_from + 1):
            for cell in row:
                if isinstance(cell.value, Decimal):
                    cell.number_format = CENTS
        _style(sheet, widths=[18] * len(headers))
        if not rows:
            sheet.append([])
            sheet.append(["Nothing in this period."])

    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def _style(sheet, widths):
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    # Freeze the header so a long Input VAT sheet stays readable while
    # scrolled -- the one spreadsheet nicety that is actually worth the
    # line of code.
    sheet.freeze_panes = "A2"
