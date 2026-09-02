"""CSV import for bank statement exports -- the practical way to use this
feature today, since FNB's direct API access isn't a self-service thing
(see fnb_client.py's docstring).

Two things a real FNB export does that this used to choke on:

1. **A preamble.** The file doesn't begin with the column headers. It opens
   with account information -- account number, statement period, opening
   balance -- and the header row is somewhere below that. csv.DictReader takes
   the FIRST line as the header, so every row came back keyed on "Statement"
   or similar with no Date/Description/Amount to find, and the whole file
   reported "Date is required" on every single row. The header row is now
   found by looking for one that actually names a date and an amount.

2. **Separate money-in / money-out columns.** This used to require one signed
   Amount column and told you to combine the two yourself in a spreadsheet
   first, which rather defeats the point of an import. Debit/credit pairs are
   now combined into a single signed amount here.

The amount convention after parsing is unchanged: positive = money in,
negative = money out.
"""
import csv
import hashlib
import io
from datetime import datetime
from decimal import Decimal, InvalidOperation

COLUMN_ALIASES = {
    "date": ["date", "transaction date", "value date", "posting date", "trans date"],
    "description": [
        "description", "transaction description", "details", "narrative",
        "reference", "payment reference", "transaction",
    ],
    "amount": ["amount", "transaction amount", "value", "signed amount"],
    # Debit/credit pairs, for exports that split the two directions. "debit"
    # and "money out" are money LEAVING the account, so they end up negative
    # whichever sign the bank happened to write them with.
    "money_in": ["money in", "credit", "credit amount", "deposits", "amount in"],
    "money_out": ["money out", "debit", "debit amount", "withdrawals", "amount out"],
}

# Tried in order -- covers ISO and the DD/MM/YYYY-style dates common in SA
# bank exports.
_DATE_FORMATS = [
    "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y",
    "%Y/%m/%d", "%d/%m/%y", "%d %b %y",
]

# How far into the file to look for the header row. Generous on purpose: FNB's
# preamble is a handful of lines, but a statement carrying an address block
# can push the header further down than you would guess.
_MAX_HEADER_SCAN_LINES = 30


class StatementFormatError(ValueError):
    """No usable header row anywhere in the file. Carries a message written
    for the person who did the export, naming what was actually found."""


def _normalise(value):
    return (value or "").strip().lower()


def _header_score(cells):
    """How much this row looks like a header row.

    A header needs a date column and some way of expressing an amount --
    either a single signed one or a debit/credit pair. Description is
    deliberately NOT required: an export may call it something not yet in the
    alias list, and a file with dates and amounts is still worth importing.
    """
    seen = {_normalise(c) for c in cells if c is not None}
    has_date = any(alias in seen for alias in COLUMN_ALIASES["date"])
    has_amount = any(alias in seen for alias in COLUMN_ALIASES["amount"])
    has_pair = any(alias in seen for alias in COLUMN_ALIASES["money_in"]) or any(
        alias in seen for alias in COLUMN_ALIASES["money_out"]
    )
    if not has_date or not (has_amount or has_pair):
        return 0
    # More recognised columns means a better header, so a genuine header row
    # wins over a stray preamble line that happens to contain the word "date".
    return sum(
        1
        for aliases in COLUMN_ALIASES.values()
        for alias in aliases
        if alias in seen
    )


def _decode(file_obj):
    raw = file_obj.read()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _sniff(decoded):
    try:
        return csv.Sniffer().sniff(decoded[:4096], delimiters=",;\t|")
    except csv.Error:
        return csv.excel


def _parse_csv_rows(file_obj):
    """(rows as dicts keyed on the detected header, line number of that header).

    Everything above the header -- the account-info preamble -- is discarded,
    as are entirely blank rows below it, since exports commonly end with a
    blank line or a totals row separated by one.
    """
    decoded = _decode(file_obj)
    dialect = _sniff(decoded)
    all_rows = list(csv.reader(io.StringIO(decoded), dialect=dialect))
    if not all_rows:
        raise StatementFormatError("That file is empty.")

    best_index, best_score = None, 0
    for index, cells in enumerate(all_rows[:_MAX_HEADER_SCAN_LINES]):
        score = _header_score(cells)
        if score > best_score:
            best_index, best_score = index, score

    if best_index is None:
        # Show what was actually in the file rather than failing generically --
        # the fix is almost always "that's the wrong export".
        sample = next((c for c in all_rows if any((x or "").strip() for x in c)), [])
        raise StatementFormatError(
            "Couldn't find a header row with a date and an amount in the first "
            f"{_MAX_HEADER_SCAN_LINES} lines. The first row with anything in it reads: "
            f"{', '.join((x or '').strip() for x in sample[:8]) or '(blank)'}. "
            "The export needs a Date column, and either an Amount column or a "
            "Money In / Money Out pair."
        )

    header = [_normalise(c) for c in all_rows[best_index]]
    rows = []
    for cells in all_rows[best_index + 1:]:
        if not any((c or "").strip() for c in cells):
            continue
        rows.append({header[i]: cells[i] for i in range(min(len(header), len(cells)))})
    return rows, best_index + 1


def _get(row_lower, key):
    for alias in COLUMN_ALIASES[key]:
        value = row_lower.get(alias)
        if value:
            return value.strip()
    return ""


def _to_decimal(raw):
    """Parse a bank-formatted number, or raise InvalidOperation.

    Handles a currency symbol, spaces (including the non-breaking ones Excel
    exports are full of), the accounting-style "(123.45)" negative, and a
    trailing minus.

    The awkward part is the comma, because South African exports use it BOTH
    ways: "12,500.00" is twelve and a half thousand, and "12 500,00" is the
    same number with the comma as the decimal point. Stripping commas blindly
    turns the second into 1250000 -- a thousand times too much, on real money,
    silently. So the separators are worked out rather than assumed:

      * both '.' and ',' present -> whichever comes LAST is the decimal point
      * only ',' present -> a decimal point if there is exactly one of them and
        it is followed by exactly two digits, otherwise a thousands separator
      * only '.' present -> the usual decimal point
    """
    cleaned = raw
    for space in ("\u00a0", "\u202f", "\u2009", " ", "\t"):
        cleaned = cleaned.replace(space, "")
    cleaned = cleaned.replace("R", "").replace("r", "").strip()

    negative = False
    if cleaned.startswith("(") and cleaned.endswith(")"):
        negative = True
        cleaned = cleaned[1:-1]
    if cleaned.endswith("-"):
        negative = not negative
        cleaned = cleaned[:-1]
    if cleaned.startswith("-"):
        negative = not negative
        cleaned = cleaned[1:]

    has_dot, has_comma = "." in cleaned, "," in cleaned
    if has_dot and has_comma:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif has_comma:
        head, _, tail = cleaned.rpartition(",")
        cleaned = f"{head}.{tail}" if (len(tail) == 2 and tail.isdigit() and "," not in head) \
            else cleaned.replace(",", "")

    value = Decimal(cleaned)
    return -value if negative else value

def _resolve_amount(row_lower):
    """(amount, error). Prefers a single signed column, falls back to the pair.

    For a debit/credit pair the sign is FORCED rather than trusted: banks are
    inconsistent about whether a "Money Out" figure is already written
    negative, and taking it at face value would turn half of someone's
    payments into deposits.
    """
    signed_raw = _get(row_lower, "amount")
    if signed_raw:
        try:
            return _to_decimal(signed_raw), None
        except InvalidOperation:
            return None, f"'{signed_raw}' isn't a valid amount"

    in_raw = _get(row_lower, "money_in")
    out_raw = _get(row_lower, "money_out")
    if not in_raw and not out_raw:
        return None, "Amount is required"

    total = Decimal("0")
    for raw, sign in ((in_raw, 1), (out_raw, -1)):
        if not raw:
            continue
        try:
            total += sign * abs(_to_decimal(raw))
        except InvalidOperation:
            return None, f"'{raw}' isn't a valid amount"
    return total, None


def _parse_date(raw):
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def parse_statement_csv(file_obj):
    """Returns a list of per-row dicts: {"row", "date" (ISO string or
    None), "description", "amount" (str or None), "external_id", "errors"}
    -- doesn't touch the database. `external_id` is a deterministic hash
    of (date, description, amount, row position), so re-uploading the
    exact same file twice produces the exact same external_ids and the
    import-commit step's dedupe naturally skips them all as duplicates.

    Raises StatementFormatError when no header row can be identified at all.
    That is a whole-file problem, not a per-row one, and reporting it as N
    rows of "Date is required" tells the user nothing about the real cause.
    """
    rows, header_line = _parse_csv_rows(file_obj)
    results = []
    for offset, row in enumerate(rows):
        # Numbered by real line in the file, so an error points at the row you
        # would actually find in a spreadsheet -- which is not the same as its
        # position once a preamble has been skipped.
        line_number = header_line + offset + 1
        row_lower = {_normalise(k): v for k, v in row.items()}
        errors = []

        date_raw = _get(row_lower, "date")
        date_value = _parse_date(date_raw) if date_raw else None
        if not date_value:
            errors.append(f"Couldn't parse a date from '{date_raw}'" if date_raw else "Date is required")

        description = _get(row_lower, "description")

        amount_value, amount_error = _resolve_amount(row_lower)
        if amount_error:
            errors.append(amount_error)

        external_id = None
        if not errors:
            key = f"{date_value.isoformat()}|{description}|{amount_value}|{line_number}"
            external_id = "csv:" + hashlib.sha256(key.encode()).hexdigest()[:32]

        results.append(
            {
                "row": line_number,
                "date": date_value.isoformat() if date_value else None,
                "description": description,
                "amount": str(amount_value) if amount_value is not None else None,
                "external_id": external_id,
                "errors": errors,
            }
        )
    return results
