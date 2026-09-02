from django.conf import settings
from django.db import transaction
from rest_framework import serializers

from .identifiers import InvalidMac, normalise_mac, normalise_serial, parse_serial_lines

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
    # The rate the receipt form should pre-fill for this supplier: their
    # default rate while VAT-registered, 0 otherwise. Computed on the
    # model, never stored -- see Supplier.effective_vat_rate_pct for why
    # the authoritative rate lives on the receipt line instead.
    effective_vat_rate_pct = serializers.DecimalField(
        max_digits=5, decimal_places=2, read_only=True
    )

    class Meta:
        model = Supplier
        fields = "__all__"
        read_only_fields = ["id", "created_at"]


class ProductSerializer(serializers.ModelSerializer):
    """The catalogue entry. `initial_units` lets you put physical units on the
    shelf at the moment you create the entry -- opening stock, or a unit that
    arrived without a supplier invoice.

    Those units get no supplier and no cost, because there is no receipt to
    hang either on, so this is NOT the way to book in a delivery: that has to
    go through a StockReceipt or the VAT return and the stock valuation are
    both wrong. Hence the note stamped on each unit, so a blank supplier
    column in the Units list reads as "we know" rather than "something broke".
    """

    quantity_on_hand = serializers.IntegerField(read_only=True)
    is_low_stock = serializers.SerializerMethodField()
    # Derived on the model so the Products list, the margin figure and any
    # future stock-valuation report all agree. Read-only: cost comes from
    # what was actually paid on a receipt, never typed here.
    latest_cost_excl_vat = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    average_cost_excl_vat = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    margin_pct = serializers.DecimalField(max_digits=6, decimal_places=1, read_only=True)
    # Write-only, create-only: [{"serial": "...", "mac": "..."}, ...]
    initial_units = serializers.ListField(
        child=serializers.DictField(child=serializers.CharField(allow_blank=True)),
        write_only=True,
        required=False,
    )

    class Meta:
        model = Product
        fields = [
            "id", "name", "sku", "category", "tracking_type", "unit",
            "low_stock_threshold", "description", "is_active",
            "sell_price", "sell_tax_rate_pct",
            "latest_cost_excl_vat", "average_cost_excl_vat", "margin_pct",
            "quantity_on_hand", "is_low_stock", "created_at", "initial_units",
        ]
        read_only_fields = [
            "id", "quantity_on_hand", "created_at",
            "latest_cost_excl_vat", "average_cost_excl_vat", "margin_pct",
        ]

    def get_is_low_stock(self, obj):
        if obj.low_stock_threshold is None:
            return False
        return obj.quantity_on_hand <= obj.low_stock_threshold

    def validate(self, attrs):
        units = attrs.get("initial_units") or []
        if not units:
            return attrs
        parsed = []
        seen_serials, seen_macs = set(), set()
        for index, raw in enumerate(units, start=1):
            serial = normalise_serial(raw.get("serial"))
            if not serial:
                # A MAC with no serial is almost always a half-filled row, so
                # skip a fully blank one and complain about the other case.
                if normalise_serial(raw.get("mac")):
                    raise serializers.ValidationError(
                        {"initial_units": f"Unit {index} has a MAC address but no serial number."}
                    )
                continue
            try:
                mac = normalise_mac(raw.get("mac"))
            except InvalidMac as exc:
                raise serializers.ValidationError({"initial_units": f"Unit {index}: {exc}"}) from exc
            if serial in seen_serials:
                raise serializers.ValidationError(
                    {"initial_units": f"Serial number '{serial}' was entered twice."}
                )
            seen_serials.add(serial)
            if mac:
                if mac in seen_macs:
                    raise serializers.ValidationError(
                        {"initial_units": f"MAC address '{mac}' was entered twice."}
                    )
                seen_macs.add(mac)
            parsed.append((serial, mac))

        clashing_serials = set(
            SerializedUnit.objects.filter(serial_number__in=seen_serials)
            .values_list("serial_number", flat=True)
        )
        if clashing_serials:
            raise serializers.ValidationError({
                "initial_units": "These serial numbers are already in the system: "
                                 f"{', '.join(sorted(clashing_serials))}"
            })
        if seen_macs:
            clashing_macs = set(
                SerializedUnit.objects.filter(mac_address__in=seen_macs)
                .values_list("mac_address", flat=True)
            )
            if clashing_macs:
                raise serializers.ValidationError({
                    "initial_units": "These MAC addresses are already in the system: "
                                     f"{', '.join(sorted(clashing_macs))}"
                })

        # Entering units IS the statement that this thing is tracked
        # individually, so don't make the user also find the dropdown -- just
        # set it. Silently keeping "quantity" would accept the serials and
        # then never show them anywhere. Set only if a unit actually survived
        # parsing: an empty row pair left untouched by someone who never
        # meant to use the section must not change how the product is
        # tracked.
        if parsed:
            attrs["tracking_type"] = Product.TrackingType.SERIALIZED
        attrs["initial_units"] = parsed
        return attrs

    def create(self, validated_data):
        units = validated_data.pop("initial_units", [])
        product = super().create(validated_data)
        for serial, mac in units:
            SerializedUnit.objects.create(
                product=product,
                serial_number=serial,
                mac_address=mac,
                notes="Added with the product — opening stock, no supplier invoice.",
            )
        return product

    def update(self, instance, validated_data):
        # Create-only. Adding stock to an existing product belongs on a
        # receipt, where it gets a supplier and a cost.
        validated_data.pop("initial_units", None)
        return super().update(instance, validated_data)


class SerializedUnitSerializer(serializers.ModelSerializer):
    """One physical unit. Writable for `serial_number`, `mac_address` and
    `notes` only -- see SerializedUnitViewSet for why corrections are allowed
    but status changes still belong to the receipt/issue flows.

    Carries where the unit came from: which supplier, on which supplier
    invoice, on what date. That provenance is the reason to record a serial
    at all -- an RMA needs to know who to send it back to."""

    product_name = serializers.CharField(source="product.name", read_only=True)
    # Read straight off the receipt line the unit arrived on. Null for a unit
    # whose receipt line was deleted (received_via_line is SET_NULL) rather
    # than an error -- the unit is still real, its paperwork just isn't.
    supplier_name = serializers.CharField(
        source="received_via_line.receipt.supplier.name", read_only=True, default=None
    )
    supplier_id = serializers.IntegerField(
        source="received_via_line.receipt.supplier_id", read_only=True, default=None
    )
    receipt_id = serializers.IntegerField(
        source="received_via_line.receipt_id", read_only=True, default=None
    )
    receipt_invoice_number = serializers.CharField(
        source="received_via_line.receipt.invoice_number", read_only=True, default=None
    )
    received_on = serializers.DateField(
        source="received_via_line.receipt.invoice_date", read_only=True, default=None
    )

    class Meta:
        model = SerializedUnit
        fields = [
            "id", "product", "product_name", "serial_number", "mac_address",
            "status", "notes", "created_at",
            "supplier_id", "supplier_name", "receipt_id", "receipt_invoice_number", "received_on",
        ]
        # product and status are fixed after check-in: moving a unit to a
        # different product would falsify both products' on-hand counts, and
        # status is owned by the receipt/issue flows.
        read_only_fields = ["id", "created_at", "product", "status"]

    def validate_serial_number(self, value):
        serial = normalise_serial(value)
        if not serial:
            raise serializers.ValidationError("A serial number is required.")
        clash = SerializedUnit.objects.filter(serial_number__iexact=serial)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError(
                f"Another unit already has serial number '{serial}'."
            )
        return serial

    def validate_mac_address(self, value):
        try:
            mac = normalise_mac(value)
        except InvalidMac as exc:
            raise serializers.ValidationError(str(exc)) from exc
        if not mac:
            return ""
        # Blank MACs are allowed to repeat (they mean "not recorded"); a real
        # one must not. Two units sharing a MAC is a fault you find out about
        # weeks later, when DHCP or RADIUS starts behaving strangely.
        clash = SerializedUnit.objects.filter(mac_address__iexact=mac)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        existing = clash.first()
        if existing is not None:
            raise serializers.ValidationError(
                f"MAC address {mac} is already on unit '{existing.serial_number}'."
            )
        return mac


class StockReceiptLineSerializer(serializers.ModelSerializer):
    """Read representation — used when displaying an existing receipt."""

    product_name = serializers.CharField(source="product.name", read_only=True)
    serial_count = serializers.SerializerMethodField()
    # Derived on the model so the same arithmetic backs the UI and the VAT
    # return -- whether unit_cost is VAT-inclusive is decided by the parent
    # receipt's prices_include_vat, not per line.
    unit_cost_excl_vat = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    line_excl_vat = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    line_vat = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    line_incl_vat = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    vat_recorded = serializers.BooleanField(read_only=True)

    class Meta:
        model = StockReceiptLine
        fields = [
            "id", "receipt", "product", "product_name", "quantity",
            "serial_numbers", "unit_cost", "vat_rate_pct", "serial_count",
            "unit_cost_excl_vat", "line_excl_vat", "line_vat", "line_incl_vat",
            "vat_recorded", "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def get_serial_count(self, obj):
        return obj.units.count()


class StockReceiptLineInputSerializer(serializers.ModelSerializer):
    """Write representation accepted when creating a receipt — no
    `receipt` field since that's supplied by the parent create()."""

    # Whether THIS delivery is being recorded unit by unit. Optional: omit it
    # and the product's own tracking_type decides, which is how this worked
    # before. Sending it lets someone check in serials without first having
    # gone to the Products tab to find a "tracking" dropdown they had no
    # reason to know about -- see the parent create() for the switch, and for
    # why it can be refused.
    track_individually = serializers.BooleanField(required=False, allow_null=True)

    class Meta:
        model = StockReceiptLine
        # vat_rate_pct is optional on input. Omit it and the parent
        # create() fills in the supplier's default; send it explicitly
        # (including 0) to override for a zero-rated or imported line.
        fields = ["product", "quantity", "serial_numbers", "unit_cost", "vat_rate_pct", "track_individually"]


class StockReceiptSerializer(SignedAttachmentMixin, serializers.ModelSerializer):
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    received_by_name = serializers.CharField(source="received_by.username", read_only=True, default=None)
    lines = StockReceiptLineSerializer(many=True, read_only=True)
    # `attachment` is deliberately NOT declared here.
    #
    # It used to be a SerializerMethodField, which reads fine and is
    # READ-ONLY -- and this serializer handles PATCH as well as GET, since
    # the viewset only swaps in the create serializer for `create`. The
    # stock receipt form uploads the file in a second PATCH, because the
    # nested line items cannot be expressed in multipart. So every upload
    # was accepted, silently discarded, and answered with 200: no file, no
    # error, and nothing on screen to say so.
    #
    # Leaving it undeclared lets ModelSerializer build an ordinary
    # writable FileField carrying the model's own validators (the
    # extension allowlist and size limit in config/uploads.py), so a bad
    # file is now REFUSED rather than dropped. to_representation below
    # still swaps the stored value for a signed link on the way out.
    # Rounded once per receipt (see StockReceipt.totals) so these agree to
    # the cent with the Input VAT the Accountant -> VAT Returns report
    # claims for this receipt.
    total_excl_vat = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    vat_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    total_incl_vat = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    has_unrecorded_vat = serializers.BooleanField(read_only=True)
    supplier_is_vat_registered = serializers.BooleanField(
        source="supplier.is_vat_registered", read_only=True
    )

    class Meta:
        model = StockReceipt
        fields = [
            "id", "supplier", "supplier_name", "supplier_is_vat_registered",
            "invoice_number", "invoice_date", "prices_include_vat",
            "attachment", "received_by", "received_by_name", "notes", "lines",
            "total_excl_vat", "vat_total", "total_incl_vat", "has_unrecorded_vat",
            "created_at",
        ]
        read_only_fields = ["id", "received_by", "created_at"]

    def to_representation(self, instance):
        # Signed, short-lived download link instead of the raw MEDIA path
        # DRF's FileField would render -- the protected media view would
        # refuse that anyway, so handing one back would look like success
        # and then 403 on click. Same treatment the create serializer
        # already gives it.
        data = super().to_representation(instance)
        data["attachment"] = self.get_attachment(instance)
        return data


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
        fields = [
            "id", "supplier", "invoice_number", "invoice_date",
            "prices_include_vat", "attachment", "notes", "lines",
        ]
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

            # A line that didn't specify a rate inherits the supplier's
            # default (0 for a supplier who isn't VAT-registered). A line
            # that sent one -- including 0 -- keeps it, so a zero-rated or
            # imported item can be recorded against a registered supplier.
            #
            # Note: `is None` rather than a truthiness test, because 0 is a
            # meaningful, deliberate rate here and must not be replaced by
            # the supplier default.
            default_vat_rate = receipt.supplier.effective_vat_rate_pct

            # Identifiers already claimed by an earlier line of THIS receipt.
            # The per-line checks below can't see across lines on their own.
            receipt_seen = {"Serial number": set(), "MAC address": set()}

            for line_data in lines_data:
                product = line_data["product"]
                if line_data.get("vat_rate_pct") is None:
                    line_data["vat_rate_pct"] = default_vat_rate

                # The line can override the product's tracking_type, and if it
                # does the product is switched to match so the catalogue and
                # the stock on the shelf keep telling the same story.
                #
                # Refused once the product already holds stock of the other
                # kind: quantity_on_hand is computed from StockMovement rows
                # for a quantity product and from SerializedUnit rows for a
                # serialized one, so flipping a product that has either would
                # make its existing stock silently vanish from the count.
                requested = line_data.pop("track_individually", None)
                if requested is not None:
                    wanted = (
                        Product.TrackingType.SERIALIZED if requested
                        else Product.TrackingType.QUANTITY
                    )
                    if wanted != product.tracking_type:
                        if product.tracking_type == Product.TrackingType.QUANTITY:
                            held = product.movements.exists()
                            holding = "quantity-tracked stock"
                        else:
                            held = product.serialized_units.exists()
                            holding = "individually-tracked units"
                        if held:
                            raise serializers.ValidationError(
                                f"'{product.name}' already has {holding} on record, so it can't be "
                                "switched now — its on-hand count is worked out from those. Create a "
                                "separate product for the individually-tracked version."
                            )
                        product.tracking_type = wanted
                        product.save(update_fields=["tracking_type"])

                if product.tracking_type == Product.TrackingType.SERIALIZED:
                    raw_text = line_data.get("serial_numbers", "") or ""
                    try:
                        parsed = parse_serial_lines(raw_text)
                    except (InvalidMac, ValueError) as exc:
                        raise serializers.ValidationError(f"'{product.name}': {exc}") from exc
                    if not parsed:
                        raise serializers.ValidationError(
                            f"'{product.name}' is a serialized product — enter at least one serial number."
                        )

                    # Duplicates are checked in three places, because all
                    # three happen in practice: the same unit keyed twice in
                    # one line, the same unit split across two lines of one
                    # receipt (a genuine scanner double-tap), and a unit that
                    # is already on the shelf from an earlier delivery.
                    for label, values in (
                        ("Serial number", [s for s, _ in parsed]),
                        ("MAC address", [m for _, m in parsed if m]),
                    ):
                        seen = set()
                        for value in values:
                            if value in seen:
                                raise serializers.ValidationError(
                                    f"{label} '{value}' was entered twice for '{product.name}'."
                                )
                            seen.add(value)
                        clash = seen & receipt_seen[label]
                        if clash:
                            raise serializers.ValidationError(
                                f"{label} '{sorted(clash)[0]}' appears on more than one line of this receipt."
                            )
                        receipt_seen[label] |= seen

                    serials = [s for s, _ in parsed]
                    macs = [m for _, m in parsed if m]
                    existing_serials = set(
                        SerializedUnit.objects.filter(serial_number__in=serials)
                        .values_list("serial_number", flat=True)
                    )
                    if existing_serials:
                        raise serializers.ValidationError(
                            "These serial numbers are already in the system: "
                            f"{', '.join(sorted(existing_serials))}"
                        )
                    if macs:
                        existing_macs = set(
                            SerializedUnit.objects.filter(mac_address__in=macs)
                            .values_list("mac_address", flat=True)
                        )
                        if existing_macs:
                            raise serializers.ValidationError(
                                "These MAC addresses are already in the system: "
                                f"{', '.join(sorted(existing_macs))}"
                            )

                    line = StockReceiptLine.objects.create(
                        receipt=receipt,
                        product=product,
                        quantity=len(parsed),
                        # Stored back in canonical form, not as typed, so the
                        # receipt's own record matches the units it created.
                        serial_numbers="\n".join(
                            f"{serial},{mac}" if mac else serial for serial, mac in parsed
                        ),
                        unit_cost=line_data.get("unit_cost"),
                        vat_rate_pct=line_data.get("vat_rate_pct"),
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
                        vat_rate_pct=line_data.get("vat_rate_pct"),
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
