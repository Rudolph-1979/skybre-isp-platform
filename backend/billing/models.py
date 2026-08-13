from django.conf import settings
from django.db import models


class Tariff(models.Model):
    class ServiceType(models.TextChoices):
        INTERNET = "internet", "Internet"
        VOICE = "voice", "Voice"
        BUNDLE = "bundle", "Bundle"
        OTHER = "other", "Other"

    class BillingPeriod(models.TextChoices):
        MONTHLY = "monthly", "Monthly"
        QUARTERLY = "quarterly", "Quarterly"
        ANNUALLY = "annually", "Annually"

    name = models.CharField(max_length=150)
    service_type = models.CharField(max_length=20, choices=ServiceType.choices, default=ServiceType.INTERNET)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    billing_period = models.CharField(max_length=20, choices=BillingPeriod.choices, default=BillingPeriod.MONTHLY)
    speed_download_mbps = models.PositiveIntegerField(null=True, blank=True)
    speed_upload_mbps = models.PositiveIntegerField(null=True, blank=True)
    data_cap_gb = models.PositiveIntegerField(null=True, blank=True, help_text="Blank = unlimited")
    tax_rate_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.price}/{self.billing_period})"


class Service(models.Model):
    """A customer's active subscription to a tariff/plan."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"
        TERMINATED = "terminated", "Terminated"
        PENDING = "pending", "Pending Activation"

    customer = models.ForeignKey("customers.Customer", on_delete=models.CASCADE, related_name="services")
    tariff = models.ForeignKey(Tariff, on_delete=models.PROTECT, related_name="services")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    device = models.ForeignKey(
        "network.Device", on_delete=models.SET_NULL, null=True, blank=True, related_name="services"
    )
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.customer} -> {self.tariff}"


class Invoice(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        UNPAID = "unpaid", "Unpaid"
        PAID = "paid", "Paid"
        OVERDUE = "overdue", "Overdue"
        CANCELLED = "cancelled", "Cancelled"

    number = models.CharField(max_length=30, unique=True, editable=False)
    customer = models.ForeignKey("customers.Customer", on_delete=models.CASCADE, related_name="invoices")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    date_created = models.DateField(auto_now_add=True)
    date_due = models.DateField()
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-date_created"]

    def save(self, *args, **kwargs):
        if not self.number:
            last = Invoice.objects.order_by("-id").first()
            next_num = (last.id + 1) if last else 1
            self.number = f"INV-{next_num:06d}"
        super().save(*args, **kwargs)

    def recalc_totals(self):
        items = self.items.all()
        self.subtotal = sum((i.quantity * i.unit_price for i in items), start=0)
        self.tax_total = sum((i.quantity * i.unit_price * (i.tax_rate_pct / 100) for i in items), start=0)
        self.total = self.subtotal + self.tax_total
        if self.paid_amount >= self.total and self.total > 0:
            self.status = self.Status.PAID
        self.save()

    def __str__(self):
        return self.number


class InvoiceItem(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="items")
    service = models.ForeignKey(Service, on_delete=models.SET_NULL, null=True, blank=True)
    description = models.CharField(max_length=255)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    tax_rate_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    @property
    def total(self):
        return self.quantity * self.unit_price * (1 + self.tax_rate_pct / 100)

    def __str__(self):
        return f"{self.description} x{self.quantity}"


class Payment(models.Model):
    class Method(models.TextChoices):
        CASH = "cash", "Cash"
        CARD = "card", "Card"
        BANK_TRANSFER = "bank_transfer", "Bank Transfer"
        MOBILE_MONEY = "mobile_money", "Mobile Money"
        MANUAL = "manual", "Manual Adjustment"

    customer = models.ForeignKey("customers.Customer", on_delete=models.CASCADE, related_name="payments")
    invoice = models.ForeignKey(Invoice, on_delete=models.SET_NULL, null=True, blank=True, related_name="payments")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=20, choices=Method.choices, default=Method.CASH)
    date = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=255, blank=True)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="payments_received"
    )

    class Meta:
        ordering = ["-date"]

    def __str__(self):
        return f"Payment {self.amount} - {self.customer}"
