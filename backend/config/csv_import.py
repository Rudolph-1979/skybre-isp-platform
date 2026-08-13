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
            "type": "str" | "decimal" | "int" | "bool",   # default "str"
            "default": <value used when the cell is empty and not required>,
            "choices": [...],              # optional, list of allowed values
        },
        ...
    }

Subclasses can override `extra_row_validation(cleaned, raw_row)` to do
things a plain type/choices check can't — e.g. resolving a foreign key by
name and erroring per-row if it doesn't exist. `cleaned` is mutated in
place; return a list of extra error strings (or an empty list).

Subclasses can override `import_row_to_kwargs(cleaned)` if the cleaned
dict needs any final reshaping before being passed to
`import_model.objects.create(**kwargs)` (the default just returns it as-is).
"""

import csv
import io
from decimal import Decimal, InvalidOperation

from rest_framework.decorators import action
from rest_framework.response import Response


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
        reader = csv.DictReader(io.StringIO(decoded))
        return list(reader)

    def _validate_row(self, row):
        errors = []
        cleaned = {}
        for field, rules in self.import_fields.items():
            raw = (row.get(field) or "").strip()
            if not raw:
                if rules.get("required"):
                    errors.append(f"'{field}' is required")
                else:
                    cleaned[field] = rules.get("default")
                continue

            field_type = rules.get("type", "str")
            if field_type == "decimal":
                try:
                    cleaned[field] = Decimal(raw)
                except InvalidOperation:
                    errors.append(f"'{field}' must be a number (got '{raw}')")
                    continue
            elif field_type == "int":
                try:
                    cleaned[field] = int(raw)
                except ValueError:
                    errors.append(f"'{field}' must be a whole number (got '{raw}')")
                    continue
            elif field_type == "bool":
                cleaned[field] = raw.lower() in ("1", "true", "yes", "y")
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
        for i, row in enumerate(rows, start=2):
            cleaned, errors = self._validate_row(row)
            if errors:
                skipped.append({"row": i, "errors": errors})
                continue
            try:
                kwargs = self.import_row_to_kwargs(cleaned)
                self.import_model.objects.create(**kwargs)
                created += 1
            except Exception as exc:  # noqa: BLE001 - surface any save-time error per row
                skipped.append({"row": i, "errors": [str(exc)]})

        return Response({"created": created, "skipped_count": len(skipped), "skipped": skipped})
