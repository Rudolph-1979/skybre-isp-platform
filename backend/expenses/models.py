from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models

from config.uploads import ATTACHMENT_VALIDATORS


class Expense(models.Model):
    """A real business expense/purchase -- rent, bandwidth, equipment,
    fuel, software, professional fees, etc. -- entered by staff so the
    VAT paid on it (Input VAT) can feed into the Accountant -> VAT
    Returns report alongside Output VAT from real customer invoices.
    There was no expense/purchase tracking anywhere in this codebase
    before this (see inventory.StockReceipt, which tracks stock coming
    in but has no VAT field at all) -- this is the first.

    `date` is deliberately the date on the supplier's own invoice/
    receipt, not when it was entered here -- that's what determines
    which VAT period (see VatReturnView in this app's views.py) the
    input VAT on this expense falls into, on the invoice/accrual basis
    Skybre files under.
    """

    class Category(models.TextChoices):
        BANDWIDTH = "bandwidth", "Bandwidth / Transit"
        EQUIPMENT = "equipment", "Equipment / Hardware"
        FUEL = "fuel", "Fuel / Vehicles"
        RENT = "rent", "Rent / Facilities"
        UTILITIES = "utilities", "Utilities"
        SOFTWARE = "software", "Software / Subscriptions"
        PROFESSIONAL = "professional", "Professional fees (legal / accounting)"
        SALARIES = "salaries", "Salaries / Payroll"
        OTHER = "other", "Other"

    supplier = models.ForeignKey(
        "inventory.Supplier",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="expenses",
        help_text="Optional link to an existing Supplier record (Stock / Inventory -> Suppliers).",
    )
    # Free-text fallback -- most expenses (rent, electricity, a software
    # subscription) won't have a matching inventory.Supplier record, and
    # forcing one to be created just to log an expense would be busywork.
    supplier_name = models.CharField(
        max_length=200, blank=True,
        help_text="Supplier/payee name, if not linked to a Supplier record above.",
    )
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.OTHER)
    # The chart account this expense belongs to.
    #
    # Kept ALONGSIDE `category` rather than replacing it. category still
    # drives the salaries/VAT rule and the existing reporting, and swapping
    # one for the other in a single release would mean rewriting both in a
    # migration that also has to be right about VAT. The account is the
    # richer one and can grow into the primary; for now it is stamped from
    # the supplier's category on capture, and stored, so that changing a
    # supplier's category later never re-posts an expense already declared
    # in a filed VAT period.
    account = models.ForeignKey(
        "ledger.Account",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="expenses",
        limit_choices_to={"type": "expense"},
    )
    # The vehicle this cost is for -- a workshop invoice, tyres, a part
    # (George, 13 Oct 2026). Optional. A linked expense shows on the
    # vehicle's page and counts toward its running cost per km. Fuel is NOT
    # linked here: fill-ups come from the vehicle's own fuel log, and
    # linking a fuel slip too would count it twice.
    vehicle = models.ForeignKey(
        "fleet.Vehicle",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="expenses",
        help_text="Optional: the vehicle this cost is for (maintenance, repairs, tyres). Not for fuel.",
    )
    description = models.CharField(max_length=255)
    invoice_number = models.CharField(
        max_length=100, blank=True, help_text="The supplier's own invoice/receipt number, if any.",
    )
    date = models.DateField(
        help_text="The date on the supplier's invoice/receipt -- this determines which VAT period this expense's Input VAT falls into.",
    )
    amount_excl_vat = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0"))],
    )
    vat_rate_pct = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("15"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
        help_text="0 for zero-rated/exempt purchases (e.g. most bank charges) -- no Input VAT can be claimed on those.",
    )
    attachment = models.FileField(
        upload_to="expense_receipts/%Y/%m/", null=True, blank=True, validators=ATTACHMENT_VALIDATORS,
    )
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "-created_at"]

    def __str__(self):
        return f"{self.description} — R{self.amount_excl_vat} ({self.date})"

    @property
    def vat_amount(self):
        return (self.amount_excl_vat * self.vat_rate_pct / Decimal("100")).quantize(Decimal("0.01"))

    @property
    def amount_incl_vat(self):
        return self.amount_excl_vat + self.vat_amount
