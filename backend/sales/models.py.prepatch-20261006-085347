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


class Lead(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "New"
        CONTACTED = "contacted", "Contacted"
        # Coverage confirmed and they can afford it -- the point where a
        # lead stops being a phone number and starts being a real
        # prospect worth a rep's time.
        QUALIFIED = "qualified", "Qualified"
        QUOTED = "quoted", "Quoted"
        WON = "won", "Won"
        LOST = "lost", "Lost"

    # Stages a lead is still moving through. Named once so the follow-up
    # queries and the pipeline totals can't drift apart.
    OPEN_STATUSES = (Status.NEW, Status.CONTACTED, Status.QUALIFIED, Status.QUOTED)
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
        if self.estimated_monthly_value is not None:
            return self.estimated_monthly_value
        if self.interested_tariff_id and self.interested_tariff:
            return self.interested_tariff.price
        return Decimal("0.00")

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
            partner=self.partner,
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
