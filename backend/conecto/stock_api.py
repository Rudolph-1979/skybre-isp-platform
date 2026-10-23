"""The stock scanner in the Conecto admin app (Bernard, 12 Oct 2026).

    POST /api/conecto/stock/lookup/   {staff_code, barcode}  -> {item} | 404
    POST /api/conecto/stock/search/   {staff_code, query}    -> {results: [item, ...]}
    POST /api/conecto/stock/receive/  {conecto_receipt_id, staff_code, ..., lines}
                                      -> 201 {receipt_id} | 200 {receipt_id, duplicate}

Same bearer key as every other Conecto call (ConectoWebhook). The staff code
must belong to an active staff member with the Stock / Inventory section;
anything else is a 403 -- including an unknown code, so the app can never
read "no such person" as "no such barcode" on the lookup's 404.

BOOKING GOES THROUGH THE SAME CODE AS THE STOCK RECEIPTS SCREEN.
receive() builds the payload that screen sends and hands it to
inventory.serializers.StockReceiptCreateSerializer, so serial numbers,
SerializedUnit rows, StockMovement rows and every duplicate check are the
ones the office already relies on. A second copy of that logic here would
drift, and stock booked two different ways is how on-hand counts stop
being believed.

What the scanner cannot know, and how it is handled:

* COST AND VAT. Nothing is priced on a phone, so lines carry no unit cost.
  The receipt then shows as "VAT not recorded" and is left out of the VAT
  return until somebody types the supplier invoice's costs in -- the same
  rule as any receipt captured without costs.
* SUPPLIER. Matched to an existing supplier by name (ignoring case).
  Anything else lands on one "Conecto scanner (unspecified)" supplier with
  the typed name in the notes. Creating suppliers from free text typed on a
  phone would fill the list with near-duplicates.
* LOCATION. Stock movements have no site yet; everything is general stock,
  exactly as the receipts screen books it.

Idempotent on conecto_receipt_id (unique on StockReceipt): a resend returns
the receipt already booked, checked before anything else so a retry can
never trip over the serials it booked itself.
"""
import logging
import types

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from inventory.models import (
    Product, ProductBarcode, SerializedUnit, StockMovement, StockReceipt, Supplier,
)

from .views import ConectoWebhook, parse_timestamp

logger = logging.getLogger(__name__)

MAX_SEARCH_RESULTS = 50
MAX_LINES = 200
MAX_QUANTITY = 100000
PLACEHOLDER_SUPPLIER = "Conecto scanner (unspecified)"
#: Characters a serial can't contain: the receipt stores serials one per
#: line as "SERIAL" or "SERIAL,MAC" (inventory.identifiers.parse_serial_lines).
SERIAL_FORBIDDEN = (",", "\n", "\r", "\t")


class StockApiError(Exception):
    """Becomes {"detail": ...} (plus any extra keys) with this status."""

    def __init__(self, message, status_code=400, **extra):
        super().__init__(message)
        self.status_code = status_code
        self.extra = extra


def _error(exc):
    body = {"detail": str(exc)}
    body.update(exc.extra)
    return Response(body, status=exc.status_code)


def _body(request):
    return request.data if isinstance(request.data, dict) else {}


def _text(value, limit):
    if value is None:
        return ""
    return str(value).strip()[:limit]


def grn(receipt):
    return f"GRN-{receipt.pk:05d}"


# --- who is asking ------------------------------------------------------------


def stock_user(staff_code):
    from accounts.permissions import user_can_access_section

    code = str(staff_code or "").strip()
    if not code:
        raise StockApiError("staff_code is required.", status.HTTP_403_FORBIDDEN)
    user = get_user_model().objects.filter(staff_code__iexact=code).first()
    if user is None:
        raise StockApiError(f"No staff member with code {code}.", status.HTTP_403_FORBIDDEN)
    if not user.is_active or not user.is_staff_member or not user_can_access_section(user, "inventory"):
        raise StockApiError(
            "This staff member has no access to Stock / Inventory.", status.HTTP_403_FORBIDDEN
        )
    return user


# --- items ----------------------------------------------------------------------


def _clean_barcode(value, *, required, where=""):
    prefix = f"{where}: " if where else ""
    if value is None or str(value).strip() == "":
        if required:
            raise StockApiError(f"{prefix}barcode is required.")
        return ""
    code = str(value).strip()
    if len(code) > 64:
        raise StockApiError(f"{prefix}barcode is longer than 64 characters.")
    if any(ch in code for ch in ("\n", "\r", "\t")):
        raise StockApiError(f"{prefix}barcode contains a line break or tab.")
    return code


def on_hand_for(products):
    """{product_id: on hand} in two queries, however many products --
    the same figures as Product.quantity_on_hand, without a query each."""
    quantity_ids = [p.pk for p in products if p.tracking_type != Product.TrackingType.SERIALIZED]
    serial_ids = [p.pk for p in products if p.tracking_type == Product.TrackingType.SERIALIZED]
    figures = {}
    if quantity_ids:
        figures.update(
            StockMovement.objects.filter(product_id__in=quantity_ids)
            .order_by().values("product_id").annotate(total=Sum("quantity"))
            .values_list("product_id", "total")
        )
    if serial_ids:
        figures.update(
            SerializedUnit.objects.filter(product_id__in=serial_ids, status=SerializedUnit.Status.IN_STOCK)
            .order_by().values("product_id").annotate(total=Count("id"))
            .values_list("product_id", "total")
        )
    return figures


def item_row(product, on_hand, barcode=None):
    if barcode is None:
        codes = [b.barcode for b in product.barcodes.all()]
        barcode = codes[0] if codes else None
    serialized = product.tracking_type == Product.TrackingType.SERIALIZED
    return {
        "item_id": product.pk,
        "sku": product.sku or "",
        "name": product.name,
        "barcode": barcode,
        "tracks_serials": serialized,
        "unit": "each" if serialized else (product.unit or "each"),
        "on_hand": int(on_hand.get(product.pk) or 0),
        "category": product.get_category_display(),
    }


class StockLookupView(ConectoWebhook):
    """A barcode, scanned. Matched on the item's linked barcodes, then --
    for labels that print the SKU as the barcode -- on a SKU that only one
    active item carries."""

    def post(self, request):
        data = _body(request)
        try:
            stock_user(data.get("staff_code"))
            barcode = _clean_barcode(data.get("barcode"), required=True)
        except StockApiError as exc:
            return _error(exc)

        link = (
            ProductBarcode.objects.select_related("product")
            .filter(barcode=barcode, product__is_active=True).first()
        )
        product = link.product if link is not None else None
        if product is None:
            by_sku = list(Product.objects.filter(is_active=True, sku__iexact=barcode)[:2])
            if len(by_sku) == 1:
                product = by_sku[0]
        if product is None:
            return Response(
                {"detail": f"No item has barcode {barcode}."}, status=status.HTTP_404_NOT_FOUND
            )
        return Response({"item": item_row(product, on_hand_for([product]), barcode=barcode)})


class StockSearchView(ConectoWebhook):
    """Typed search: every word must match the name, SKU or a barcode.
    Active items only, up to 50, by name. Under two characters returns
    nothing rather than the whole catalogue."""

    def post(self, request):
        data = _body(request)
        try:
            stock_user(data.get("staff_code"))
        except StockApiError as exc:
            return _error(exc)

        query = _text(data.get("query"), 100)
        if len(query) < 2:
            return Response({"results": []})
        qs = Product.objects.filter(is_active=True)
        for word in query.split():
            qs = qs.filter(
                Q(name__icontains=word) | Q(sku__icontains=word) | Q(barcodes__barcode__icontains=word)
            )
        products = list(qs.distinct().prefetch_related("barcodes").order_by("name", "pk")[:MAX_SEARCH_RESULTS])
        figures = on_hand_for(products)
        return Response({"results": [item_row(p, figures) for p in products]})


# --- receiving --------------------------------------------------------------------


def _product(raw, where):
    if isinstance(raw, bool) or raw is None or not str(raw).strip().isdigit():
        raise StockApiError(f"{where}: item_id must be an item number.")
    product = Product.objects.filter(pk=int(str(raw).strip())).first()
    if product is None:
        raise StockApiError(f"{where}: there is no item {raw}.")
    if not product.is_active:
        raise StockApiError(f"{where}: {product.name} is switched off in Vexar, so stock can't be received for it.")
    return product


def _quantity(raw, where):
    if isinstance(raw, bool):
        raise StockApiError(f"{where}: quantity must be a whole number.")
    if isinstance(raw, float) and raw.is_integer():
        raw = int(raw)
    if isinstance(raw, str) and raw.strip().isdigit():
        raw = int(raw.strip())
    if not isinstance(raw, int):
        raise StockApiError(f"{where}: quantity must be a whole number.")
    if raw <= 0:
        raise StockApiError(f"{where}: quantity must be more than 0.")
    if raw > MAX_QUANTITY:
        raise StockApiError(f"{where}: quantity {raw} is more than {MAX_QUANTITY}.")
    return raw


def _serials(raw, where):
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise StockApiError(f"{where}: serials must be a list.")
    out = []
    for value in raw:
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            raise StockApiError(f"{where}: every serial must be text.")
        serial = str(value).strip()
        if not serial:
            raise StockApiError(f"{where}: a serial number is blank.")
        if len(serial) > 100:
            raise StockApiError(f"{where}: serial {serial[:20]}... is longer than 100 characters.")
        if any(ch in serial for ch in SERIAL_FORBIDDEN):
            raise StockApiError(f"{where}: serial {serial!r} contains a comma, tab or line break.")
        out.append(serial)
    return out


def plan_receipt(data):
    """Check every line before anything is written. Returns (lines,
    barcodes) where barcodes maps each barcode to link -> its item id."""
    lines = data.get("lines")
    if not isinstance(lines, list) or not lines:
        raise StockApiError("lines must be a list with at least one line.")
    if len(lines) > MAX_LINES:
        raise StockApiError(f"A receipt can have at most {MAX_LINES} lines.")

    planned, barcodes, seen = [], {}, set()
    for n, raw in enumerate(lines, 1):
        where = f"Line {n}"
        if not isinstance(raw, dict):
            raise StockApiError(f"{where} is not an object.")
        product = _product(raw.get("item_id"), where)
        quantity = _quantity(raw.get("quantity"), where)
        serials = _serials(raw.get("serials"), where)

        if product.tracking_type == Product.TrackingType.SERIALIZED:
            if not serials:
                raise StockApiError(f"{where}: {product.name} is serial-tracked, so it needs a serial per unit.")
            if len(serials) != quantity:
                raise StockApiError(
                    f"{where}: quantity is {quantity} but {len(serials)} serial(s) were sent for {product.name}."
                )
            for serial in serials:
                if serial in seen:
                    raise StockApiError(f"Serial {serial} appears twice on this receipt.")
                seen.add(serial)
        elif serials:
            raise StockApiError(f"{where}: {product.name} isn't serial-tracked; send it without serials.")

        barcode = _clean_barcode(raw.get("barcode"), required=False, where=where)
        if barcode:
            if barcodes.get(barcode, product.pk) != product.pk:
                raise StockApiError(f"Barcode {barcode} is on two different items in this receipt.")
            barcodes[barcode] = product.pk
        planned.append({"product": product, "quantity": quantity, "serials": serials})

    for code, product_id in barcodes.items():
        other = (
            ProductBarcode.objects.select_related("product")
            .filter(barcode=code).exclude(product_id=product_id).first()
        )
        if other is not None:
            raise StockApiError(
                f"Barcode {code} already belongs to {other.product.name} (item {other.product_id}), "
                "so it can't be linked to another item. Check the item picked for that line."
            )

    clash = _serial_clash(seen)
    if clash:
        raise StockApiError(
            "These serial numbers are already in Vexar: " + ", ".join(clash),
            status.HTTP_409_CONFLICT, duplicate_serials=clash,
        )
    return planned, barcodes


def _serial_clash(serials):
    if not serials:
        return []
    return sorted(SerializedUnit.objects.filter(serial_number__in=serials).values_list("serial_number", flat=True))


def _supplier(raw):
    """(supplier, the typed name if it matched nothing)."""
    name = _text(raw, 255)
    if name:
        match = Supplier.objects.filter(name__iexact=name).first()
        if match is not None:
            return match, ""
    placeholder, _ = Supplier.objects.get_or_create(
        name=PLACEHOLDER_SUPPLIER,
        defaults={
            "notes": (
                "Stock received in the Conecto app with no supplier, or one that isn't in this list. "
                "The supplier as typed is in each receipt's notes -- move the receipt to the right supplier."
            ),
        },
    )
    return placeholder, name


def _flatten(detail):
    if isinstance(detail, dict):
        for key, value in detail.items():
            text = _flatten(value)
            return text if key in ("non_field_errors", "detail") else f"{key}: {text}"
        return "Rejected."
    if isinstance(detail, (list, tuple)):
        return _flatten(detail[0]) if detail else "Rejected."
    return str(detail)


class StockReceiveView(ConectoWebhook):
    def post(self, request):
        data = _body(request)
        receipt_id = _text(data.get("conecto_receipt_id"), 101)
        if not receipt_id:
            return Response({"detail": "conecto_receipt_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        if len(receipt_id) > 100:
            return Response({"detail": "conecto_receipt_id is longer than 100 characters."},
                            status=status.HTTP_400_BAD_REQUEST)

        existing = StockReceipt.objects.filter(conecto_receipt_id=receipt_id).first()
        if existing is not None:
            return self._duplicate(existing)

        try:
            user = stock_user(data.get("staff_code"))
            planned, barcodes = plan_receipt(data)
            return self._book(receipt_id, user, data, planned, barcodes)
        except StockApiError as exc:
            return _error(exc)

    @staticmethod
    def _duplicate(receipt):
        return Response({"receipt_id": grn(receipt), "duplicate": True}, status=status.HTTP_200_OK)

    def _book(self, receipt_id, user, data, planned, barcodes):
        from inventory.serializers import StockReceiptCreateSerializer

        supplier, typed_supplier = _supplier(data.get("supplier"))
        received_at = parse_timestamp(data.get("received_at")) or timezone.now()
        reference = _text(data.get("reference"), 100)
        who = _text(data.get("received_by_name"), 150) or user.get_full_name() or user.username
        notes = [f"Received in the Conecto app by {who} ({user.staff_code}). Conecto receipt {receipt_id}."]
        if typed_supplier:
            notes.append(f"Supplier as typed: {typed_supplier}.")
        if _text(data.get("notes"), 2000):
            notes.append(_text(data.get("notes"), 2000))

        payload = {
            "supplier": supplier.pk,
            "invoice_number": reference or f"Conecto {receipt_id}"[:100],
            "invoice_date": timezone.localtime(received_at).date().isoformat(),
            "prices_include_vat": False,
            "notes": "\n".join(notes),
            "lines": [
                {
                    "product": line["product"].pk,
                    "quantity": line["quantity"],
                    "serial_numbers": "\n".join(line["serials"]),
                }
                for line in planned
            ],
        }
        serializer = StockReceiptCreateSerializer(
            data=payload, context={"request": types.SimpleNamespace(user=user)}
        )
        try:
            with transaction.atomic():
                serializer.is_valid(raise_exception=True)
                receipt = serializer.save(conecto_receipt_id=receipt_id)
                for code, product_id in barcodes.items():
                    link, created = ProductBarcode.objects.get_or_create(
                        barcode=code,
                        defaults={
                            "product_id": product_id,
                            "source": ProductBarcode.Source.CONECTO,
                            "added_by": user,
                        },
                    )
                    if not created and link.product_id != product_id:
                        raise StockApiError(
                            f"Barcode {code} was just linked to another item. Look the item up again."
                        )
        except DRFValidationError as exc:
            raise StockApiError(_flatten(exc.detail))
        except IntegrityError:
            # Two sends of one receipt at the same moment, or a serial booked
            # by somebody else in between the check and the save.
            existing = StockReceipt.objects.filter(conecto_receipt_id=receipt_id).first()
            if existing is not None:
                return self._duplicate(existing)
            clash = _serial_clash({s for line in planned for s in line["serials"]})
            if clash:
                raise StockApiError(
                    "These serial numbers are already in Vexar: " + ", ".join(clash),
                    status.HTTP_409_CONFLICT, duplicate_serials=clash,
                )
            raise
        logger.info("Conecto stock receipt %s booked as %s by %s", receipt_id, grn(receipt), user.staff_code)
        return Response({"receipt_id": grn(receipt)}, status=status.HTTP_201_CREATED)
