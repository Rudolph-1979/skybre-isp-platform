from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models

from config.uploads import ATTACHMENT_VALIDATORS


class Supplier(models.Model):
    """A vendor Skybre buys network/CPE equipment and consumables from."""

    name = models.CharField(max_length=255, unique=True)
    contact_person = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # --- VAT ---------------------------------------------------------------
    # A supplier not registered for VAT cannot legally charge it, so no Input
    # VAT can be claimed on their invoices. These fields only ever supply the
    # *default* rate on a stock receipt line -- the rate that actually counts
    # is stored per line (StockReceiptLine.vat_rate_pct), deliberately:
    #
    #   * one supplier invoice can mix standard-rated and zero-rated items,
    #   * imports carry customs VAT rather than the supplier's own VAT, and
    #   * a supplier can register or deregister mid-year, and that must not
    #     retroactively rewrite the VAT on receipts already captured.
    is_vat_registered = models.BooleanField(
        default=True,
        help_text=(
            "Uncheck for a supplier not registered for VAT — their receipt lines "
            "default to 0% and no Input VAT can be claimed on them."
        ),
    )
    vat_number = models.CharField(
        max_length=32,
        blank=True,
        help_text="The supplier's own SARS VAT registration number, as printed on their invoices.",
    )
    default_vat_rate_pct = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("15"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
        help_text=(
            "Pre-fills the VAT rate on each new receipt line for this supplier. "
            "Ignored while 'VAT registered' is unchecked."
        ),
    )
    # A given supplier almost always quotes prices the same way on every
    # invoice, so this pre-fills the receipt's own prices_include_vat toggle
    # and saves choosing it every time. It is only a default -- the receipt
    # stores its own copy, so an unusual invoice can still be entered the
    # other way without changing the supplier.
    default_prices_include_vat = models.BooleanField(
        default=False,
        help_text=(
            "How this supplier normally quotes prices. Pre-fills the "
            "'unit costs include VAT' toggle when checking in their stock."
        ),
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def effective_vat_rate_pct(self):
        """The rate a new receipt line for this supplier should default to.

        Not stored anywhere -- it is a suggestion the receipt form pre-fills
        and staff can override per line.
        """
        if not self.is_vat_registered:
            return Decimal("0")
        return self.default_vat_rate_pct


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
    # --- resale --------------------------------------------------------
    # What this item is SOLD to a customer for, as opposed to what it cost
    # to buy (which lives per-delivery on StockReceiptLine.unit_cost, since
    # the same router bought twice a year apart cost two different amounts).
    #
    # Stored ex-VAT to match InvoiceItem.unit_price, which is also ex-VAT --
    # so the invoice line can be filled straight from here with no conversion
    # and nothing lost to rounding on the way.
    sell_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        verbose_name="Resell price (excl. VAT)",
        help_text="What a customer is charged for one of these, excluding VAT. Blank = not for resale.",
    )
    sell_tax_rate_pct = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("15"),
        verbose_name="Resell VAT rate (%)",
        help_text="VAT charged when selling this item. 0 for a zero-rated item.",
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

    # --- cost, for working out what the margin actually is ---------------
    # Both walk this product's receipt lines. `receipt` is select_related
    # because unit_cost_excl_vat reads it to decide whether the captured cost
    # was VAT-inclusive -- without it that's a query per line.

    def _costed_lines(self):
        """Receipt lines that captured a cost.

        Deliberately `self.receipt_lines.all()` filtered in Python rather than
        a filtered queryset: `.exclude(...)` or `.order_by(...)` would ignore
        `prefetch_related("receipt_lines__receipt")` and issue a fresh query,
        which on the Products list meant four extra queries per product. This
        way the list is a fixed number of queries no matter how long it is.
        """
        return [line for line in self.receipt_lines.all() if line.unit_cost is not None]

    @property
    def latest_cost_excl_vat(self):
        """Ex-VAT cost from the most recent delivery, or None.

        The most recent price is what a replacement would cost today, which is
        the honest number to price against -- an average is dragged down by
        stock bought two years ago at a price the supplier no longer offers.
        """
        lines = self._costed_lines()
        if not lines:
            return None
        latest = max(lines, key=lambda line: (line.receipt.invoice_date, line.pk))
        return latest.unit_cost_excl_vat

    @property
    def average_cost_excl_vat(self):
        """Quantity-weighted ex-VAT cost across every delivery, or None.

        Weighted, not a plain mean of the unit costs: one unit bought
        expensively should not move the figure as much as fifty bought
        cheaply. This is the number stock on hand is worth.
        """
        total_cost = Decimal("0")
        total_qty = 0
        for line in self._costed_lines():
            unit = line.unit_cost_excl_vat
            if unit is None or not line.quantity:
                continue
            total_cost += unit * line.quantity
            total_qty += line.quantity
        if not total_qty:
            return None
        return (total_cost / total_qty).quantize(Decimal("0.01"))

    @property
    def margin_pct(self):
        """Gross margin on the latest cost, as a percentage of the SELL price.

        Margin, not markup -- they get confused constantly and the difference
        is not small. Buy at 100, sell at 150: the markup is 50% but the
        margin is 33.3%. Margin is the one that tells you what share of the
        sale you keep, so it is the one shown. None when either side is
        missing, or when the sell price is zero (nothing to take a share of).
        """
        cost = self.latest_cost_excl_vat
        if cost is None or self.sell_price is None or self.sell_price == 0:
            return None
        return ((self.sell_price - cost) / self.sell_price * Decimal("100")).quantize(Decimal("0.1"))


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
    attachment = models.FileField(
        upload_to="supplier_invoices/%Y/%m/", null=True, blank=True, validators=ATTACHMENT_VALIDATORS,
    )
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_receipts",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Supplier invoices arrive both ways -- some quote VAT-inclusive prices,
    # some exclusive. This records which convention the unit costs on THIS
    # receipt were typed in, so staff never have to back-calculate by hand
    # (which is exactly where cent-level errors and accidental double-VAT
    # creep in). It applies to every line on the receipt; the VAT *rate*
    # is still per line, so a mixed standard/zero-rated invoice works.
    prices_include_vat = models.BooleanField(
        default=False,
        help_text=(
            "Tick if the unit costs below were typed exactly as they appear on a "
            "VAT-inclusive supplier invoice. Leave unticked for VAT-exclusive prices."
        ),
    )

    class Meta:
        ordering = ["-invoice_date", "-created_at"]

    def __str__(self):
        return f"Receipt {self.invoice_number} — {self.supplier}"

    @property
    def totals(self):
        """(excl_vat, vat, incl_vat) for the whole receipt, as Decimals.

        VAT is summed unrounded across the lines and rounded ONCE for the
        receipt rather than per line. The receipt is the document that
        corresponds to one supplier invoice, so this is the figure whoever
        files the VAT return reconciles against, and it has to agree with
        the receipt's own displayed total to the cent.
        `_sum_input_vat` in expenses/views.py rounds the same way, for the
        same reason.

        Which side gets rounded and which gets derived by subtraction
        depends on how the prices were entered: the figures the user
        actually typed are exact, so those are preserved and the other
        component is derived. That keeps excl + vat == incl always, and
        keeps the receipt total equal to the supplier's invoice total.
        """
        excl_raw = Decimal("0")
        vat_raw = Decimal("0")
        incl_raw = Decimal("0")
        for line in self.lines.all():
            excl_raw += line.line_excl_vat_raw
            vat_raw += line.line_vat_raw
            incl_raw += line.line_incl_vat_raw

        cents = Decimal("0.01")
        if self.prices_include_vat:
            incl = incl_raw.quantize(cents)
            vat = vat_raw.quantize(cents)
            excl = incl - vat
        else:
            excl = excl_raw.quantize(cents)
            vat = vat_raw.quantize(cents)
            incl = excl + vat
        return excl, vat, incl

    @property
    def total_excl_vat(self):
        return self.totals[0]

    @property
    def vat_total(self):
        return self.totals[1]

    @property
    def total_incl_vat(self):
        return self.totals[2]

    @property
    def has_unrecorded_vat(self):
        """True if any line predates VAT tracking (rate is NULL).

        Those lines contribute their cost but no Input VAT, and the UI
        flags them so the number isn't silently understated.
        """
        return any(not line.vat_recorded for line in self.lines.all())


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
    # Whether this figure is VAT-inclusive or -exclusive is decided by the
    # parent receipt's `prices_include_vat`. It is stored exactly as typed,
    # so nothing is lost to rounding at entry time.
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    # NULL is meaningful: it marks a line captured before VAT tracking
    # existed. Those are excluded from the VAT return entirely rather than
    # being assumed to be 15% (which would invent an Input VAT claim) or 0%
    # (which would silently understate one). 0 is different from NULL and
    # means a genuinely zero-rated or exempt line.
    vat_rate_pct = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
        help_text=(
            "VAT rate for this line. Pre-filled from the supplier; override it for "
            "zero-rated or imported items. Blank means VAT was never recorded — the "
            "line is then left out of the VAT return rather than guessed at."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.product.name} x{self.quantity}"

    # --- VAT arithmetic ----------------------------------------------------
    # The *_raw properties are deliberately unrounded. Callers round once at
    # whatever level they display or report, so rounding never compounds.
    #
    # NOTE: these read `self.receipt`. Accessed via `receipt.lines.all()`
    # that FK is already cached, but code querying StockReceiptLine directly
    # must `.select_related("receipt")` or it is one query per line.

    @property
    def vat_recorded(self):
        """False for lines with no cost or no VAT rate captured."""
        return self.vat_rate_pct is not None and self.unit_cost is not None

    @property
    def line_total_as_entered(self):
        """quantity x unit_cost, in whichever convention it was typed."""
        if self.unit_cost is None:
            return Decimal("0")
        return self.unit_cost * self.quantity

    @property
    def line_excl_vat_raw(self):
        total = self.line_total_as_entered
        if not self.vat_recorded:
            # Take the figure at face value and claim nothing. Guessing
            # would either invent a VAT claim or change a historical cost.
            return total
        if self.receipt.prices_include_vat:
            return total / (Decimal("1") + self.vat_rate_pct / Decimal("100"))
        return total

    @property
    def line_incl_vat_raw(self):
        total = self.line_total_as_entered
        if not self.vat_recorded:
            return total
        if self.receipt.prices_include_vat:
            return total
        return total + (total * self.vat_rate_pct / Decimal("100"))

    @property
    def line_vat_raw(self):
        if not self.vat_recorded:
            return Decimal("0")
        return self.line_incl_vat_raw - self.line_excl_vat_raw

    @property
    def line_excl_vat(self):
        return self.line_excl_vat_raw.quantize(Decimal("0.01"))

    @property
    def line_vat(self):
        return self.line_vat_raw.quantize(Decimal("0.01"))

    @property
    def line_incl_vat(self):
        return self.line_incl_vat_raw.quantize(Decimal("0.01"))

    @property
    def unit_cost_excl_vat(self):
        """What one unit cost net of VAT — the figure stock should be
        valued at. None when no cost was captured."""
        if self.unit_cost is None:
            return None
        if not self.vat_recorded or not self.receipt.prices_include_vat:
            return self.unit_cost
        return (
            self.unit_cost / (Decimal("1") + self.vat_rate_pct / Decimal("100"))
        ).quantize(Decimal("0.01"))


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
        limit_choices_to={"role__in": ["admin", "support", "sales", "technician", "management", "accounts"]},
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
