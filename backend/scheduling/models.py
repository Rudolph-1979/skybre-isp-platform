from django.conf import settings
from django.db import models


class Job(models.Model):
    """A scheduled piece of work — a field job (install, repair, site
    visit) usually tied to a customer, or a standalone task (office work,
    internal errand) with no customer at all."""

    class JobType(models.TextChoices):
        INSTALLATION = "installation", "Installation"
        REPAIR = "repair", "Repair"
        MAINTENANCE = "maintenance", "Maintenance"
        SITE_VISIT = "site_visit", "Site Visit"
        OFFICE_TASK = "office_task", "Office Task"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        IN_PROGRESS = "in_progress", "In Progress"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    customer = models.ForeignKey(
        "customers.Customer",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="jobs",
        help_text="Leave blank for a standalone job with no linked customer.",
    )
    ticket = models.ForeignKey(
        "tickets.Ticket", on_delete=models.SET_NULL, null=True, blank=True, related_name="jobs"
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scheduled_jobs",
        limit_choices_to={"role__in": ["admin", "support", "sales", "technician", "management", "accounts"]},
    )
    job_type = models.CharField(max_length=20, choices=JobType.choices, default=JobType.SITE_VISIT)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SCHEDULED)
    start = models.DateTimeField()
    end = models.DateTimeField()
    location = models.CharField(
        max_length=255, blank=True, help_text="Defaults to the customer's address if left blank."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["start"]

    def __str__(self):
        return f"{self.title} — {self.customer or 'standalone'}"


class Shift(models.Model):
    """A planned block of time a staff member (field or office) is
    scheduled to work — not tied to a specific customer job."""

    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        CONFIRMED = "confirmed", "Confirmed"
        CANCELLED = "cancelled", "Cancelled"

    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="shifts",
        limit_choices_to={"role__in": ["admin", "support", "sales", "technician", "management", "accounts"]},
    )
    start = models.DateTimeField()
    end = models.DateTimeField()
    role_note = models.CharField(
        max_length=255, blank=True, help_text="e.g. 'Office — reception', 'Field standby'"
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNED)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["start"]

    def __str__(self):
        return f"{self.staff} shift {self.start:%Y-%m-%d %H:%M}"
