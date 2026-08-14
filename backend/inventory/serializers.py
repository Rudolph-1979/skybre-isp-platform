from django.conf import settings
from django.db import transaction
from rest_framework import serializers

from config.media_security import sign_media_path
from .models import (
    Supplier,
    Product,
    SerializedUnit,
    StockReceipt,
    StockReceiptLine,
    StockIssue,
    StockIssueLine,
    StockMovement,
)


class SignedAttachmentMixin:
    """Turns a plain FileField's raw MEDIA URL into a signed, short-lived
    download link (see config/media_security.py) instead of the
    unauthenticated static path DRF's default FileField would render."""

    def get_attachment(self, obj):
        if not obj.attachment:
            return None
        relative_path = obj.attachment.name
        signed = sign_media_path(relative_path)
        url = f"{settings.MEDIA_URL}{relative_path}?sig={signed}"
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url


class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = "__all__"
        read_only_fields = ["id", "created_at"]


class ProductSerializer(serializers.ModelSerializer):
    quantity_on_hand = serializers.IntegerField(read_only=True)
    is_low_stock = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id", "name", "sku", "category", "tracking_type", "unit",
            "low_stock_threshold", "description", "is_active",
            "quantity_on_hand", "is_low_stock", "created_at",
        ]
        read_only_fields = ["id", "quantity_on_hand", "created_at"]

    def get_is_low_stock(self, obj):
        if obj.low_stock_threshold is None:
            return False
        return obj.quantity_on_hand <= obj.low_stock_threshold


class SerializedUnitSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = SerializedUnit
        fields = [
            "id", "product", "product_name", "serial_number", "mac_address",
            "status", "notes", "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class StockReceiptLineSerializer(serializers.ModelSerializer):
    """Read representation — used when displaying an existing receipt."""

    product_name = serializers.CharField(source="product.name", read_only=True)
    serial_count = serializers.SerializerMethodField()

    class Meta:
        model = StockReceiptLine
        fields = [
            "id", "receipt", "product", "product_name", "quantity",
            "serial_numbers", "unit_cost", "serial_count", "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def get_serial_count(self, obj):
        return obj.units.count()


class StockReceiptLineInputSerializer(serializers.ModelSerializer):
    """Write representation accepted when creating a receipt — no
    `receipt` field since that's supplied by the parent create()."""

    class Meta:
        model = StockReceiptLine
        fields = ["product", "quantity", "serial_numbers", "unit_cost"]


class StockReceiptSerializer(SignedAttachmentMixin, serializers.ModelSerializer):
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    received_by_name = serializers.CharField(source="received_by.username", read_only=True, default=None)
    lines = StockReceiptLineSerializer(many=True, read_only=True)
    attachment = serializers.SerializerMethodField()

    class Meta:
        model = StockReceipt
        fields = [
            "id", "supplier", "supplier_name", "invoice_number", "invoice_date",
            "attachment", "received_by", "received_by_name", "notes", "lines", "created_at",
        ]
        read_only_fields = ["id", "received_by", "created_at"]


class StockReceiptCreateSerializer(SignedAttachmentMixin, serializers.ModelSerializer):
    """Accepts nested line items, creates SerializedUnit rows for
    serialized products and StockMovement rows for quantity-tracked ones,
    all inside one transaction so a bad line rolls back the whole receipt.

    `attachment` stays a normal writable field so the file upload on
    create still works — only the *response* representation is swapped
    for a signed download link (via SignedAttachmentMixin), so the
    receipt you just created doesn't hand back an unauthenticated raw
    MEDIA URL that the protected media view would then reject anyway."""

    lines = StockReceiptLineInputSerializer(many=True)

    class Meta:
        model = StockReceipt
        fields = ["id", "supplier", "invoice_number", "invoice_date", "attachment", "notes", "lines"]
        read_only_fields = ["id"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["attachment"] = self.get_attachment(instance)
        return data

    def validate_lines(self, lines):
        if not lines:
            raise serializers.ValidationError("At least one line item is required.")
        return lines

    def create(self, validated_data):
        lines_data = validated_data.pop("lines")
        user = self.context["request"].user

        with transaction.atomic():
            receipt = StockReceipt.objects.create(received_by=user, **validated_data)

            for line_data in lines_data:
                product = line_data["product"]

                if product.tracking_type == Product.TrackingType.SERIALIZED:
                    raw_text = line_data.get("serial_numbers", "") or ""
                    entries = [s.strip() for s in raw_text.splitlines() if s.strip()]
                    if not entries:
                        raise serializers.ValidationError(
                            f"'{product.name}' is a serialized product — enter at least one serial number "
                            "(one per line, optionally 'SERIAL,MAC')."
                        )

                    parsed = []
                    seen = set()
                    for entry in entries:
                        if "," in entry:
                            serial_number, mac_address = (p.strip() for p in entry.split(",", 1))
                        else:
                            serial_number, mac_address = entry, ""
                        if serial_number in seen:
                            raise serializers.ValidationError(
                                f"Serial number '{serial_number}' was entered twice in the same line."
                            )
                        seen.add(serial_number)
                        parsed.append((serial_number, mac_address))

                    existing = set(
                        SerializedUnit.objects.filter(
                            serial_number__in=[s for s, _ in parsed]
                        ).values_list("serial_number", flat=True)
                    )
                    if existing:
                        raise serializers.ValidationError(
                            f"These serial numbers already exist in the system: {', '.join(sorted(existing))}"
                        )

                    line = StockReceiptLine.objects.create(
                        receipt=receipt,
                        product=product,
                        quantity=len(parsed),
                        serial_numbers=raw_text,
                        unit_cost=line_data.get("unit_cost"),
                    )
                    for serial_number, mac_address in parsed:
                        SerializedUnit.objects.create(
                            product=product,
                            serial_number=serial_number,
                            mac_address=mac_address,
                            received_via_line=line,
                        )
                else:
                    quantity = line_data.get("quantity") or 0
                    if quantity <= 0:
                        raise serializers.ValidationError(
                            f"Enter a quantity greater than 0 for '{product.name}'."
                        )
                    line = StockReceiptLine.objects.create(
                        receipt=receipt,
                        product=product,
                        quantity=quantity,
                        unit_cost=line_data.get("unit_cost"),
                    )
                    StockMovement.objects.create(
                        product=product,
                        movement_type=StockMovement.MovementType.RECEIPT,
                        quantity=quantity,
                        receipt_line=line,
                        created_by=user,
                        note=f"Receipt {receipt.invoice_number}",
                    )

        return receipt


class StockIssueLineSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    serial_number = serializers.CharField(source="serial_unit.serial_number", read_only=True, default=None)
    mac_address = serializers.CharField(source="serial_unit.mac_address", read_only=True, default=None)

    class Meta:
        model = StockIssueLine
        fields = [
            "id", "issue", "product", "product_name", "quantity",
            "serial_unit", "serial_number", "mac_address",
        ]
        read_only_fields = ["id"]


class StockIssueLineInputSerializer(serializers.ModelSerializer):
    serial_number = serializers.CharField(
        required=False,
        allow_blank=True,
        write_only=True,
        help_text="For serialized products — the exact serial number of the unit being issued.",
    )

    class Meta:
        model = StockIssueLine
        fields = ["product", "quantity", "serial_number"]


class StockIssueSerializer(serializers.ModelSerializer):
    issued_to_name = serializers.CharField(source="issued_to.username", read_only=True, default=None)
    job_title = serializers.CharField(source="job.title", read_only=True, default=None)
    customer_name = serializers.CharField(source="job.customer.full_name", read_only=True, default=None)
    lines = StockIssueLineSerializer(many=True, read_only=True)

    class Meta:
        model = StockIssue
        fields = [
            "id", "job", "job_title", "customer_name", "issued_to", "issued_to_name",
            "issued_at", "notes", "lines",
        ]
        read_only_fields = ["id", "issued_at"]


class StockIssueCreateSerializer(serializers.ModelSerializer):
    lines = StockIssueLineInputSerializer(many=True)

    class Meta:
        model = StockIssue
        fields = ["id", "job", "issued_to", "notes", "lines"]
        read_only_fields = ["id"]

    def validate_lines(self, lines):
        if not lines:
            raise serializers.ValidationError("At least one line item is required.")
        return lines

    def create(self, validated_data):
        lines_data = validated_data.pop("lines")
        user = self.context["request"].user

        with transaction.atomic():
            issue = StockIssue.objects.create(created_by=user, **validated_data)

            for line_data in lines_data:
                product = line_data["product"]

                if product.tracking_type == Product.TrackingType.SERIALIZED:
                    serial_number = (line_data.get("serial_number") or "").strip()
                    if not serial_number:
                        raise serializers.ValidationError(
                            f"'{product.name}' is a serialized product — specify which serial number is being issued."
                        )
                    try:
                        unit = SerializedUnit.objects.select_for_update().get(
                            product=product, serial_number=serial_number
                        )
                    except SerializedUnit.DoesNotExist:
                        raise serializers.ValidationError(
                            f"No unit with serial number '{serial_number}' found for '{product.name}'."
                        )
                    if unit.status != SerializedUnit.Status.IN_STOCK:
                        raise serializers.ValidationError(
                            f"Serial '{serial_number}' is not currently in stock "
                            f"(status: {unit.get_status_display()})."
                        )
                    unit.status = SerializedUnit.Status.ISSUED
                    unit.save()
                    StockIssueLine.objects.create(issue=issue, product=product, quantity=1, serial_unit=unit)
                else:
                    quantity = line_data.get("quantity") or 0
                    if quantity <= 0:
                        raise serializers.ValidationError(
                            f"Enter a quantity greater than 0 for '{product.name}'."
                        )
                    available = product.quantity_on_hand
                    if quantity > available:
                        raise serializers.ValidationError(
                            f"Cannot issue {quantity} of '{product.name}' — only {available} in stock."
                        )
                    line = StockIssueLine.objects.create(issue=issue, product=product, quantity=quantity)
                    note = f"Issue #{issue.pk}"
                    if issue.job:
                        note += f" — job: {issue.job.title}"
                    StockMovement.objects.create(
                        product=product,
                        movement_type=StockMovement.MovementType.ISSUE,
                        quantity=-quantity,
                        issue_line=line,
                        created_by=user,
                        note=note,
                    )

        return issue
