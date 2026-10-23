"""Import a customer's billing history from a previous system.

WHAT THIS READS
---------------
The per-customer ledger export: one file, four kinds of row, in the shape

    Skybre: View CCTV Central  (SMF463)
    ID    Type               Number              Date        Total    Due     Payment date  Status
    34380 Recurring invoice  INV202606004199     01/09/2026  R435.85          04/08/2026    Paid
    34210 Recurring invoice  INV202601008200     03/09/2026  R433.55  R80.62                Partially paid
    32950 Payment            RCP2026-16-09100    01/09/2026  R435.85          01/09/2026
    1610  Credit note        CN202501001444      25/04/2025  R437.75                        Closed

The first line is optional but is checked when present: the reference in
brackets must match the customer this is being imported onto. Getting a
ledger onto the wrong account is the worst thing this code could do, so the
page says who and the file says who, and it refuses when they disagree.

THE DECISIONS BAKED IN HERE
---------------------------
**Every Total is VAT-INCLUSIVE**, for the whole history. R435.85 is stored
as R379.00 plus R56.85 VAT at 15%. Getting this backwards would overstate
two years of revenue by 15% and put the wrong output VAT on every historical
invoice.

The earlier R379.00 rows are inclusive too -- the price rose from R379.00 to
R435.85, and nothing about how VAT was presented ever changed. Worth stating
because the arithmetic invites the opposite conclusion: R435.85 is exactly
R379.00 x 1.15, which looks like a presentation change and is not one.

**Payments are not allocated to individual invoices.** The source's
"Payment date" column on an invoice row is derived, and on the account this
was written against it reads four weeks BEFORE the invoice existed (a credit
carried forward). Matching on it would create payments that predate their own
invoice. Payments credit the customer's balance without an invoice link
instead -- see below for how an invoice's OWN paid_amount is set.

**An invoice's paid_amount comes from the source's Due column, not its
Status word.** `Total` minus `Due` is exact and needs no interpretation, and
it is the only one of the two that can express a PARTIAL payment at all. The
Status word is read only as a fallback, for an older export with no Due
column -- and read as an EXACT match against "paid", never `"paid" in
status`: "paid" is a substring of "Unpaid" and of "Partially paid" too, so
that substring check used to mark every imported invoice fully Paid
regardless of what its own Status column actually said, silently taking it
off the list of invoices the reminder run (billing.recurring) chases for
money genuinely still owed. Found by comparing a live customer's statement
here against the same customer's statement in the source system.

**Payments are written directly, not through PaymentSerializer.** That
serializer emails the customer a receipt. Importing two years of history
through it would send a couple of dozen receipts for payments made long ago.
The balance arithmetic is replicated here with the same F() pattern, minus
the notification.

**Credit notes become invoices with a negative line.** The platform has no
credit-note model. A negative invoice keeps the document in the history,
keeps its original CN number, and moves the balance the right way.

**Original numbers are kept.** Invoice.save() honours a number that is
already set, and only records a high-water mark for numbers it issued
itself -- so imported documents keep INV202606004199 and do NOT consume
anything from this platform's own INV sequence.

IDEMPOTENT
----------
Every row is keyed on its source Number. Re-running the same file reports
each row as already imported and writes nothing. That is not a nicety: a
migration gets run two or three times before it is right, and a ledger that
double-imports is far worse than one that never imported at all.
"""
import csv
import datetime
import io
import re
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import F

from .models import Invoice, InvoiceItem, Payment

VAT_RATE = Decimal("15")
CENTS = Decimal("0.01")
# InvoiceItem.unit_price is stored to four places precisely so VAT can be
# derived from an unrounded ex-VAT figure. See that field's note.
PRECISION = Decimal("0.0001")

# The reference in the header line, e.g. "... (SMF463)".
_REF_IN_HEADER = re.compile(r"\(([^)]+)\)\s*$")

# Row types, matched loosely -- exports differ on capitalisation and on
# whether they say "Recurring invoice" or "Recurring Invoice".
INVOICE_TYPES = ("recurring invoice", "one-time invoice", "one time invoice", "invoice")
PAYMENT_TYPES = ("payment",)
CREDIT_TYPES = ("credit note", "creditnote", "credit")


def _money(raw):
    """"R435.85" / "-R0.19" / "435.85" / 435.85 -> Decimal, or None.

    Takes real numbers too, because a spreadsheet cell may hold one rather
    than a string. Routed through str() rather than Decimal(float) so a
    float's binary representation cannot leak a fraction of a cent in.
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float, Decimal)):
        try:
            return Decimal(str(raw)).quantize(CENTS, rounding=ROUND_HALF_UP)
        except Exception:                                     # noqa: BLE001
            return None
    cleaned = re.sub(r"[^\d.\-]", "", str(raw))
    if not cleaned or cleaned in {"-", "."}:
        return None
    try:
        return Decimal(cleaned).quantize(CENTS, rounding=ROUND_HALF_UP)
    except Exception:                                         # noqa: BLE001
        return None


def _date(raw):
    """Day-first, deliberately. This is a South African export: 04/08/2026
    is 4 August, not 8 April. ISO is accepted too because some exports emit
    it."""
    if not raw:
        return None
    # A spreadsheet may hand back a real date, in which case there is
    # nothing to interpret and no day-first/month-first ambiguity to get
    # wrong. This is the main reason .xlsx is read directly rather than
    # asking for a CSV: Excel writes dates out in the machine's locale, so
    # the same export becomes 04/08/2026 on one PC and 8/4/2026 on another.
    if isinstance(raw, datetime.datetime):
        return raw.date()
    if isinstance(raw, datetime.date):
        return raw
    text = str(raw).strip().split(" ")[0]
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y"):
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _split_vat(total):
    """(ex_vat, vat, ok) for a VAT-INCLUSIVE total.

    Kept to FOUR decimal places, which is the whole trick. R379.00 inclusive
    is R329.5652 ex; 15% of that is R49.43478, rounding to R49.43, and
    R329.57 + R49.43 is R379.00. Round the price to cents first and 15%
    becomes R49.44, and the invoice totals R379.01 instead.

    This is exactly how the system these ledgers come from does it -- its
    own invoice shows "Price 329.5652 / Tax 15 / With Tax 379.0000" -- which
    is why InvoiceItem.unit_price was widened to match. An imported invoice
    now reproduces the original document line for line.

    Returns (ex_vat, vat, adjustment). adjustment is a safety net and should
    always be zero; if an amount ever fails to reconcile it becomes a
    visible one-cent line rather than a silently wrong total.
    """
    ex = (total / (Decimal("1") + VAT_RATE / 100)).quantize(PRECISION, rounding=ROUND_HALF_UP)
    subtotal = ex.quantize(CENTS, rounding=ROUND_HALF_UP)
    vat = (ex * VAT_RATE / 100).quantize(CENTS, rounding=ROUND_HALF_UP)
    # Should be zero for every realistic amount -- it was verified against
    # the full ledger and a spread of edge cases before the column was
    # widened. Kept as a safety net rather than an assertion: an import that
    # silently writes a total the customer never paid is worse than one that
    # adds a visible cent and says so.
    return ex, vat, total - (subtotal + vat)


def _grid_from_xlsx(data):
    """First worksheet as a list of row-lists."""
    from openpyxl import load_workbook

    workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        sheet = workbook[workbook.sheetnames[0]]
        return [
            ["" if cell is None else cell for cell in row]
            for row in sheet.iter_rows(values_only=True)
        ]
    finally:
        workbook.close()


def _grid_from_text(data):
    """CSV/TSV as a list of row-lists. Delimiter auto-detected."""
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        return None
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    try:
        dialect = csv.Sniffer().sniff("\n".join(lines[:20]), delimiters="\t,;")
    except csv.Error:
        dialect = csv.excel_tab
    return [row for row in csv.reader(lines, dialect=dialect)]


def parse(data):
    """(reference_or_None, rows, fatal_errors) from raw file bytes.

    Accepts the .xlsx straight out of the old system as well as CSV/TSV.
    Reading the spreadsheet directly is not just convenience: it hands back
    real dates and numbers, where a CSV round-trip through Excel re-writes
    dates in whatever the exporting machine's locale happens to be.
    """
    if not data:
        return None, [], ["The file is empty."]

    if data[:2] == b"PK":                       # xlsx/zip magic
        try:
            grid = _grid_from_xlsx(data)
        except Exception as exc:                              # noqa: BLE001
            return None, [], [f"Couldn't read that spreadsheet: {exc}"]
    else:
        grid = _grid_from_text(data)
        if grid is None:
            return None, [], [
                "Couldn't read that file — save it as .xlsx, or as CSV in UTF-8."
            ]

    grid = [row for row in grid if any(str(cell).strip() for cell in row)]
    if not grid:
        return None, [], ["The file is empty."]

    reference = None
    header_index = None
    for index, row in enumerate(grid[:10]):
        cells = [str(cell).strip().lower() for cell in row]
        if "type" in cells and "number" in cells and "date" in cells:
            header_index = index
            break
        for cell in row:
            match = _REF_IN_HEADER.search(str(cell).strip())
            if match and reference is None:
                reference = match.group(1).strip()

    if header_index is None:
        return reference, [], [
            "Couldn't find the column headings. The file needs a row containing "
            "Type, Number and Date — they're usually on the second row, under "
            "the customer's name."
        ]

    headers = [str(cell).strip() for cell in grid[header_index]]
    rows = [dict(zip(headers, row)) for row in grid[header_index + 1:]]
    return reference, rows, []


def _cell(row, *names):
    for name in names:
        for key in row:
            if key and key.strip().lower() == name:
                value = row[key]
                return value.strip() if isinstance(value, str) else value
    return ""


def _paid_amount_for_invoice(raw, total, status):
    """How much of this invoice's total the source says is already settled.

    Prefers the source's own **Due** column -- it is the one number that can
    express a PARTIAL payment, and it needs no interpretation of what the
    Status column's word means. `paid = total - due`.

    Falls back to the Status word only when there is no Due column on this
    export (an older format). That match is deliberately EXACT, not
    `"paid" in status`: "paid" is a substring of "Unpaid" AND of "Partially
    paid", so the old substring check matched almost every status word this
    source actually uses, not just "Paid" -- every imported invoice came in
    marked fully paid regardless of what its own Status column said.

    Found by comparing a live customer's statement here against the same
    customer's statement in the source system: two invoices the source
    correctly showed as Unpaid and Partially paid came across marked Paid,
    so a customer who genuinely still owed money stopped being sent payment
    reminders for it -- the reminder run only chases UNPAID/OVERDUE
    invoices (see billing.recurring), and this had quietly marked the debt
    settled.
    """
    due = _money(_cell(raw, "due"))
    if due is not None:
        paid = total - due
    else:
        paid = total if status.strip() == "paid" else Decimal("0")
    return max(Decimal("0"), min(paid, total))


def plan(customer, data):
    """What an import WOULD do. Writes nothing.

    Returns {"reference", "rows": [...], "summary": {...}, "errors": [...]}.
    Each row carries an `action` of create_invoice / create_payment /
    create_credit / skip / error, and a message a human can act on.
    """
    reference, raw_rows, errors = parse(data)
    result = {"reference": reference, "rows": [], "summary": {}, "errors": list(errors)}
    if errors:
        return result

    # The file says who, the page says who. Disagreement is refused rather
    # than resolved -- a ledger on the wrong customer is not recoverable by
    # anything short of restoring a backup.
    if reference and customer.customer_id and reference.strip().lower() != customer.customer_id.strip().lower():
        result["errors"].append(
            f"This file is for reference '{reference}', but this customer's reference is "
            f"'{customer.customer_id}'. Nothing has been imported. Open the right customer, or "
            f"correct the reference on one of them first."
        )
        return result

    counts = {"invoices": 0, "payments": 0, "credits": 0, "skipped": 0, "errors": 0, "rounded": 0}
    invoiced = Decimal("0")
    paid = Decimal("0")
    credited = Decimal("0")

    for index, raw in enumerate(raw_rows, start=1):
        row_type = (_cell(raw, "type") or "").lower()
        number = _cell(raw, "number")
        when = _date(_cell(raw, "date"))
        total = _money(_cell(raw, "total"))
        status = (_cell(raw, "status") or "").lower()
        entry = {
            "line": index, "type": _cell(raw, "type"), "number": number,
            "date": when.isoformat() if when else None,
            "total": str(total) if total is not None else None,
            "action": "error", "message": "",
        }

        if not row_type or not number:
            entry["message"] = "No type or no number on this row — skipped."
            entry["action"] = "skip"
            counts["skipped"] += 1
            result["rows"].append(entry)
            continue
        if when is None:
            entry["message"] = f"Couldn't read the date '{_cell(raw, 'date')}'. Expected 04/08/2026."
            counts["errors"] += 1
            result["rows"].append(entry)
            continue
        if total is None or total == 0:
            entry["message"] = f"Couldn't read the total '{_cell(raw, 'total')}'."
            counts["errors"] += 1
            result["rows"].append(entry)
            continue

        if any(t in row_type for t in PAYMENT_TYPES):
            if Payment.objects.filter(customer=customer, note__contains=number).exists():
                entry.update(action="skip", message="Already imported.")
                counts["skipped"] += 1
            else:
                entry.update(action="create_payment", message=f"Payment of R{total}.")
                counts["payments"] += 1
                paid += total
        elif any(t in row_type for t in CREDIT_TYPES):
            if Invoice.objects.filter(number=number).exists():
                entry.update(action="skip", message="Already imported.")
                counts["skipped"] += 1
            else:
                ex, vat, adj = _split_vat(total)
                note = f" (+{adj}c rounding)" if adj else ""
                entry.update(
                    action="create_credit",
                    message=f"Credit note: -R{ex} + -R{vat} VAT{note}.",
                )
                counts["credits"] += 1
                credited += total
                if adj:
                    counts["rounded"] = counts.get("rounded", 0) + 1
        elif any(t in row_type for t in INVOICE_TYPES):
            if Invoice.objects.filter(number=number).exists():
                entry.update(action="skip", message="Already imported.")
                counts["skipped"] += 1
            else:
                ex, vat, adj = _split_vat(total)
                note = f" incl. {adj} rounding" if adj else ""
                paid_amount = _paid_amount_for_invoice(raw, total, status)
                if paid_amount >= total:
                    paid_word = "Paid"
                elif paid_amount > 0:
                    paid_word = f"Partially paid (R{paid_amount} of R{total})"
                else:
                    paid_word = "Unpaid"
                entry.update(
                    action="create_invoice",
                    message=f"R{ex} + R{vat} VAT = R{total}{note}. {paid_word}.",
                    paid_amount=str(paid_amount),
                )
                counts["invoices"] += 1
                invoiced += total
                if adj:
                    counts["rounded"] = counts.get("rounded", 0) + 1
        else:
            entry.update(action="skip", message=f"Unknown row type '{row_type}' — skipped.")
            counts["skipped"] += 1

        result["rows"].append(entry)

    # The net effect on what this customer owes. Shown before committing
    # because a PARTIAL ledger -- invoices without their payments, or the
    # other way round -- lands a balance that looks like a debt or a credit
    # nobody can explain six months later.
    net = invoiced - paid - credited
    counts["invoiced_total"] = str(invoiced)
    counts["paid_total"] = str(paid)
    counts["credited_total"] = str(credited)
    counts["balance_change"] = str(net)
    result["summary"] = counts
    return result


@transaction.atomic
def commit(customer, data, user=None):
    """Apply the plan. Everything or nothing.

    Wrapped in one transaction deliberately: a ledger half-written is worse
    than one not written, because the balance it leaves behind looks real.
    """
    planned = plan(customer, data)
    if planned["errors"]:
        return planned

    written = {"invoices": 0, "payments": 0, "credits": 0}
    for entry in planned["rows"]:
        if entry["action"] not in {"create_invoice", "create_payment", "create_credit"}:
            continue
        when = datetime.date.fromisoformat(entry["date"])
        total = Decimal(entry["total"])
        number = entry["number"]

        if entry["action"] == "create_payment":
            payment = Payment.objects.create(
                customer=customer,
                amount=total,
                method=Payment.Method.BANK_TRANSFER,
                # The source reference lives here because Payment has no
                # reference column -- and it is what makes a re-run
                # idempotent, so the wording must not drift.
                note=f"Imported from previous system: {number}",
                received_by=user,
            )
            # date is auto_now_add, so it cannot be set on create. An
            # UPDATE bypasses that -- without it every historical payment
            # would be dated the day of the import and the payment history
            # would be one flat stack.
            Payment.objects.filter(pk=payment.pk).update(
                date=datetime.datetime.combine(when, datetime.time(12, 0))
            )
            type(customer).objects.filter(pk=customer.pk).update(balance=F("balance") - total)
            written["payments"] += 1
            continue

        credit = entry["action"] == "create_credit"
        ex, vat, adj = _split_vat(total)
        if credit:
            ex, adj = -ex, -adj

        # A credit note is always treated as fully applied (see the module
        # docstring). An invoice's paid_amount was already worked out in
        # plan() -- from the source's own Due column where there is one,
        # or (only when there isn't) an EXACT match against the Status
        # word "paid", never a substring match. See _paid_amount_for_invoice.
        paid_amount = total if credit else Decimal(entry["paid_amount"])

        invoice = Invoice(
            customer=customer,
            number=number,                      # kept: save() honours a set number
            status=Invoice.Status.PAID if paid_amount >= total else Invoice.Status.UNPAID,
            date_due=when,
            # Set explicitly. save() stamps date_issued as TODAY when it is
            # blank and the status is an issued one -- which would drop a
            # 2024 invoice into this month's VAT return.
            date_issued=when,
        )
        invoice.save()
        # date_created is auto_now_add; same trick as the payment date.
        Invoice.objects.filter(pk=invoice.pk).update(date_created=when)

        InvoiceItem.objects.create(
            invoice=invoice,
            item_type=InvoiceItem.ItemType.CUSTOM,
            description=("Credit note — imported from previous system"
                         if credit else "Imported from previous system"),
            quantity=1,
            unit_price=ex,
            tax_rate_pct=VAT_RATE,
        )
        if adj:
            # One cent at 0% VAT so the document totals to what the customer
            # actually paid. recalc_totals derives tax per item, so this is
            # the only way to land an amount that 15% arithmetic cannot
            # reach -- and it says so on the invoice rather than hiding a
            # cent inside the price.
            InvoiceItem.objects.create(
                invoice=invoice,
                item_type=InvoiceItem.ItemType.CUSTOM,
                description="Rounding adjustment",
                quantity=1,
                unit_price=adj,
                tax_rate_pct=0,
            )
        invoice.refresh_from_db()
        invoice.recalc_totals()
        if paid_amount > 0:
            # Fully or PARTIALLY settled in the old system. Recorded as such
            # so it does not appear on an age analysis as completely unpaid
            # debt it no longer is -- and, just as importantly, so a still-
            # outstanding balance keeps being chased rather than silently
            # marked Paid. Clamped to the invoice's own (recalculated) total:
            # a rounding-adjustment line can move it a cent either side of
            # the source's own figure, and paid_amount must never exceed it.
            Invoice.objects.filter(pk=invoice.pk).update(
                paid_amount=min(paid_amount, invoice.total)
            )
        invoice.apply_balance_debit()
        written["credits" if credit else "invoices"] += 1

    # Imported money settles this client's open invoices once it lands
    # (12 Oct 2026): four clients sat "Overdue but in credit" because their
    # payments arrived by import AFTER the platform had raised the invoice,
    # and nothing swept them. sweep_customer only ever lets imported money
    # settle what the balance proves is covered (_settle_from_imported), so
    # it can never pay an invoice twice. Runs after this transaction commits.
    if any(written.values()):
        from .allocation import sweep_after_commit

        sweep_after_commit(customer.pk)

    planned["written"] = written
    return planned
