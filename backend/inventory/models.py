from django.conf import settings
from django.db import models


class Supplier(models.Model):
    """A vendor Skybre buys network/CPE equipment and consumables from."""

    name = models.CharField(max_length=255, unique=True)
    contact_person = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Product(models.Model):
    """A stock catalog entry — either an individually-tracked item
    (routers, ONTs — identified by serial/MAC, useful for warranty/RMA)
    or a bulk consumable tracked purely by quantity (cable, connectors)."""

    class Category(models.TextChoices):
        ROUTER = "router", "Router / CPE"
        ONT = "ont", "ONT"
        CABLE = "cable", "Cable"
        CONNECTOR = "connector", "Connector"
        TOOL = "tool", "Tool / Equipment"
        OTHER = "other", "Other"

    class TrackingType(models.TextChoices):
        SERIALIZED = "serialized", "Individually tracked (serial/MAC)"
        QUANTITY = "quantity", "Tracked by quantity"

    name = models.CharField(max_length=255)
    sku = models.CharField(max_length=100, blank=True)
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.OTHER)
    tracking_type = models.CharField(
        max_length=20, choices=TrackingType.choices, default=TrackingType.QUANTITY
    )
    unit = models.CharField(
        max_length=20,
        default="each",
        help_text="e.g. each, meter, box — ignored for serialized products.",
    )
    low_stock_threshold = models.PositiveIntegerField(
        null=True, blank=True, help_text="Flag as low stock at or below this quantity."
    )
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def quantity_on_hand(self):
        if self.tracking_type == self.TrackingType.SERIALIZED:
            return self.serialized_units.filter(status=SerializedUnit.Status.IN_STOCK).count()
        total = self.movements.aggregate(total=models.Sum("quantity"))["total"]
        return total or 0


class SerializedUnit(models.Model):
    """One physical, individually-identified unit of a serialized Product
    (e.g. a single router). MAC address is stored alongside the serial
    since ISP equipment is commonly labelled/tracked by both."""

    class Status(models.TextChoices):
        IN_STOCK = "in_stock", "In Stock"
        ISSUED = "issued", "Issued"
        FAULTY = "faulty", "Faulty"
        RETURNED = "returned_to_supplier", "Returned to Supplier"

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="serialized_units")
    serial_number = models.CharField(max_length=100, unique=True)
    mac_address = models.CharField(
        max_length=17, blank=True, help_text="e.g. AA:BB:CC:DD:EE:FF — routers/ONTs only."
    )
    status = models.CharField(max_length=25, choices=Status.choices, default=Status.IN_STOCK)
    received_via_line = models.ForeignKey(
        "StockReceiptLine",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="units",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.serial_number} ({self.product.name})"


class StockReceipt(models.Model):
    """Stock checked in against a real supplier invoice."""

    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="receipts")
    invoice_number = models.CharField(max_length=100)
    invoice_date = models.DateField()
    attachment = models.FileField(upload_to="supplier_invoices/%Y/%m/", null=True, blank=True)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_receipts",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-invoice_date", "-created_at"]

    def __str__(self):
        return f"Receipt {self.invoice_number} — {self.supplier}"


class StockReceiptLine(models.Model):
    receipt = models.ForeignKey(StockReceipt, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="receipt_lines")
    quantity = models.PositiveIntegerField(
        default=1,
        help_text="For quantity-tracked products. Ignored for serialized products — count comes from serial numbers entered.",
    )
    serial_numbers = models.TextField(
        blank=True,
        help_text="For serialized products: one serial per line, optionally 'SERIAL,MAC'.",
    )
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.product.name} x{self.quantity}"


class StockIssue(models.Model):
    """Stock issued out to a technician, optionally for a specific job."""

    job = models.ForeignKey(
        "scheduling.Job",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_issues",
    )
    issued_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_issued_to",
        limit_choices_to={"role__in": ["admin", "staff", "technician"]},
    )
    issued_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_issues_created",
    )

    class Meta:
        ordering = ["-issued_at"]

    def __str__(self):
        return f"Issue #{self.pk}"


class StockIssueLine(models.Model):
    issue = models.ForeignKey(StockIssue, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="issue_lines")
    quantity = models.PositiveIntegerField(default=1, help_text="For quantity-tracked products.")
    serial_unit = models.ForeignKey(
        SerializedUnit,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="issue_lines",
    )

    def __str__(self):
        return f"{self.product.name} x{self.quantity}"


class StockMovement(models.Model):
    """Auditable ledger of every quantity change for quantity-tracked
    products. Serialized products track history via SerializedUnit.status
    instead — this table exists so quantity-on-hand for bulk items
    (cables, connectors) is a running total derived from history, not a
    mutable counter that can silently drift out of sync."""

    class MovementType(models.TextChoices):
        RECEIPT = "receipt", "Receipt"
        ISSUE = "issue", "Issue"
        ADJUSTMENT = "adjustment", "Adjustment"

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="movements")
    movement_type = models.CharField(max_length=20, choices=MovementType.choices)
    quantity = models.IntegerField(help_text="Positive for stock in, negative for stock out.")
    receipt_line = models.ForeignKey(
        StockReceiptLine, on_delete=models.SET_NULL, null=True, blank=True, related_name="movements"
    )
    issue_line = models.ForeignKey(
        StockIssueLine, on_delete=models.SET_NULL, null=True, blank=True, related_name="movements"
    )
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="stock_movements"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.movement_type} {self.quantity} — {self.product.name}"
