from django.conf import settings
from django.db import models


class EmailTemplate(models.Model):
    """One of a fixed set of email templates staff can customize. Rows are
    seeded by a data migration — this ViewSet only supports list/retrieve/
    update (no create/delete) since the template "slots" map directly to
    concrete sending logic in notifications/services.py.

    Subject and body are rendered with Django's own template engine, so
    staff can use {{ placeholder }} syntax (and even {% if %} blocks) — see
    notifications/services.py's build_context() for what's available per
    template key.
    """

    class Key(models.TextChoices):
        WELCOME = "welcome", "Welcome message"
        QUOTE = "quote", "Quote"
        PROFORMA = "proforma", "Pro forma invoice"
        STATEMENT = "statement", "Statement"
        INVOICE = "invoice", "Invoice"
        PAYMENT_REMINDER = "payment_reminder", "Payment reminder"
        SUSPENSION = "suspension", "Suspension notification"
        PAYMENT_RECEIVED = "payment_received", "Payment received"
        # Goes to a STAFF member, not a customer -- the only one that does.
        # See EmailLog.staff below, and payroll's email-payslip action.
        PAYSLIP = "payslip", "Payslip"

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
    # A payslip is the one thing this platform emails to a member of staff
    # rather than a customer, and it is exactly the kind of send you may need
    # to prove you made -- so it gets a log row like everything else, hung off
    # the recipient. Exactly one of customer/staff is set on any given row.
    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="payslip_email_logs",
        null=True, blank=True,
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


class EmailSettings(models.Model):
    """Singleton row (always pk=1) holding the platform's outgoing-email
    (SMTP) configuration -- editable by an admin from Configs -> Email
    Settings, so changing mail providers or credentials doesn't require
    SSHing into the server and editing .env.

    Every field here is optional and falls back to the corresponding
    .env-driven Django setting when left blank/unset (see
    notifications.email_settings.get_email_config()) -- a fresh install
    with this table empty behaves exactly as it did before this model
    existed. Setting just smtp_host from the UI is enough to take over;
    every other field can be filled in or left to fall back individually.
    """

    smtp_host = models.CharField(
        max_length=255, blank=True, help_text="e.g. smtp.office365.com. Leave blank to use the server's default."
    )
    smtp_port = models.PositiveIntegerField(null=True, blank=True, help_text="Leave blank to use the server's default.")
    smtp_username = models.CharField(max_length=255, blank=True)
    # Stored as plain text (like the server's own .env file) rather than
    # hashed, since the raw value must be sent to the SMTP server to
    # authenticate -- this is standard for SMTP credentials (Django's own
    # EMAIL_HOST_PASSWORD setting works the same way). Never serialized
    # back out over the API -- see EmailSettingsSerializer.
    smtp_password = models.CharField(max_length=255, blank=True)
    use_tls = models.BooleanField(null=True, blank=True, help_text="Leave unset to use the server's default.")
    use_ssl = models.BooleanField(null=True, blank=True, help_text="Leave unset to use the server's default.")
    default_from_email = models.CharField(
        max_length=255, blank=True, help_text='"From" header for outgoing mail, e.g. "Skybre <no-reply@skybre.co.za>".'
    )
    company_name = models.CharField(max_length=100, blank=True, help_text="Used in email subjects/bodies and generated PDFs.")
    site_url = models.CharField(
        max_length=255, blank=True, help_text="Base URL used to build links inside emails, e.g. https://skybre.co.za"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Email settings"
        verbose_name_plural = "Email settings"

    def __str__(self):
        return "Email settings"

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
