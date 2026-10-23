"""Clients per partner / per tariff as an Excel workbook (George, 13 Oct
2026: "Also add a option to export in Excell"). Two sheets: Summary (the
rows as on screen, plus the total) and Clients (one line per client per
row). Money and percentages are written as numbers so they add up in Excel.
All rand figures are incl. VAT (13 Oct 2026).
"""
import io
from decimal import Decimal

CENTS = "#,##0.00"
PCT = "0.0"


def _num(value):
    return None if value in (None, "") else Decimal(str(value))


def build_xlsx(report, column, per_tariff):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    headers = [column, "Clients", "Live services", "Active services", "Monthly incl. VAT"]
    if per_tariff:
        headers += ["Price per line", "Cost per line"]
    headers += ["Monthly cost", "Gross profit", "Markup %", "Margin %", "Uncosted lines"]
    money_cols = {"Monthly incl. VAT", "Price per line", "Cost per line", "Monthly cost", "Gross profit"}
    pct_cols = {"Markup %", "Margin %"}
    ws.append(headers)

    def line(name, r, unit=True):
        out = [name, r["clients"], r["services"], r["active_services"], _num(r["monthly_incl"])]
        if per_tariff:
            out += [_num(r.get("unit_price")), _num(r.get("unit_cost"))] if unit else [None, None]
        out += [_num(r.get("monthly_cost")), _num(r.get("gross_profit")), _num(r.get("markup_pct")),
                _num(r.get("margin_pct")), r.get("uncosted_lines") or 0]
        return out

    for r in report["rows"]:
        ws.append(line(r["name"], r))
    ws.append(line("Total (distinct clients)", report["total"], unit=False))
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)
    _formats(ws, headers, money_cols, pct_cols)
    _style(ws, [28] + [18] * (len(headers) - 1))

    cheaders = [column, "Client", "Client code", "Client status", "Services", "Monthly incl. VAT", "Monthly cost",
                "Discount (negative = charged extra)", "After discount", "Discount detail"]
    client_rows = []
    for r in report["rows"]:
        for c in r.get("client_list") or []:
            services = "; ".join(
                s["tariff"] if s["status"] == "active" else f'{s["tariff"]} ({s["status"]})' for s in c["services"]
            ) or "No live service"
            client_rows.append([r["name"], c["full_name"], c["customer_ref"], c["status"], services,
                                _num(c["monthly_incl"]), _num(c.get("monthly_cost")),
                                _num(c.get("discount")), _num(c.get("monthly_after_discount")),
                                c.get("discount_detail") or ""])

    # The clients under the summary on the first sheet as well (George,
    # 13 Oct 2026: the Clients sheet tab was easy to miss).
    ws.append([])
    ws.append(["Clients"])
    ws.cell(row=ws.max_row, column=1).font = Font(bold=True, size=12)
    ws.append(cheaders)
    head_row = ws.max_row
    for cell in ws[head_row]:
        cell.font = Font(bold=True)
    for row in client_rows:
        ws.append(row)
    for row in ws.iter_rows(min_row=head_row + 1, min_col=6, max_col=9):
        for cell in row:
            cell.number_format = CENTS
    ws.column_dimensions["B"].width = 32
    ws.column_dimensions["E"].width = 34

    cs = wb.create_sheet("Clients")
    cs.append(cheaders)
    for row in client_rows:
        cs.append(row)
    _formats(cs, cheaders, {"Monthly incl. VAT", "Monthly cost", "Discount (negative = charged extra)", "After discount"}, set())
    _style(cs, [24, 32, 14, 14, 40, 18, 16, 14, 16, 40])
    cs.auto_filter.ref = cs.dimensions

    stream = io.BytesIO()
    wb.save(stream)
    return stream.getvalue()


def _formats(ws, headers, money_cols, pct_cols):
    for idx, h in enumerate(headers, start=1):
        fmt = CENTS if h in money_cols else PCT if h in pct_cols else None
        if fmt:
            for row in ws.iter_rows(min_row=2, min_col=idx, max_col=idx):
                row[0].number_format = fmt


def _style(ws, widths):
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    for cell in ws[1]:
        cell.font = Font(bold=True)
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
