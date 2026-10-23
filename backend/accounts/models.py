from django.conf import settings
from django.contrib.auth.hashers import make_password, check_password
from django.contrib.auth.models import AbstractUser
from django.contrib.postgres.fields import ArrayField
from django.db import models


#: Automatic calendar colours: mid-tones that carry white text in light and
#: dark mode, distinct from one another. A staff member gets one by id until
#: someone picks their own (User.calendar_colour).
CALENDAR_PALETTE = [
    "#2a6fd0", "#1a9e6e", "#7652d6", "#0e8d96", "#d0661f", "#c03c78",
    "#7d8a12", "#b8452f", "#4f8a2a", "#9a4fb8", "#2f8fc4", "#a87a12",
]


class User(AbstractUser):
    """Unified user model. Staff (admin/support/sales/technician/
    management/accounts) and customers both authenticate through this
    model, distinguished by `role`.
    """

    class Role(models.TextChoices):
        ADMIN = "admin", "Administrator"
        SUPPORT = "support", "Support"
        SALES = "sales", "Sales"
        TECHNICIAN = "technician", "Technician"
        MANAGEMENT = "management", "Management"
        ACCOUNTS = "accounts", "Accounts"
        CUSTOMER = "customer", "Customer"

    # One entry per sidebar tab a staff member can be individually granted
    # or denied access to -- kept in sync with the frontend's NAV entries
    # in components/AdminLayout.tsx and the section keys used by
    # `accounts.permissions.section_permission()`.
    class Section(models.TextChoices):
        SCHEDULING = "scheduling", "Scheduling"
        # Leads and the sales pipeline. Its own section rather than a
        # corner of CUSTOMERS: a rep working enquiries has no reason to
        # see every existing customer's billing, and the support desk has
        # no reason to see the pipeline. Every account with an empty
        # allowed_sections (the default) gets it automatically -- only
        # deliberately-restricted staff need it added by hand.
        SALES = "sales", "Sales / Leads"
        CUSTOMERS = "customers", "Customers"
        SERVICES = "services", "Services"
        FINANCE = "finance", "Finance"
        INVENTORY = "inventory", "Stock / Inventory"
        NETWORKING = "networking", "Networking"
        TICKETS = "tickets", "Support Tickets"
        # STAFF removed 2026-08-19 -- the Staff page (Attendance/Leave/
        # Employees/Payroll) was folded into ACCOUNTANT below, alongside
        # Users and Partners which had already moved to Configs the same
        # day. Any existing allowed_sections entry containing the old
        # "staff" value is just inert now (not a valid choice, but ArrayField
        # doesn't enforce choices at the DB level, so it won't break
        # anything -- it simply stops granting access to anything).
        VEHICLES = "vehicles", "Vehicles"
        BULK_EMAIL = "bulk_email", "Bulk Email"
        CONFIGS = "configs", "Configs"
        ACCOUNTANT = "accountant", "Accountant"
        # Administration -> Reports (Rudolph, 9 Oct 2026). Granted to nobody
        # by default; Admin always has it.
        ADMINISTRATION = "administration", "Administration"

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.CUSTOMER)
    phone = models.CharField(max_length=32, blank=True)

    # Rudolph, 26 Sep, on the ticket escalation workflow: NOC is not a
    # separate roster of accounts, it's a capability certain staff carry
    # ("overlapping / same staff, different capability") -- typically a
    # senior Support account, but not exclusively, and not tied to `role`
    # the way is_staff_member is. A plain boolean rather than a new Role
    # value or a Section, because it isn't a sidebar tab to show or hide
    # (see Section above) and it isn't an account TYPE the way role is --
    # it's permission to perform one specific action (deciding a ticket
    # escalation, tickets.permissions.IsNOC) on top of whatever role and
    # section access the account already has.
    is_noc = models.BooleanField(
        default=False,
        help_text="Can accept or reject a ticket a support member escalates to NOC.",
    )

    # This person's colour on the Scheduling calendar (Rudolph, 6 Oct 2026:
    # colours belong to staff, not job types; "staff colours should be
    # editable"). Blank = an automatic colour from CALENDAR_PALETTE, so
    # everybody has one from day one.
    calendar_colour = models.CharField(
        max_length=7, blank=True, help_text="Calendar colour, #rrggbb. Blank = automatic.",
    )

    @property
    def display_colour(self):
        return self.calendar_colour or CALENDAR_PALETTE[(self.pk or 0) % len(CALENDAR_PALETTE)]

    # Per-user section access list. Empty means NO sections -- a new
    # account starts with nothing and is granted what it needs. It used to
    # mean unrestricted, which made the whole mechanism opt-in and left
    # every unconfigured account able to open payroll and the credential
    # store; the backfill migration gave the accounts that existed at the
    # time an explicit list for their role, so flipping it took nobody's
    # access away.
    #
    # Admin accounts always have full access regardless of this field's
    # contents (see accounts.permissions.user_can_access_section) -- this
    # is a way to *restrict* non-admin staff, not a way to lock out Admin.
    allowed_sections = ArrayField(
        models.CharField(max_length=20, choices=Section.choices),
        blank=True,
        default=list,
        help_text="Sections this staff member can see/use. Empty = no access. Ignored for Admin, who always sees everything.",
    )

    # Reseller-partner visibility restriction (mirrors allowed_sections
    # above): empty means unrestricted, i.e. this staff member sees every
    # partner's customers (plus direct, no-partner customers). Only
    # Management/Admin can set this, via Staff -> Partners -> Staff access
    # (accounts.serializers.StaffPermissionsSerializer). Stores
    # customers.Partner ids rather than a ManyToManyField so it can reuse
    # the exact same empty-means-everything convention as allowed_sections.
    allowed_partners = ArrayField(
        models.IntegerField(),
        blank=True,
        default=list,
        help_text="Partner ids this staff member can see customers for. Empty = unrestricted (sees all partners).",
    )

    # A staff member's own personal narrowing of the above -- which of
    # their *allowed* partners they currently want shown by default on the
    # Customers page. Empty = show all of whichever partners they're
    # allowed to see. Self-service (see accounts.views.MeView.patch),
    # unlike allowed_partners which only Management/Admin can set.
    visible_partners = ArrayField(
        models.IntegerField(),
        blank=True,
        default=list,
        help_text="Subset of allowed_partners this staff member currently wants shown by default. Empty = show all allowed.",
    )

    # Dashboard tile order, per person (Rudolph, 15 Oct 2026: "Can we make
    # the tile movable and remembered for each users profile?"). Self-service
    # through PATCH /me/ like visible_partners. {"order": ["customers_total",
    # ...]}; empty = the built-in order. Tiles a person can't see are simply
    # skipped, and a tile added later that isn't in their list slots in next
    # to its built-in neighbour.
    dashboard_layout = models.JSONField(
        blank=True,
        default=dict,
        help_text='This person\'s own dashboard tile order: {"order": [tile keys]}. Empty = default order.',
    )

    # Which foldable panels this person keeps open, per page (#193,
    # Rudolph 16 Oct 2026: "Make the panels foldable"). Self-service through
    # PATCH /me/: {"vehicle": ["odometer", "fuel"]}. A page that isn't here
    # uses its built-in default.
    panel_state = models.JSONField(
        blank=True,
        default=dict,
        help_text='Open panels per page: {"vehicle": [panel keys]}. A page not listed = its default.',
    )

    # A stable identifier for a staff member, the way customer_id is one
    # for a customer. Conecto gates staff registration on it: a person can
    # only create an account in the app if we have told Conecto their code
    # and what role it carries.
    #
    # Null rather than blank for the ones that have none. This is unique,
    # and Postgres allows many NULLs in a unique column but only one empty
    # string -- so blank="" would mean the second customer ever created
    # collides with the first.
    #
    # Derived from the primary key rather than allocated sequentially.
    # Sequential allocation means reading the highest code and adding one,
    # which two admins creating staff at the same moment can both do and
    # both get the same answer; the primary key is already unique and
    # already exists. The cost is gaps -- staff share the user table with
    # customers, so the numbers jump -- which is invisible in a code and
    # cheaper than a retry loop around a race nobody would reproduce.
    staff_code = models.CharField(
        max_length=20, unique=True, null=True, blank=True,
        help_text="Identifier shared with the Conecto app. Assigned automatically to staff accounts.",
    )

    def save(self, *args, **kwargs):
        """Assign a staff code on the way in, once.

        After the first save rather than before it, because the code is
        built from the primary key and there is no key until the row
        exists. That costs one extra UPDATE on the save that creates a
        staff account, and nothing on any save after it.

        Customers are skipped: they already have customer_id, and a
        customer has no business registering in a staff app.
        """
        super().save(*args, **kwargs)
        if self.is_staff_member and not self.staff_code:
            self.staff_code = f"STF-{self.pk:06d}"
            # `using` carried through: create_user() passes it, and a
            # second save that quietly ignored it would write the code to a
            # different database than the row it belongs to.
            super().save(update_fields=["staff_code"], using=kwargs.get("using"))

    @property
    def is_staff_member(self):
        # Every role except Customer is an internal/staff account -- this
        # way a newly-added role (like Management or Accounts) is staff
        # by default rather than needing to be added to a hardcoded list.
        return self.role != self.Role.CUSTOMER

    def __str__(self):
        return f"{self.username} ({self.role})"


# Every role except Customer is an internal/staff account. Computed from
# Role.choices (rather than hand-listed) so a newly added role is staff by
# default -- this is the single source of truth shared by StaffListView,
# StaffPermissionsViewSet, and StaffAccountsViewSet in views.py, and by the
# serializers that validate a `role` value against it.
STAFF_ROLES = [r for r in User.Role.values if r != User.Role.CUSTOMER]


class TwoFactorAuth(models.Model):
    """One optional TOTP device per user. `confirmed=False` means the user
    started setup (scanned a QR) but never verified a code yet — login is
    only gated once confirmed, so a half-finished setup can't lock anyone
    out. Any user (staff or customer) can opt in via their own account."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="two_factor")
    secret = models.CharField(max_length=32)
    confirmed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"2FA for {self.user.username} ({'confirmed' if self.confirmed else 'pending'})"


class TwoFactorBackupCode(models.Model):
    """One-time recovery codes, shown to the user once at setup, stored
    only as a hash — same treatment as a password, since possessing one
    is equivalent to bypassing the TOTP check."""

    device = models.ForeignKey(TwoFactorAuth, on_delete=models.CASCADE, related_name="backup_codes")
    code_hash = models.CharField(max_length=128)
    used = models.BooleanField(default=False)
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def set_code(self, raw_code):
        self.code_hash = make_password(raw_code)

    def check_code(self, raw_code):
        return not self.used and check_password(raw_code, self.code_hash)
