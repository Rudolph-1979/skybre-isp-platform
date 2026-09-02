from django.conf import settings
from django.contrib.auth.hashers import make_password, check_password
from django.contrib.auth.models import AbstractUser
from django.contrib.postgres.fields import ArrayField
from django.db import models


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

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.CUSTOMER)
    phone = models.CharField(max_length=32, blank=True)

    # Per-user section access list. Empty (the default) means unrestricted
    # -- every existing account keeps seeing everything it already could
    # until an admin deliberately narrows it down. Admin accounts always
    # have full access regardless of this field's contents (see
    # accounts.permissions.user_can_access_section) -- this is a way to
    # *restrict* non-admin staff, not a way to lock out Admin.
    allowed_sections = ArrayField(
        models.CharField(max_length=20, choices=Section.choices),
        blank=True,
        default=list,
        help_text="Sections this staff member can see/use. Empty = unrestricted (sees everything). Ignored for Admin.",
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
