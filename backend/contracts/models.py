"""Digital contract signing (Rudolph, 6 Oct 2026).

A client signs either our own template (filled in from their profile) or a
PDF uploaded for them. They sign from a link on their phone or in person on
a staff device. Each signed contract is kept on the client's profile with the
signed PDF and a record of who signed, when, from where, and the hash of the
exact document they signed.

The PDFs are stored IN THE DATABASE (BinaryField), not under MEDIA_ROOT: a
signed contract is a legal record, and the database is what gets backed up.
They are small (a template contract is ~50 KB; uploads are capped at 10 MB).
"""
import hashlib
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone


def _token():
    return secrets.token_urlsafe(32)


class ContractTemplate(models.Model):
    """The wording, with {{ placeholders }} filled from the client's profile.
    Editing a template never changes a contract already sent: each contract
    keeps its own copy of the filled-in wording and PDF."""

    name = models.CharField(max_length=120, unique=True)
    title = models.CharField(
        max_length=200, help_text="Heading on the contract, e.g. Internet Service Agreement."
    )
    body_html = models.TextField(help_text="The terms. HTML with {{ placeholders }}.")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ContractSettings(models.Model):
    """Singleton (pk=1), Configs -> Contracts."""

    default_template = models.ForeignKey(
        ContractTemplate, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    # Off until the real wording is in: an automatic send of an unfinished
    # template would reach real clients.
    auto_send_new_client = models.BooleanField(default=False)
    auto_send_lead_accepted = models.BooleanField(default=False)
    link_valid_days = models.PositiveSmallIntegerField(default=14)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Contract(models.Model):
    class Kind(models.TextChoices):
        TEMPLATE = "template", "From template"
        UPLOAD = "upload", "Uploaded PDF"

    class Status(models.TextChoices):
        DRAFT = "draft", "Not sent"
        SENT = "sent", "Awaiting signature"
        VIEWED = "viewed", "Opened"
        SIGNED = "signed", "Signed"
        CANCELLED = "cancelled", "Cancelled"

    OPEN_STATUSES = (Status.DRAFT, Status.SENT, Status.VIEWED)

    customer = models.ForeignKey("customers.Customer", on_delete=models.CASCADE, related_name="contracts")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    template = models.ForeignKey(
        ContractTemplate, on_delete=models.SET_NULL, null=True, blank=True, related_name="contracts"
    )
    lead = models.ForeignKey(
        "sales.Lead", on_delete=models.SET_NULL, null=True, blank=True, related_name="contracts"
    )
    title = models.CharField(max_length=200)
    # What the client reads and signs, frozen when the contract is made.
    body_html = models.TextField(blank=True, help_text="Filled-in wording (template contracts).")
    document_pdf = models.BinaryField()
    document_filename = models.CharField(max_length=200, blank=True)
    document_sha256 = models.CharField(max_length=64)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    token = models.CharField(max_length=64, unique=True, default=_token, editable=False)
    expires_at = models.DateTimeField(null=True, blank=True)
    sent_to = models.EmailField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    viewed_at = models.DateTimeField(null=True, blank=True)

    signed_at = models.DateTimeField(null=True, blank=True)
    signer_name = models.CharField(max_length=200, blank=True)
    signer_id_number = models.CharField(max_length=40, blank=True)
    signature_png = models.BinaryField(null=True, blank=True)
    signer_ip = models.GenericIPAddressField(null=True, blank=True)
    signer_user_agent = models.CharField(max_length=400, blank=True)
    signed_in_person = models.BooleanField(default=False)
    witnessed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    signed_pdf = models.BinaryField(null=True, blank=True)
    signed_sha256 = models.CharField(max_length=64, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["customer", "status"])]

    def __str__(self):
        return f"{self.reference} {self.title}"

    @property
    def reference(self):
        return f"CTR-{self.pk:05d}" if self.pk else "CTR-new"

    @property
    def is_open(self):
        return self.status in self.OPEN_STATUSES

    @property
    def is_expired(self):
        return self.is_open and self.expires_at is not None and self.expires_at < timezone.now()

    def set_document(self, pdf_bytes, filename=""):
        self.document_pdf = pdf_bytes
        self.document_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
        self.document_filename = filename or f"{self.title}.pdf"


class ContractEvent(models.Model):
    """The audit trail: every step, with who/where."""

    class Kind(models.TextChoices):
        CREATED = "created", "Created"
        SENT = "sent", "Sent"
        SEND_FAILED = "send_failed", "Email failed"
        VIEWED = "viewed", "Opened by client"
        DOCUMENT_OPENED = "document_opened", "PDF opened by client"
        IN_PERSON = "in_person", "Opened for signing in person"
        SIGNED = "signed", "Signed"
        CANCELLED = "cancelled", "Cancelled"

    contract = models.ForeignKey(Contract, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    at = models.DateTimeField(default=timezone.now)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=400, blank=True)
    detail = models.CharField(max_length=400, blank=True)

    class Meta:
        ordering = ["at", "pk"]
