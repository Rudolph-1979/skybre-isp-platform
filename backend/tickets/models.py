from django.conf import settings
from django.db import models


class Ticket(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        PENDING = "pending", "Pending Customer"
        RESOLVED = "resolved", "Resolved"
        CLOSED = "closed", "Closed"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    class Department(models.TextChoices):
        SUPPORT = "support", "Technical Support"
        BILLING = "billing", "Billing"
        SALES = "sales", "Sales"
        ABUSE = "abuse", "Abuse/NOC"

    ticket_number = models.CharField(max_length=20, unique=True, editable=False)
    customer = models.ForeignKey("customers.Customer", on_delete=models.CASCADE, related_name="tickets")
    subject = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    department = models.CharField(max_length=20, choices=Department.choices, default=Department.SUPPORT)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    priority = models.CharField(max_length=20, choices=Priority.choices, default=Priority.MEDIUM)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_tickets",
        limit_choices_to={"role__in": ["admin", "staff", "technician"]},
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.ticket_number:
            last = Ticket.objects.order_by("-id").first()
            next_num = (last.id + 1) if last else 1
            self.ticket_number = f"TCK-{next_num:06d}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.ticket_number}: {self.subject}"


class TicketComment(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="ticket_comments")
    message = models.TextField()
    is_internal = models.BooleanField(default=False, help_text="Internal staff note, hidden from customer portal.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Comment on {self.ticket}"
