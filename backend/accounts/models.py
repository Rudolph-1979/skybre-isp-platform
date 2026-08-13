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
