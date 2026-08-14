from django.conf import settings
from django.contrib.auth.hashers import make_password, check_password
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Unified user model. Staff (admin/support/technician) and customers
    both authenticate through this model, distinguished by `role`.
    """

    class Role(models.TextChoices):
        ADMIN = "admin", "Administrator"
        STAFF = "staff", "Staff"
        TECHNICIAN = "technician", "Technician"
        CUSTOMER = "customer", "Customer"

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.CUSTOMER)
    phone = models.CharField(max_length=32, blank=True)

    @property
    def is_staff_member(self):
        return self.role in (self.Role.ADMIN, self.Role.STAFF, self.Role.TECHNICIAN)

    def __str__(self):
        return f"{self.username} ({self.role})"


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
