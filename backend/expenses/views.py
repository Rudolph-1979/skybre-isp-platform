from decimal import Decimal

# Coalesce moved to vat_sources.py along with the query that used it.
from django.http import HttpResponse
from django.utils.dateparse import parse_date
from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsStaffMember, section_permission
# The filters below used to be written out inline here. They now live in
# vat_sources so the spreadsheet export reads exactly the same definition
# of "which documents count" that these figures are summed from -- see
# that module's docstring for why two copies is the failure worth
# preventing.
from .vat_sources import input_vat_expenses, input_vat_stock_receipts, output_vat_items
from .models import Expense
from .serializers import ExpenseSerializer

# Same factory every other section-gated app uses (see bankfeeds/views.py's
# HasFinanceAccess, radiusauth/views.py, etc.) -- no hand-written permission
# class needed, this just narrows IsStaffMember further to staff who've
# actually been granted the "accountant" section (Configs -> Permissions).
HasAccountantAccess = section_permission("accountant")


class ExpenseViewSet(viewsets.ModelViewSet):
    """CRUD for business expenses/purchases -- the Input VAT side of the
    Accountant -> VAT Returns report. See Expense's docstring for why
    this exists (there was no expense/purchase tracking anywhere in this
    codebase before it)."""

    serializer_class = ExpenseSerializer
    # bank_transaction is the reverse OneToOne from BankTransaction.
    # created_expense -- select_related it too so from_bank_feed's
    # hasattr() check doesn't cost an extra query per row.
    queryset = Expense.objects.select_related("supplier", "created_by", "bank_transaction", "vehicle").all()
    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasAccountantAccess]
    # No parser_classes override -- DRF's own defaults (JSONParser,
    # FormParser, MultiPartParser) already cover both a plain JSON
    # edit and a multipart request carrying a new attachment file, same
    # as every other file-attachment viewset in this codebase (e.g.
    # inventory.StockReceiptViewSet, which also doesn't override this).
    filterset_fields = ["category", "supplier", "vehicle"]
    search_fields = ["description", "supplier_name", "invoice_number"]
    ordering_fields = ["date", "amount_excl_vat", "created_at"]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=False, methods=["get"], url_path="vehicle-choices")
    def vehicle_choices(self, request):
        """GET /api/expenses/vehicle-choices/ -- the Vehicle dropdown on the
        expense form (13 Oct 2026). Served here, under Accountant access,
        because whoever captures invoices may not have the Vehicles section;
        it is a registration and a name, nothing more."""
        from fleet.models import Vehicle

        rows = Vehicle.objects.order_by("-is_active", "registration_number").values(
            "id", "registration_number", "make", "model", "is_active"
        )
        return Response([
            {
                "id": r["id"],
                "label": f"{r['registration_number']} — {r['make']} {r['model']}".strip()
                + ("" if r["is_active"] else " (inactive)"),
            }
            for r in rows
        ])


def _sum_output_vat(period_start, period_end):
    """Output VAT for the period, on the invoice/accrual basis: every
    InvoiceItem on an invoice actually issued (date_issued) in the
    period, excluding quotes/pro-formas/drafts (never real tax invoices)
    and cancelled invoices (VAT never became due). Bucketed by whether
    the line's tax_rate_pct is >0 (standard-rated) or exactly 0
    (zero-rated) -- there's no separate zero-rated/exempt flag anywhere
    in this codebase, so a 0% line is trusted at face value as
    intentionally zero-rated, not treated as a data-entry gap."""
    # Which lines count, and the long argument for dating them on
    # date_issued, both moved to vat_sources.output_vat_items -- the
    # reasoning went with the code rather than being left here beside a
    # call that no longer does it. `.only()` stays: what must not drift
    # between the summary and the export is which ROWS are counted, not
    # which columns each caller bothers to load.
    items = output_vat_items(period_start, period_end).only(
        "quantity", "unit_price", "tax_rate_pct", "invoice_id"
    )

    standard_rated_supplies = Decimal("0")
    zero_rated_supplies = Decimal("0")
    output_vat = Decimal("0")
    invoice_ids = set()
    for item in items:
        line_excl_vat = item.quantity * item.unit_price
        invoice_ids.add(item.invoice_id)
        if item.tax_rate_pct and item.tax_rate_pct > 0:
            standard_rated_supplies += line_excl_vat
            output_vat += line_excl_vat * item.tax_rate_pct / Decimal("100")
        else:
            zero_rated_supplies += line_excl_vat

    return {
        "standard_rated_supplies": standard_rated_supplies.quantize(Decimal("0.01")),
        "zero_rated_supplies": zero_rated_supplies.quantize(Decimal("0.01")),
        "output_vat": output_vat.quantize(Decimal("0.01")),
        "invoice_count": len(invoice_ids),
    }


def _sum_input_vat(period_start, period_end):
    """Input VAT for the period, from BOTH sources that record a purchase:

      * Expense    -- non-stock costs: rent, bandwidth, fuel, software.
      * StockReceipt -- equipment and consumables bought for stock.

    Stock receipts were originally not counted here at all, which silently
    under-claimed Input VAT on every router, ONT and drum of cable Skybre
    buys -- usually one of the largest input lines an ISP has. Equipment
    invoices are now expected to be captured in Inventory ONLY; see
    _find_duplicate_claims for the guard that catches one entered in both
    places, which would otherwise claim its VAT twice.

    Both sides are dated by the SUPPLIER's own invoice date (Expense.date,
    StockReceipt.invoice_date), not when the record was created -- that is
    what determines the VAT period on the invoice/accrual basis Skybre
    files under.

    ROUNDING. Each source is rounded at the level the UI displays, then
    summed:

      * Expenses: per-expense `vat_amount`, already rounded, because the
        Expenses tab shows each row rounded. Rounding once at the end made
        the return disagree with the sum of the rows on screen by a cent
        or two (three R33.37 lines showed as 5.01 each = 15.03, but the
        return said 15.02).
      * Stock: per-RECEIPT `vat_total`, because a receipt corresponds to
        one supplier invoice and that is the document whoever files this
        reconciles against. StockReceipt.totals rounds once per receipt
        for the same reason.

    The Output VAT side deliberately does neither -- it mirrors
    Invoice.recalc_totals, summing unrounded across line items so
    output_vat agrees with each invoice's own stored tax_total.
    """
    from inventory.models import StockReceipt

    expense_excl = Decimal("0")
    expense_vat = Decimal("0")
    expense_count = 0
    expenses = input_vat_expenses(period_start, period_end).only(
        "amount_excl_vat", "vat_rate_pct"
    )
    for expense in expenses:
        expense_count += 1
        expense_excl += expense.amount_excl_vat
        expense_vat += expense.vat_amount

    stock_excl = Decimal("0")
    stock_vat = Decimal("0")
    stock_count = 0
    receipts_missing_vat = 0
    # prefetch_related populates each line's cached `receipt`, which the
    # line-level VAT properties read -- without it this is one extra query
    # per line.
    # prefetch_related("lines") now travels with the filter itself, since
    # every VAT figure on a receipt is derived from its lines and a caller
    # that forgets it just gets the same answer slowly.
    receipts = input_vat_stock_receipts(period_start, period_end)
    for receipt in receipts:
        stock_count += 1
        excl, vat, _incl = receipt.totals
        stock_excl += excl
        stock_vat += vat
        if receipt.has_unrecorded_vat:
            # Lines captured before VAT tracking existed. They contribute
            # their cost but no VAT, so the claim may be understated --
            # surfaced rather than silently absorbed.
            receipts_missing_vat += 1

    cents = Decimal("0.01")
    return {
        # Combined totals keep their original names, so anything already
        # reading input.input_vat / input.purchases_excl_vat still works.
        "purchases_excl_vat": (expense_excl + stock_excl).quantize(cents),
        "input_vat": (expense_vat + stock_vat).quantize(cents),
        "expense_count": expense_count,
        # Breakdown by source, so the two can be reconciled separately.
        "expenses": {
            "purchases_excl_vat": expense_excl.quantize(cents),
            "input_vat": expense_vat.quantize(cents),
            "count": expense_count,
        },
        "stock": {
            "purchases_excl_vat": stock_excl.quantize(cents),
            "input_vat": stock_vat.quantize(cents),
            "count": stock_count,
            "receipts_missing_vat": receipts_missing_vat,
        },
    }


def _normalise_invoice_number(value):
    """Invoice numbers get typed inconsistently -- 'inv 1234', 'INV-1234'.
    Compared with spaces, hyphens and case removed so the duplicate guard
    isn't defeated by punctuation."""
    return "".join(ch for ch in (value or "").upper() if ch.isalnum())


def _find_duplicate_claims(period_start, period_end):
    """Supplier invoices captured BOTH as a stock receipt and as an
    expense -- which would claim the same Input VAT twice.

    Equipment purchases are meant to live in Inventory only. This catches
    the habit of also logging them under Expenses.

    Deliberately WARNS rather than silently deducting one side: two
    genuinely different invoices can share a number across suppliers, and
    a report that quietly altered the figures would be worse than one that
    asks a human to look. Nothing here changes any total.

    Matching is on supplier + normalised invoice number, and expenses are
    NOT restricted to the period -- an invoice number is unique per
    supplier, so a match in an adjacent period is still a double claim,
    just one split across two returns.
    """
    from inventory.models import StockReceipt

    receipts = StockReceipt.objects.filter(
        invoice_date__gte=period_start, invoice_date__lte=period_end
    ).select_related("supplier").prefetch_related("lines")

    by_key = {}
    supplier_names = {}
    for receipt in receipts:
        number = _normalise_invoice_number(receipt.invoice_number)
        if not number:
            continue
        by_key[(receipt.supplier_id, number)] = receipt
        supplier_names[receipt.supplier.name.strip().upper()] = receipt.supplier_id

    if not by_key:
        return []

    warnings = []
    candidates = Expense.objects.exclude(invoice_number="").select_related("supplier")
    for expense in candidates:
        number = _normalise_invoice_number(expense.invoice_number)
        if not number:
            continue
        # An expense may point at a Supplier record, or only name one in
        # free text (most expenses have no Supplier row at all) -- try both.
        supplier_id = expense.supplier_id
        if supplier_id is None:
            supplier_id = supplier_names.get((expense.supplier_name or "").strip().upper())
        if supplier_id is None:
            continue
        receipt = by_key.get((supplier_id, number))
        if receipt is None:
            continue
        warnings.append({
            "invoice_number": receipt.invoice_number,
            "supplier": receipt.supplier.name,
            "receipt_id": receipt.pk,
            "receipt_date": receipt.invoice_date,
            "receipt_vat": receipt.vat_total,
            "expense_id": expense.pk,
            "expense_date": expense.date,
            "expense_description": expense.description,
            "expense_vat": expense.vat_amount,
        })
    warnings.sort(key=lambda w: (w["supplier"], w["invoice_number"]))
    return warnings


def _sum_credit_notes(period_start, period_end):
    """Informational only, and only for a credit with no invoice of its
    own. Approving a CreditRequest now creates a real, zero-rated credit
    note invoice (see CreditRequest.create_credit_note_invoice) whose line
    already lands in `_sum_output_vat`'s zero-rated total for the period
    it was issued in -- so listing it again here would put the same rand
    amount in the return twice, once netted against supplies and once as
    this separate note. credit_note__isnull=True keeps this to exactly
    the credits that still have nothing else on the return to show for
    them: ones decided before that feature existed and not yet backfilled.
    Shown so whoever files the return can factor those in by hand if
    material, rather than the report silently overstating Output VAT."""
    from billing.models import CreditRequest

    credits = CreditRequest.objects.filter(
        status=CreditRequest.Status.APPROVED,
        decided_at__date__gte=period_start,
        decided_at__date__lte=period_end,
        credit_note__isnull=True,
    )
    total = Decimal("0")
    count = 0
    for credit in credits.only("amount"):
        count += 1
        total += credit.amount
    return {"total_amount": total.quantize(Decimal("0.01")), "count": count}


def build_vat_return(period_start, period_end):
    from billing.models import BillingDefaults
    from notifications.email_settings import get_email_config

    output = _sum_output_vat(period_start, period_end)
    input_ = _sum_input_vat(period_start, period_end)
    credit_notes = _sum_credit_notes(period_start, period_end)
    duplicate_claims = _find_duplicate_claims(period_start, period_end)
    net_vat = (output["output_vat"] - input_["input_vat"]).quantize(Decimal("0.01"))
    if net_vat > 0:
        direction = "payable"
    elif net_vat < 0:
        direction = "refundable"
    else:
        direction = "even"

    billing_defaults = BillingDefaults.load()
    company_name = get_email_config()["company_name"]

    return {
        "period_start": period_start,
        "period_end": period_end,
        "basis": "invoice",
        "vat_category": "B",
        "company_name": company_name,
        "vat_number": billing_defaults.vat_number,
        "output": output,
        "input": input_,
        "credit_notes": credit_notes,
        # Invoices captured twice (once in Inventory, once in Expenses).
        # Empty list in the normal case. These do NOT alter any figure
        # above -- see _find_duplicate_claims for why they only warn.
        "duplicate_claims": duplicate_claims,
        "net_vat": net_vat,
        "net_vat_direction": direction,
    }


def _parse_period(request):
    period_start = parse_date(request.query_params.get("period_start") or "")
    period_end = parse_date(request.query_params.get("period_end") or "")
    if not period_start or not period_end:
        return None, None, "period_start and period_end (YYYY-MM-DD) are both required."
    if period_start > period_end:
        return None, None, "period_start must be on or before period_end."
    return period_start, period_end, None


class VatReturnView(APIView):
    """GET /api/vat-return/?period_start=YYYY-MM-DD&period_end=YYYY-MM-DD
    -- the Output VAT (from real invoices) / Input VAT (from Expense
    records) / Net VAT breakdown for one two-monthly SARS VAT201 period.
    The frontend computes the actual Category-B period boundaries
    (endings Feb/Apr/Jun/Aug/Oct/Dec) and passes them in explicitly --
    this view just sums whatever range it's given, so it has no
    hard-coded assumption about which category or cadence is in use
    beyond the "basis"/"vat_category" labels echoed back for display."""

    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasAccountantAccess]

    def get(self, request):
        period_start, period_end, error = _parse_period(request)
        if error:
            return Response({"detail": error}, status=400)
        return Response(build_vat_return(period_start, period_end))


class VatReturnExportView(APIView):
    """GET /api/vat-return/export/?period_start=&period_end=&format=xlsx

    The same figures as VatReturnView, as a spreadsheet.
    `filetype=xlsx` (the default) gives a workbook with the summary and
    the documents behind it on separate sheets; `filetype=csv` gives one
    flat file, and `section=summary|output|input|warnings` picks which.

    NOT called `format`. That name is reserved by DRF for content
    negotiation: `?format=csv` makes it hunt for a renderer named "csv"
    and 404 before this view is entered. It reads more naturally and it
    does not work.

    Why both: the xlsx is what gets read and emailed to an accountant,
    the csv is what gets imported into something else. Stacking several
    tables into one CSV with blank lines between them would look right in
    Excel and import into nothing, which defeats the only reason to pick
    CSV over the workbook.
    """

    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasAccountantAccess]

    def get(self, request):
        from .vat_export import SECTIONS, build_csv, build_xlsx

        period_start, period_end, error = _parse_period(request)
        if error:
            return Response({"detail": error}, status=400)

        fmt = (request.query_params.get("filetype") or "xlsx").lower()
        if fmt not in ("xlsx", "csv"):
            return Response({"detail": "filetype must be xlsx or csv."}, status=400)
        section = (request.query_params.get("section") or "summary").lower()
        if section not in SECTIONS:
            return Response(
                {"detail": f"section must be one of {', '.join(SECTIONS)}."}, status=400
            )

        vat_return = build_vat_return(period_start, period_end)
        stem = f"VAT-Return-{period_start}-to-{period_end}"

        if fmt == "csv":
            payload = build_csv(vat_return, section)
            content_type = "text/csv; charset=utf-8"
            filename = f"{stem}-{section}.csv"
        else:
            payload = build_xlsx(vat_return)
            content_type = (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            filename = f"{stem}.xlsx"

        response = HttpResponse(payload, content_type=content_type)
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class VatReturnPdfView(APIView):
    """Same calculation as VatReturnView, rendered as a downloadable PDF
    for actually filing/keeping on record -- see notifications/pdf.py's
    render_vat_report_pdf. There's no existing precedent in this codebase
    for a browser-download PDF response (render_invoice_pdf/
    render_statement_pdf are only ever used as email attachments) -- this
    is the first."""

    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasAccountantAccess]

    def get(self, request):
        from notifications.pdf import render_vat_report_pdf

        period_start, period_end, error = _parse_period(request)
        if error:
            return Response({"detail": error}, status=400)
        vat_return = build_vat_return(period_start, period_end)
        pdf_bytes = render_vat_report_pdf(vat_return)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="VAT-Return-{period_start}-to-{period_end}.pdf"'
        return response
