"""
Generic CSV bulk-import support for DRF ViewSets.

Mix `CSVImportMixin` into a `ModelViewSet`, set `import_model` and
`import_fields`, and you get two extra actions for free:

  POST /api/<resource>/import-preview/   (multipart file field "file")
      Parses + validates every row without writing to the database.
      Returns per-row validation results so the frontend can show a
      preview before anything is committed.

  POST /api/<resource>/import-commit/    (multipart file field "file")
      Re-parses + validates, then creates every row that passed
      validation. Rows that fail validation are skipped (not the whole
      import) and reported back individually.

`import_fields` maps CSV column names to parsing rules:
    {
        "some_column": {
            "required": bool,             # default False
            "type": "str" | "decimal" | "int" | "bool" | "date",  # default "str"
            "default": <value used when the cell is empty and not required>,
            "choices": [...],              # optional, list of allowed values
            "aliases": [...],              # optional, alternate header names
                                            # accepted for this field (e.g. a
                                            # third-party export's own column
                                            # names) — checked if the primary
                                            # name isn't present/populated.
        },
        ...
    }

The parser auto-detects the delimiter (comma, tab, or semicolon), so a
tab-delimited export (e.g. Splynx's native CSV export) works without any
manual reformatting.

Decimal fields are cleaned of currency symbols/letters before parsing
(e.g. "R975.00" or "-R0.19" both parse fine), so money columns from
billing-system exports don't need to be pre-stripped either.

Date fields accept the handful of formats these exports actually emit --
ISO (2024-03-17), ISO with a time part (2024-03-17 08:31:00, the shape
Splynx exports use), and day-first D/M/Y with either separator. Day-first
is deliberate, not US month-first: this is a South African system, and
"03/04/2024" from a local export means 3 April.

Subclasses can override `extra_row_validation(cleaned, raw_row)` to do
things a plain type/choices check can't — e.g. resolving a foreign key by
name and erroring per-row if it doesn't exist. `cleaned` is mutated in
place; return a list of extra error strings (or an empty list).
`raw_row` is the original untouched dict for that row, keyed by whatever
headers the uploaded file actually had — useful for reading extra source
columns that don't have a slot in `import_fields` (e.g. stashing a source
system's internal ID into a notes field for traceability).

Subclasses can override `import_row_to_kwargs(cleaned)` if the cleaned
dict needs any final reshaping before being passed to
`import_model.objects.create(**kwargs)` (the default just returns it as-is).
"""

import csv
import datetime
import io
import re
from decimal import Decimal, InvalidOperation

from django.db import transaction
from rest_framework.decorators import action
from rest_framework.response import Response

# Everything that is decoration around a number: currency codes and
# symbols, ordinary and non-breaking spaces (Excel exports are full of the
# latter), and apostrophes, which some locales use for thousands.
_CURRENCY_NOISE_RE = re.compile(r"[R$£€\s  ']|ZAR", re.IGNORECASE)
_DIGIT_GROUP_RE = re.compile(r"^\d{1,3}(\d{3})*$")


class AmbiguousNumber(ValueError):
    """A number whose separator could be a decimal point or a thousands
    separator, with no way to tell which. Raised rather than guessed --
    see parse_import_decimal."""


def parse_import_decimal(raw):
    """A money cell from a spreadsheet, as a Decimal.

    This used to be `Decimal(re.sub(r"[^0-9.\\-]", "", raw))`, which
    stripped the comma -- and in the locale this module's own date parser
    invokes ("this is a South African system"), the comma IS the decimal
    separator and the space is the thousands separator. So every
    comma-decimal cell imported a hundred times too large, silently, with
    no error and a green preview:

        R1 234,56  ->  123456.00
        12,50      ->  1250.00
        1.234,56   ->  1.23

    The 1,592 real customers on this platform were imported through this
    function with Splynx's "Account balance" column mapped to
    Customer.balance, so this was not hypothetical.

    Both conventions are accepted, decided by position rather than by a
    locale setting, because a single export can be either:

      * Both separators present -- the RIGHTMOST is the decimal separator
        and the other is thousands. `1.234,56` and `1,234.56` are both
        1234.56. Unambiguous.
      * One separator, 1 or 2 trailing digits -- decimal separator.
        `12,50` is 12.50, `12.50` is 12.50.
      * One separator appearing more than once -- thousands.
        `1,234,567` is 1234567.
      * One separator, exactly 3 trailing digits, appearing once --
        genuinely ambiguous. `1,500` is 1500 in en-US and 1.5 in en-ZA,
        and nothing in the cell distinguishes them. Raises
        AmbiguousNumber so the importer reports the row instead of
        picking one and being wrong about money.

    Parentheses are the accounting notation for a negative, which the old
    regex also discarded: `(150.00)` came through as +150.00, turning
    R150 of credit into R150 of debt.
    """
    text = (raw or "").strip()
    if not text:
        return Decimal("0")

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1].strip()

    text = _CURRENCY_NOISE_RE.sub("", text)
    if text.startswith("-"):
        negative = not negative
        text = text[1:]
    elif text.startswith("+"):
        text = text[1:]

    if not text:
        raise ValueError("no digits")

    dot, comma = text.rfind("."), text.rfind(",")
    if dot >= 0 and comma >= 0:
        # Rightmost wins as the decimal separator.
        decimal_sep, thousands_sep = ((".", ",") if dot > comma else (",", "."))
    elif dot >= 0 or comma >= 0:
        sep = "." if dot >= 0 else ","
        trailing = len(text) - text.rfind(sep) - 1
        occurrences = text.count(sep)
        if occurrences > 1:
            decimal_sep, thousands_sep = None, sep
        elif trailing in (1, 2):
            decimal_sep, thousands_sep = sep, None
        elif trailing == 3:
            raise AmbiguousNumber(
                f"'{raw}' could be {text.replace(sep, '')} (thousands separator) or "
                f"{text.replace(sep, '.')} (decimal separator) -- both are in use and the cell "
                f"does not say which. Re-export with two decimal places (e.g. "
                f"{text.replace(sep, '')},00) or without the separator."
            )
        else:
            raise ValueError(f"unexpected digits after '{sep}'")
    else:
        decimal_sep, thousands_sep = None, None

    if thousands_sep:
        groups = text.split(thousands_sep)
        if decimal_sep:
            groups[-1:] = groups[-1].split(decimal_sep)
            fraction = groups.pop()
        else:
            fraction = None
        # A thousands separator has to actually separate thousands.
        if len(groups) > 1 and (
            not 1 <= len(groups[0]) <= 3 or any(len(g) != 3 for g in groups[1:])
        ):
            raise ValueError(f"'{thousands_sep}' is not grouping thousands")
        text = "".join(groups) + (f".{fraction}" if fraction is not None else "")
    elif decimal_sep:
        text = text.replace(decimal_sep, ".")

    # Everything this function rejects is raised as a ValueError, so
    # callers have one thing to catch. Decimal's own failure is an
    # ArithmeticError, which is easy to miss.
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"'{raw}' is not a number") from exc
    return -value if negative else value

# Formats these exports actually produce, tried in order. Day-first
# (%d/%m/%Y) rather than US month-first is deliberate -- this is a South
# African system, so "03/04/2024" in a local export means 3 April. There
# is no month-first fallback on purpose: silently accepting both would
# make 03/04 vs 04/03 a coin flip, and a wrong-but-plausible date is far
# worse here than a rejected row the importer tells you about.
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y/%m/%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
)


def _parse_import_date(raw):
    """Returns a datetime.date, or None if nothing matched."""
    value = (raw or "").strip()
    if not value:
        return None
    # Tolerate a trailing timezone/fractional part on ISO timestamps.
    for candidate in (value, value.split(".")[0], value.split("+")[0].strip()):
        for fmt in _DATE_FORMATS:
            try:
                return datetime.datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue
    return None


class CSVImportMixin:
    import_model = None
    import_fields: dict = {}

    # -- parsing -----------------------------------------------------

    def _parse_csv(self, file_obj):
        raw = file_obj.read()
        try:
            decoded = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            decoded = raw.decode("latin-1")
        sample = decoded[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel  # plain comma, the common case
        reader = csv.DictReader(io.StringIO(decoded), dialect=dialect)
        return list(reader)

    @staticmethod
    def _raw_value(row, field, rules):
        """Look up a cell by the field's primary name, falling back to any
        configured aliases (for accepting a source system's own headers)."""
        value = row.get(field)
        if value:
            return value
        for alias in rules.get("aliases", ()):
            value = row.get(alias)
            if value:
                return value
        return ""

    def _validate_row(self, row):
        errors = []
        cleaned = {}
        for field, rules in self.import_fields.items():
            raw = (self._raw_value(row, field, rules) or "").strip()
            if not raw:
                if rules.get("required"):
                    errors.append(f"'{field}' is required")
                else:
                    cleaned[field] = rules.get("default")
                continue

            field_type = rules.get("type", "str")
            if field_type == "decimal":
                try:
                    cleaned[field] = parse_import_decimal(raw)
                except AmbiguousNumber as exc:
                    # Reported verbatim: the message says which two values
                    # it could be and how to re-export unambiguously.
                    errors.append(f"'{field}': {exc}")
                    continue
                except (InvalidOperation, ValueError):
                    errors.append(f"'{field}' must be a number (got '{raw}')")
                    continue
            elif field_type == "int":
                try:
                    cleaned[field] = int(raw)
                except ValueError:
                    errors.append(f"'{field}' must be a whole number (got '{raw}')")
                    continue
            elif field_type == "bool":
                # Explicit both ways, and an error for anything else. This
                # used to be `raw.lower() in ("1","true","yes","y")`, so
                # any unrecognised text became False with no error --
                # importing a tariff sheet whose is_active column read
                # "Active"/"Inactive" (the words the UI uses) silently
                # deactivated every plan.
                lowered = raw.lower()
                if lowered in ("1", "true", "yes", "y", "t", "on", "active", "enabled"):
                    cleaned[field] = True
                elif lowered in ("0", "false", "no", "n", "f", "off", "inactive", "disabled"):
                    cleaned[field] = False
                else:
                    errors.append(
                        f"'{field}' must be yes or no (got '{raw}')"
                    )
                    continue
            elif field_type == "date":
                parsed = _parse_import_date(raw)
                if parsed is None:
                    errors.append(
                        f"'{field}' must be a date like 2024-03-17 or 17/03/2024 (got '{raw}')"
                    )
                    continue
                cleaned[field] = parsed
            else:
                cleaned[field] = raw

            choices = rules.get("choices")
            if choices and cleaned.get(field) not in choices:
                errors.append(f"'{field}' must be one of {', '.join(str(c) for c in choices)} (got '{raw}')")

        extra_errors = self.extra_row_validation(cleaned, row)
        if extra_errors:
            errors.extend(extra_errors)
        return cleaned, errors

    def extra_row_validation(self, cleaned, raw_row):
        """Hook for subclasses, e.g. foreign-key lookups. Mutate `cleaned`
        in place if needed; return a list of extra error strings."""
        return []

    def import_row_to_kwargs(self, cleaned):
        return cleaned

    def _process_rows(self, file_obj):
        rows = self._parse_csv(file_obj)
        results = []
        for i, row in enumerate(rows, start=2):  # row 1 is the header
            cleaned, errors = self._validate_row(row)
            results.append({"row": i, "data": self._jsonable(cleaned), "errors": errors})
        return results

    @staticmethod
    def _jsonable(cleaned):
        """Convert values (Decimal, model instances, etc.) into something
        the DRF Response renderer can serialize."""
        out = {}
        for k, v in cleaned.items():
            if isinstance(v, Decimal):
                out[k] = str(v)
            elif hasattr(v, "pk"):
                out[k] = str(v)
            else:
                out[k] = v
        return out

    # -- endpoints -----------------------------------------------------

    @action(detail=False, methods=["post"], url_path="import-preview")
    def import_preview(self, request):
        file_obj = request.FILES.get("file")
        if not file_obj:
            return Response({"detail": "No file uploaded (expected form field 'file')."}, status=400)
        results = self._process_rows(file_obj)
        valid = [r for r in results if not r["errors"]]
        invalid = [r for r in results if r["errors"]]
        return Response(
            {
                "total_rows": len(results),
                "valid_count": len(valid),
                "invalid_count": len(invalid),
                "rows": results,
            }
        )

    @action(detail=False, methods=["post"], url_path="import-commit")
    def import_commit(self, request):
        file_obj = request.FILES.get("file")
        if not file_obj:
            return Response({"detail": "No file uploaded (expected form field 'file')."}, status=400)

        rows = self._parse_csv(file_obj)
        created = 0
        skipped = []
        # One transaction for the whole file. Each row still gets its own
        # savepoint, so a bad row is reported and stepped over exactly as
        # before -- what changes is that the response and the database now
        # agree about what happened.
        #
        # Without this, every create autocommitted on its own, and the one
        # job this endpoint exists for is a ~1,500-row migration whose
        # per-row cost includes two unindexed scans. Past gunicorn's
        # 30-second default the worker was killed mid-file: several hundred
        # customers permanently committed, no response, and nobody able to
        # say which ones. Retrying then duplicated every row whose
        # reference cell was blank, because those had been given generated
        # references the duplicate check cannot match.
        with transaction.atomic():
            for i, row in enumerate(rows, start=2):
                cleaned, errors = self._validate_row(row)
                if errors:
                    skipped.append({"row": i, "errors": errors})
                    continue
                try:
                    with transaction.atomic():
                        kwargs = self.import_row_to_kwargs(cleaned)
                        self.import_model.objects.create(**kwargs)
                    created += 1
                except Exception as exc:  # noqa: BLE001 - surface any save-time error per row
                    skipped.append({"row": i, "errors": [str(exc)]})

        return Response({"created": created, "skipped_count": len(skipped), "skipped": skipped})
