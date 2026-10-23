"""Enquiries, before they are customers.

A Lead is deliberately NOT a Customer with status "New", which is the
obvious way to build this and the wrong one here. Most enquiries never
buy anything. Filing them as customers would inflate every count, every
export and every dashboard tile with people who never became anything --
and this platform gates customer deletion behind an approval workflow
(customers.CustomerDeletionRequest), so clearing out two hundred dead
enquiries would mean two hundred deletion requests.

So a lead lives here until somebody actually commits, at which point
`convert_to_customer()` creates the Customer once. The link is kept in
both directions afterwards, so "where did this customer come from" and
"what did this lead become" are both answerable.
"""
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone



def _discount_amount(spec, base):
    """{"type": "percentage"|"amount", "value": "10"} off `base`, in rand,
    rounded to cents and capped at the base. Anything else is no discount."""
    from decimal import ROUND_HALF_UP, InvalidOperation

    if not spec or spec.get("type") not in ("percentage", "amount"):
        return Decimal("0.00")
    try:
        value = Decimal(str(spec.get("value") or "0"))
    except (InvalidOperation, ValueError):
        return Decimal("0.00")
    base = Decimal(base or 0)
    if value <= 0 or base <= 0:
        return Decimal("0.00")
    raw = base * value / Decimal("100") if spec["type"] == "percentage" else value
    return min(raw, base).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

class Lead(models.Model):
    # Rudolph's flow (6 Oct 2026): New -> Qualify -> Coverage check
    # (Electronic LOS / Scan LOS / Fibre check) -> Quote -> Accept or
    # Reject, with Lost possible at any step. "Contacted" was dropped --
    # the follow-up date and the timeline already say that -- and its
    # leads moved back to New (migration 0002). "won" keeps its key and
    # now reads "Accepted".
    class Status(models.TextChoices):
        NEW = "new", "New"
        # Worth pursuing: they want it and could pay for it.
        QUALIFIED = "qualified", "Qualified"
        COVERAGE = "coverage", "Coverage check"
        QUOTED = "quoted", "Quoted"
        WON = "won", "Accepted"
        LOST = "lost", "Lost"

    # Stages a lead is still moving through. Named once so the follow-up
    # queries and the pipeline totals can't drift apart.
    OPEN_STATUSES = (Status.NEW, Status.QUALIFIED, Status.COVERAGE, Status.QUOTED)
    CLOSED_STATUSES = (Status.WON, Status.LOST)

    class Source(models.TextChoices):
        WALK_IN = "walk_in", "Walk-in"
        PHONE = "phone", "Phone enquiry"
        REFERRAL = "referral", "Referral"
        WEBSITE = "website", "Website"
        SOCIAL = "social", "Social media"
        RESELLER = "reseller", "Reseller"
        CAMPAIGN = "campaign", "Campaign"
        EXISTING_CUSTOMER = "existing_customer", "Existing customer"
        OTHER = "other", "Other"

    class LostReason(models.TextChoices):
        # First on the list because it is the one worth acting on: it is
        # not a sales failure, it is a coverage gap, and phase 2 turns
        # these into the case for where the next tower goes.
        NO_LOS = "no_los", "No line of sight"
        NO_FIBRE = "no_fibre", "Fibre not available"
        QUOTE_REJECTED = "quote_rejected", "Quote rejected"
        NO_COVERAGE = "no_coverage", "No coverage"
        PRICE = "price", "Price"
        COMPETITOR = "competitor", "Went with a competitor"
        WENT_QUIET = "went_quiet", "Went quiet"
        NOT_READY = "not_ready", "Not ready yet"
        DUPLICATE = "duplicate", "Duplicate enquiry"
        OTHER = "other", "Other"

    full_name = models.CharField(max_length=255)
    company_name = models.CharField(max_length=255, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)

    # Kept in the same shape as Customer's so conversion is a straight
    # copy rather than a re-typing exercise.
    address = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    zip_code = models.CharField(max_length=20, blank=True)

    source = models.CharField(max_length=20, choices=Source.choices, default=Source.PHONE)
    # Free text for the specifics the choice above can't hold -- which
    # campaign, who referred them. "Referral" alone never answers the
    # question anyone actually asks, which is who to thank.
    source_detail = models.CharField(max_length=255, blank=True)

    partner = models.ForeignKey(
        "customers.Partner",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="leads",
        help_text="Reseller this enquiry came through, if any.",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_leads",
        limit_choices_to={"role__in": ["admin", "support", "sales", "management"]},
    )
    interested_tariff = models.ForeignKey(
        "billing.Tariff",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="leads",
        help_text="What they asked about. Sets the pipeline value unless overridden below.",
    )
    # Overrides the tariff price when the deal isn't a standard plan.
    # Nullable rather than 0-defaulted: 0 is a real answer ("free trial")
    # and "nobody has said yet" is a different one.
    estimated_monthly_value = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW, db_index=True)
    lost_reason = models.CharField(max_length=20, choices=LostReason.choices, blank=True)
    # The stage a lead was at when it was lost, so the funnel report can say
    # WHERE people drop out, not only why. Stamped by save().
    lost_at_stage = models.CharField(max_length=20, blank=True)

    # Where they are, for a farm with no street address -- and where a scan
    # job and the installation job are sent.
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    # --- Coverage check (6 Oct 2026) --------------------------------------
    class CoverageMethod(models.TextChoices):
        ELECTRONIC = "electronic", "Electronic LOS"
        SCAN = "scan", "Scan LOS"
        FIBRE = "fibre", "Fibre check"

    class CoverageResult(models.TextChoices):
        PASS = "pass", "Pass"
        FAIL = "fail", "Fail"
        FIBRE_CONFIRMED = "fibre_confirmed", "Fibre confirmed"
        FIBRE_UNAVAILABLE = "fibre_unavailable", "Fibre not available"

    #: Results that let the lead go on to a quote.
    COVERAGE_OK = (CoverageResult.PASS, CoverageResult.FIBRE_CONFIRMED)

    coverage_method = models.CharField(max_length=12, choices=CoverageMethod.choices, blank=True)
    coverage_result = models.CharField(max_length=20, choices=CoverageResult.choices, blank=True)
    coverage_tower = models.CharField(max_length=100, blank=True)
    coverage_sector = models.CharField(max_length=100, blank=True)
    coverage_signal = models.CharField(max_length=50, blank=True)
    coverage_notes = models.TextField(blank=True)
    # The fibre network found at the address (a Partner, e.g. Vumatel).
    coverage_partner = models.ForeignKey(
        "customers.Partner", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    coverage_checked_at = models.DateTimeField(null=True, blank=True)
    coverage_checked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    # The Site Visit job a Scan LOS booked in Scheduling.
    scan_job = models.ForeignKey(
        "scheduling.Job", on_delete=models.SET_NULL, null=True, blank=True, related_name="scan_for_leads",
    )

    # --- Quote ------------------------------------------------------------
    # The plan is interested_tariff; these are the once-off extras,
    # [{"label": "Installation", "amount": "1500.00"}, ...], excl. VAT.
    quote_items = models.JSONField(default=list, blank=True)
    # Monthly extras on top of the plan (7 Oct 2026): recurring charges such
    # as IP rental, [{"label": "IP Rental", "amount": "100.00",
    # "recurring_charge": 3}, ...], excl. VAT. recurring_charge is the
    # Configs -> Tariffs -> Recurring Charges entry it came from, or absent.
    quote_recurring = models.JSONField(default=list, blank=True)
    # The day the service starts (7 Oct 2026, Rudolph: clients come on
    # halfway into the month and are charged from that date to month end,
    # then recurring billing). Drives the quote's pro-rata line. Blank = no
    # pro rata on the quote.
    quote_start_date = models.DateField(null=True, blank=True)
    # Discounts on the quote (7 Oct 2026), the same two kinds a service has
    # (percentage / rand amount, excl. VAT):
    # {"monthly": {"type": "percentage", "value": "10", "months": 3},
    #  "once_off": {"type": "amount", "value": "500"}, "reason": "Launch special"}.
    # The monthly one comes off the PLAN price -- exactly how the service
    # discount bills it later -- and months blank = ongoing.
    quote_discount = models.JSONField(default=dict, blank=True)
    quote_valid_until = models.DateField(null=True, blank=True)
    quote_sent_at = models.DateTimeField(null=True, blank=True)
    # The installation job Accept put in the job pool.
    install_job = models.ForeignKey(
        "scheduling.Job", on_delete=models.SET_NULL, null=True, blank=True, related_name="install_for_leads",
    )

    # A date, not a datetime. Nobody follows up at 14:30 -- they work a
    # list for the day, and asking for a time would make the field
    # annoying enough to leave blank.
    next_follow_up = models.DateField(
        null=True, blank=True, db_index=True,
        help_text="When to chase them next. Blank means nothing is scheduled.",
    )

    # What this became. Set by convert_to_customer(); SET_NULL so deleting
    # a customer doesn't erase the record of where they came from.
    customer = models.ForeignKey(
        "customers.Customer",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="originating_leads",
    )

    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "next_follow_up"]),
            models.Index(fields=["assigned_to", "status"]),
        ]

    def __str__(self):
        return self.company_name or self.full_name

    # --- derived ---------------------------------------------------------

    @property
    def value(self):
        """Monthly value for pipeline totals.

        The explicit override wins; otherwise the tariff's own price. A
        lead with neither counts as zero rather than being excluded --
        excluding it would quietly shrink the pipeline count as well as
        its value, and the count is the number people trust.
        """
        return self.plan_value - self.monthly_discount + self.recurring_value

    @property
    def plan_value(self):
        """The plan's monthly price excl. VAT, or the explicit override."""
        if self.estimated_monthly_value is not None:
            return self.estimated_monthly_value
        if self.interested_tariff_id and self.interested_tariff:
            return self.interested_tariff.price
        return Decimal("0.00")

    @property
    def monthly_discount(self):
        """Rand amount off the plan per month (excl. VAT), never more than it."""
        return _discount_amount((self.quote_discount or {}).get("monthly"), self.plan_value)

    @property
    def once_off_discount(self):
        gross = Decimal("0.00")
        for item in self.quote_items or []:
            try:
                gross += Decimal(str(item.get("amount") or "0"))
            except Exception:  # noqa: BLE001
                continue
        return _discount_amount((self.quote_discount or {}).get("once_off"), gross)

    @property
    def recurring_value(self):
        """The quoted recurring charges (IP rental...), monthly excl. VAT."""
        total = Decimal("0.00")
        for item in self.quote_recurring or []:
            try:
                total += Decimal(str(item.get("amount") or "0"))
            except Exception:  # noqa: BLE001 -- a bad row counts as nothing
                continue
        return total

    @property
    def is_open(self):
        return self.status in self.OPEN_STATUSES

    @property
    def follow_up_is_due(self):
        """Due today or overdue, and still worth chasing.

        Overdue counts as due rather than as its own state. A rep working
        a list needs one list; splitting "today" from "late" produces two
        screens where the late one is the one that stops being opened.
        """
        if not self.next_follow_up or not self.is_open:
            return False
        return self.next_follow_up <= timezone.localdate()

    # --- rules -----------------------------------------------------------

    def clean(self):
        # A lost lead with no reason is precisely the record you most want
        # later -- it is the difference between "our pricing is wrong" and
        # "we need a tower in Bela-Bela". Enforced at the model, not just
        # in the form, so an import or a shell edit can't sidestep it.
        if self.status == self.Status.LOST and not self.lost_reason:
            raise ValidationError({"lost_reason": "Say why it was lost — this is the part worth knowing later."})
        if self.status != self.Status.LOST and self.lost_reason:
            raise ValidationError({"lost_reason": "Only a lost lead has a lost reason."})

    def save(self, *args, **kwargs):
        # Stamp/clear the closing time from the status itself rather than
        # trusting every caller to remember. Reopening a lead that was
        # closed by mistake has to clear it, or "how long do deals take"
        # silently measures the wrong thing.
        if self.status in self.CLOSED_STATUSES and self.closed_at is None:
            self.closed_at = timezone.now()
        elif self.status not in self.CLOSED_STATUSES:
            self.closed_at = None
        # Which stage it was lost at -- read from the row as it stands
        # before this save. Cleared when a lost lead is reopened.
        if self.status == self.Status.LOST and not self.lost_at_stage:
            before = (
                Lead.objects.filter(pk=self.pk).values_list("status", flat=True).first() if self.pk else None
            )
            self.lost_at_stage = before if before and before != self.Status.LOST else self.Status.NEW
        elif self.status != self.Status.LOST:
            self.lost_at_stage = ""
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = list(set(kwargs["update_fields"]) | {"closed_at", "lost_at_stage"})
        super().save(*args, **kwargs)

    def convert_to_customer(self, actor=None):
        """Create the Customer this lead became, once.

        Idempotent by design: a second call returns the customer already
        linked rather than making another one. Two people clicking
        Convert within a few seconds of each other is an ordinary Tuesday,
        and a duplicate customer is expensive to unpick afterwards.

        The customer is created as NEW, not ACTIVE. Nothing has been
        installed yet -- marking them Active here would put somebody in
        the connected-customer count who has no service and no router.
        """
        from customers.models import Customer

        if self.customer_id:
            return self.customer

        customer = Customer.objects.create(
            full_name=self.full_name,
            company_name=self.company_name,
            customer_type=(
                Customer.CustomerType.COMPANY if self.company_name else Customer.CustomerType.INDIVIDUAL
            ),
            category=(
                Customer.Category.BUSINESS if self.company_name else Customer.Category.RESIDENTIAL
            ),
            email=self.email,
            phone=self.phone,
            address=self.address,
            city=self.city,
            zip_code=self.zip_code,
            latitude=self.latitude,
            longitude=self.longitude,
            # Fibre confirmed on a network: the client belongs to that
            # partner -- its plans are what the quote offered.
            partner=(
                self.coverage_partner
                if self.coverage_result == self.CoverageResult.FIBRE_CONFIRMED and self.coverage_partner_id
                else self.partner
            ),
            assigned_staff=self.assigned_to,
            status=Customer.Status.NEW,
            notes=self.notes,
        )
        self.customer = customer
        self.status = self.Status.WON
        self.lost_reason = ""
        self.next_follow_up = None
        self.save()
        LeadNote.objects.create(
            lead=self,
            author=actor,
            body=f"Converted to customer {customer.customer_id} — {customer.full_name}.",
            kind=LeadNote.Kind.SYSTEM,
        )
        return customer


class LeadNote(models.Model):
    """One entry on a lead's timeline.

    Separate rows rather than appending to a text field, because the two
    questions people ask are "what was said" and "when, by whom" -- and a
    growing blob of text answers neither once more than one person has
    touched it.
    """

    class Kind(models.TextChoices):
        NOTE = "note", "Note"
        CALL = "call", "Call"
        EMAIL = "email", "Email"
        MEETING = "meeting", "Meeting"
        # Written by the platform, not a person. Rendered differently so
        # nobody reads an automatic line as something a colleague typed.
        SYSTEM = "system", "System"

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="lead_notes")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="lead_notes"
    )
    # Snapshot, same reasoning as the audit trail: a note whose author
    # reads "(deleted)" is worth less than one that still says who wrote it.
    author_label = models.CharField(max_length=255, blank=True)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.NOTE)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.author_label and self.author_id and self.author:
            full = f"{self.author.first_name} {self.author.last_name}".strip()
            self.author_label = full or self.author.username
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_kind_display()} on {self.lead}"
