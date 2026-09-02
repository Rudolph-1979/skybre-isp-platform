import datetime
import uuid

from django.conf import settings
from django.db import IntegrityError, models, transaction
from django.utils import timezone


class Partner(models.Model):
    """A reseller who sells our services under their own customer base.
    Customers can be tagged to a Partner (see Customer.partner below) for
    reporting/commission purposes. Which staff can see which partners'
    customers is a separate, per-staff restriction -- see
    accounts.models.User.allowed_partners / visible_partners."""

    name = models.CharField(max_length=255, unique=True)
    contact_person = models.CharField(max_length=255, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    commission_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Optional commission percentage owed to this partner, e.g. 10.00 for 10%.",
    )
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Customer(models.Model):
    class CustomerType(models.TextChoices):
        INDIVIDUAL = "individual", "Individual"
        COMPANY = "company", "Company"

    class Status(models.TextChoices):
        NEW = "new", "New"
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"
        # Written off: they owe money we have stopped expecting to collect.
        # Distinct from Suspended (temporary, they can pay and come back) and
        # from Inactive (they left, with nothing outstanding). Kept apart
        # because those three lead to completely different conversations.
        BAD_DEBT = "bad_debt", "Bad Debt"
        INACTIVE = "inactive", "Inactive"

    # Statuses that mean the customer should NOT be connected. Named once, so
    # nothing has to remember to add a new one to four separate checks.
    OFF_STATUSES = ("suspended", "bad_debt", "inactive")

    class Category(models.TextChoices):
        RESIDENTIAL = "residential", "Residential"
        BUSINESS = "business", "Business"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="customer_profile",
        help_text="Portal login account for this customer, if enabled.",
    )
    # The customer's payment reference. Printed on their statement and
    # invoices, and it is what bankfeeds.matching.match_customer_by_reference
    # looks for in a bank transaction's description.
    #
    # Editable (it used to be editable=False) specifically so a customer
    # migrated in from another system can KEEP the reference they already use
    # on their EFTs. Auto-generating one they'd never type meant their
    # payments silently never matched.
    #
    # Leave it blank and save() generates the next CUS-###### for you.
    customer_id = models.CharField(
        max_length=20,
        unique=True,
        blank=True,
        verbose_name="Payment reference",
        help_text=(
            "What this customer types as the reference on their EFT/bank transfer. "
            "Leave blank to generate the next CUS-###### automatically."
        ),
    )
    # Unguessable key for the no-login "check your usage" page we send
    # customers. Treated as a bearer credential: anyone holding the link
    # sees this customer's usage, so the page it opens deliberately exposes
    # usage only -- no address, no invoices, no contact details.
    #
    # A random UUID rather than a signed token so it can be REVOKED: if a
    # customer forwards the link or it ends up somewhere public, staff
    # regenerate this and the old link is dead immediately. A signature
    # would be unrevocable without also storing something like this.
    usage_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    customer_type = models.CharField(max_length=20, choices=CustomerType.choices, default=CustomerType.INDIVIDUAL)
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.RESIDENTIAL)
    full_name = models.CharField(max_length=255)
    company_name = models.CharField(max_length=255, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    address = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    zip_code = models.CharField(max_length=20, blank=True)
    # Printed on the customer's side of a tax invoice. Both are optional --
    # an individual residential customer usually has neither, which is why
    # the invoice prints the labels with nothing after them rather than
    # hiding the rows.
    id_number = models.CharField(
        max_length=32, blank=True,
        help_text="ID number, or company registration number for a business.",
    )
    vat_number = models.CharField(
        max_length=32, blank=True, help_text="The customer's own VAT registration number, if they have one.",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    assigned_staff = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_customers",
        limit_choices_to={"role__in": ["admin", "support", "sales", "technician", "management", "accounts"]},
    )
    partner = models.ForeignKey(
        Partner,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="customers",
        help_text="Reseller partner this customer belongs to, if any. Blank = a direct (non-partner) customer.",
    )
    notes = models.TextField(blank=True)
    # The date this customer actually became a customer, as opposed to the
    # date their row was created in THIS system. Those differ for anyone
    # migrated from another platform: created_at is auto_now_add, so a bulk
    # import stamps every migrated customer with the import date, which on
    # a growth chart reads as one huge spike in the import month and no
    # history at all before it. Populate this from the legacy export's own
    # signup/date-added column (the CSV importer accepts it -- see
    # CustomerViewSet.import_fields) and reporting prefers it over
    # created_at. Blank is normal for customers signed up directly here,
    # where created_at already IS the signup date.
    signup_date = models.DateField(
        null=True, blank=True,
        help_text="Original signup date, for customers migrated from another platform. Leave blank for customers created here — their created date is used instead.",
    )
    # Whether this customer may see their own LIVE speed -- through the
    # signed-in portal or through their no-login usage link. One switch for
    # both doors: signing in is a stronger position than holding a link, but
    # the cost to the router is identical either way, and a customer having
    # live figures through one door and not the other would be impossible to
    # explain to the person answering the phone.
    #
    # Off by default and staff-controlled, because that graph is the one thing
    # on the page that costs something: it holds a connection open to the
    # router for as long as it is being watched. The usage link needs no login
    # -- the token in the URL is the whole credential -- so with this on by
    # default, anyone holding a forwarded link could keep a router connection
    # open indefinitely by leaving a tab open. Turned on deliberately, per
    # customer, it is a useful thing to give somebody who is debugging their
    # own line; turned on for everybody it is a way to keep every router busy.
    # It also turns itself off. See expire_live_bandwidth_if_idle below --
    # a switch that only a human can turn off is a switch that stays on.
    live_bandwidth_last_viewed_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Last time the customer actually loaded a live view. Set when staff enable it, "
                  "so the idle clock starts even if they never look.",
    )
    live_bandwidth_public = models.BooleanField(
        default=False,
        verbose_name="Let this customer see their live speed",
        help_text=(
            "Off by default. When on, this customer sees a live speed figure in the portal and on "
            "their usage link, which holds a connection to their router open while they watch."
        ),
    )
    # How long a customer's live view may sit unwatched before it switches
    # itself off. Short on purpose: this is normally turned on to help
    # somebody debug their line for a few minutes, and the failure mode of
    # leaving it on is a customer able to hold a router connection open
    # whenever they like, months after anyone remembers enabling it.
    LIVE_BANDWIDTH_IDLE_TIMEOUT = datetime.timedelta(minutes=5)

    def expire_live_bandwidth_if_idle(self, now=None):
        """Turn the customer's live view off if nobody has loaded it lately.

        Evaluated lazily, wherever the flag is read, rather than by a cron
        sweep -- so it is correct the moment anyone looks, needs nothing
        scheduled, and cannot be defeated by a scheduled job that stopped
        running. Returns True if it just turned it off.
        """
        if not self.live_bandwidth_public:
            return False
        now = now or timezone.now()
        last = self.live_bandwidth_last_viewed_at
        if last is None:
            # On, but the clock was never started -- an import, a fixture, or
            # a direct database edit. Start it here rather than expiring on
            # the spot: "no record of a view" is not the same as "five idle
            # minutes", and expiring instantly would make the switch look
            # broken to whoever just set it. It still expires five minutes
            # from now if nobody looks.
            self.touch_live_bandwidth_view(now)
            return False
        if now - last <= self.LIVE_BANDWIDTH_IDLE_TIMEOUT:
            return False
        # .update() rather than .save(): updated_at is auto_now, and an
        # automatic expiry is not an edit anybody made to this customer.
        Customer.objects.filter(pk=self.pk).update(live_bandwidth_public=False)
        self.live_bandwidth_public = False
        return True

    def touch_live_bandwidth_view(self, now=None):
        """Record that the customer just loaded a live view.

        Written at most every 30 seconds. The page polls once a SECOND, and a
        row write per second per customer -- to keep a five-minute timer
        roughly accurate -- is a poor trade.
        """
        now = now or timezone.now()
        last = self.live_bandwidth_last_viewed_at
        if last and (now - last).total_seconds() < 30:
            return
        Customer.objects.filter(pk=self.pk).update(live_bandwidth_last_viewed_at=now)
        self.live_bandwidth_last_viewed_at = now

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def effective_signup_date(self):
        """The date to report this customer's acquisition on: the real
        signup date where we know it, otherwise the date the row was
        created. The dashboard growth endpoint mirrors this in SQL (see
        accounts.views.CustomerGrowthView) -- keep the two in step."""
        return self.signup_date or self.created_at.date()

    # Prefix used by auto-generated references. A reference typed by hand is
    # kept exactly as entered and needn't use this at all.
    REFERENCE_PREFIX = "CUS-"

    def _next_generated_reference(self):
        """The next free CUS-###### .

        Derived from the highest existing CUS- reference rather than from the
        last row's primary key, which is what this used to do. That matters
        now that references can be typed by hand: someone entering
        "CUS-000009" manually would otherwise collide with the next
        pk-derived value and fail the unique constraint on save.
        """
        taken = set(
            Customer.objects.filter(customer_id__startswith=self.REFERENCE_PREFIX)
            .values_list("customer_id", flat=True)
        )
        highest = 0
        for reference in taken:
            suffix = reference[len(self.REFERENCE_PREFIX):]
            if suffix.isdigit():
                highest = max(highest, int(suffix))
        return f"{self.REFERENCE_PREFIX}{highest + 1:06d}"

    def save(self, *args, **kwargs):
        if self.customer_id:
            return super().save(*args, **kwargs)

        # Two simultaneous creates can compute the same next reference --
        # the unique constraint is the only thing that actually serialises
        # them, so retry rather than surfacing a 500. Each attempt is in its
        # own atomic block so a failed INSERT can be rolled back and retried;
        # anything that isn't a customer_id clash is re-raised untouched.
        last_error = None
        for _ in range(5):
            self.customer_id = self._next_generated_reference()
            try:
                with transaction.atomic():
                    return super().save(*args, **kwargs)
            except IntegrityError as exc:
                if "customer_id" not in str(exc):
                    raise
                last_error = exc
                self.customer_id = ""
        raise last_error

    def __str__(self):
        return f"{self.customer_id} - {self.full_name}"


class CustomerDeletionRequest(models.Model):
    """Deleting a Customer cascades away ALL of their history -- services
    (and with it, RADIUS logins and any assigned IPs), invoices, payments,
    credit requests, tickets, email logs -- so it isn't a plain staff
    action. Any staff member with Customers access can submit one; only
    Management (or Admin) can approve it, at which point the customer is
    actually deleted. Mirrors billing.CreditRequest's request/decide shape.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    # SET_NULL, not CASCADE: once approved, the customer itself is deleted,
    # but this request row survives as the audit trail of what happened and
    # why -- see customer_display_name/id below, snapshotted before that
    # happens since `customer` will no longer be there to read them from.
    customer = models.ForeignKey(
        Customer, on_delete=models.SET_NULL, null=True, blank=True, related_name="deletion_requests"
    )
    customer_display_name = models.CharField(max_length=255, editable=False, blank=True)
    customer_display_id = models.CharField(max_length=20, editable=False, blank=True)
    reason = models.TextField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    decision_note = models.CharField(max_length=255, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if self.customer and not self.customer_display_name:
            self.customer_display_name = self.customer.full_name
            self.customer_display_id = self.customer.customer_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Delete {self.customer_display_name or self.customer_display_id} ({self.status})"


class CustomerTask(models.Model):
    """A piece of follow-up work owed to one customer.

    Deliberately NOT scheduling.Job and NOT tickets.Ticket, both of which
    already exist and neither of which fits:

      * A Job is a calendar block -- `start` and `end` are both required,
        and it is what the day/technician schedule views read. Filing
        "phone them back about the debit order" as a Job means inventing a
        time window for it, and every one of those inventions shows up as
        a real appointment on somebody's day.

      * A Ticket is the customer's side of a conversation. It has a
        reference number we quote to them, a department, a comment thread
        the portal shows them (TicketComment.is_internal exists precisely
        because the rest is visible), and closing one is a statement to
        the customer that their issue is handled.

    A task is neither: it is internal, it is not shown to the customer at
    all, it has no reference number, and it is done when whoever owns it
    says so. Staff-only by permission, not just by convention -- see
    CustomerTaskViewSet.
    """

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In Progress"
        DONE = "done", "Done"
        CANCELLED = "cancelled", "Cancelled"

    # The statuses that still need somebody to do something. Named once so
    # a fifth status later can't leave three separate checks disagreeing
    # about what "outstanding" means -- same reason Customer.OFF_STATUSES
    # exists.
    OPEN_STATUSES = ("open", "in_progress")

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="tasks")
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    priority = models.CharField(max_length=20, choices=Priority.choices, default=Priority.MEDIUM)
    # A date, not a datetime: these are "by Friday" items, and asking for a
    # time of day on every one of them would get a made-up answer.
    # Optional, because plenty of follow-ups genuinely have no deadline and
    # forcing one produces a wall of fake due dates that then can't be
    # told apart from the real ones.
    due_date = models.DateField(
        null=True, blank=True, help_text="Optional. Leave blank for a task with no deadline."
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_customer_tasks",
        limit_choices_to={"role__in": ["admin", "support", "sales", "technician", "management", "accounts"]},
        help_text="Leave blank for a task the whole team can pick up.",
    )
    # SET_NULL rather than CASCADE: a staff member leaving must not delete
    # the tasks they raised, the same reasoning as
    # CustomerDeletionRequest.requested_by.
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    # Stamped by save() from `status`, never set by the API -- two fields
    # that can disagree about whether a task is finished is a bug waiting
    # to be written, so only one of them is writable.
    completed_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # The customer page's task list, and the only query this model
            # gets asked in bulk.
            models.Index(fields=["customer", "status"]),
        ]

    @property
    def is_outstanding(self):
        return self.status in self.OPEN_STATUSES

    @property
    def is_overdue(self):
        """Past its due date and still not done.

        A cancelled or completed task is never overdue -- the date passing
        after the work stopped mattering isn't a thing to chase.
        """
        if not self.due_date or not self.is_outstanding:
            return False
        return self.due_date < timezone.localdate()

    def save(self, *args, **kwargs):
        # Keep completed_at in step with status in both directions: moving
        # a task to Done stamps it, and moving it back out of Done clears
        # it. Without the clear, a task reopened after being closed by
        # mistake keeps a completion time that has already been read as
        # "this was finished on the 3rd".
        if self.status == self.Status.DONE:
            if self.completed_at is None:
                self.completed_at = timezone.now()
        elif self.completed_at is not None:
            self.completed_at = None
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.title} ({self.get_status_display()})"
