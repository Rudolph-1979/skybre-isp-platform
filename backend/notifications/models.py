from django.conf import settings
from django.db import models


class EmailTemplate(models.Model):
    """One of a fixed set of email templates staff can customize. Rows are
    seeded by a data migration — this ViewSet only supports list/retrieve/
    update (no create/delete) since the 5 template "slots" map directly to
    concrete sending logic in notifications/services.py.

    Subject and body are rendered with Django's own template engine, so
    staff can use {{ placeholder }} syntax (and even {% if %} blocks) — see
    notifications/services.py's build_context() for what's available per
    template key.
    """

    class Key(models.TextChoices):
        WELCOME = "welcome", "Welcome message"
        STATEMENT = "statement", "Statement"
        INVOICE = "invoice", "Invoice"
        PAYMENT_REMINDER = "payment_reminder", "Payment reminder"
        SUSPENSION = "suspension", "Suspension notification"

    key = models.CharField(max_length=30, choices=Key.choices, unique=True)
    name = models.CharField(max_length=100)
    subject = models.CharField(max_length=255)
    body_html = models.TextField(
        help_text="HTML email body. Supports Django template syntax, e.g. {{ customer_name }}."
    )
    has_attachment = models.BooleanField(
        default=False, editable=False, help_text="Whether this template type sends a generated PDF attachment."
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]

    def __str__(self):
        return self.name


class EmailLog(models.Model):
    class Status(models.TextChoices):
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    customer = models.ForeignKey(
        "customers.Customer", on_delete=models.CASCADE, related_name="email_logs", null=True, blank=True
    )
    template_key = models.CharField(max_length=30, choices=EmailTemplate.Key.choices)
    recipient_email = models.EmailField()
    subject = models.CharField(max_length=255)
    status = models.CharField(max_length=10, choices=Status.choices)
    error_message = models.TextField(blank=True)
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="emails_sent"
    )
    # A single bulk send shares one batch_id so the frontend/API can group
    # and report on "sent to 42 customers just now" as one unit.
    batch_id = models.CharField(max_length=40, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.template_key} -> {self.recipient_email} ({self.status})"
