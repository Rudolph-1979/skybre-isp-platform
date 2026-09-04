from django.conf import settings
from django.db import IntegrityError, models, transaction
from django.db.models import BigIntegerField
from django.db.models.functions import Cast, Substr


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
        limit_choices_to={"role__in": ["admin", "support", "sales", "technician", "management", "accounts"]},
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    _NUMBER_PREFIX = "TCK"

    def _next_ticket_number(self):
        """One past the highest TCK number ever issued.

        Was `(Ticket.objects.order_by("-id").first().id + 1)` on a unique
        column, which had two faults.

        It reused numbers. The maximum came off an existing ROW, so
        deleting the newest ticket handed its number straight to the next
        one created -- and a customer quoting the reference they were given
        then reached a stranger's ticket and saw somebody else's comment
        thread. billing.IssuedNumberHighWater was created for exactly this
        on invoices, and its docstring generalises it: "the fact being
        recorded is about the sequence... it has to outlive every document
        in it". Tickets were never brought onto it, so they are now.

        And it had no retry. Two simultaneous creates computed the same
        number and the loser got an unhandled IntegrityError -- a 500 on a
        save that was perfectly valid. Both Customer.save() and
        Invoice.save() carry a five-attempt loop for this identical race.
        """
        from billing.models import IssuedNumberHighWater
        from django.db.models import Max

        prefix = self._NUMBER_PREFIX
        highest = (
            Ticket.objects.filter(ticket_number__regex=rf"^{prefix}-\d{{1,9}}$")
            .exclude(pk=self.pk)
            .annotate(_seq=Cast(Substr("ticket_number", len(prefix) + 2), BigIntegerField()))
            .aggregate(highest=Max("_seq"))["highest"]
        )
        watermark = (
            IssuedNumberHighWater.objects.filter(prefix=prefix)
            .values_list("last_seq", flat=True)
            .first()
        )
        return f"{prefix}-{max(highest or 0, watermark or 0) + 1:06d}"

    def _record_issued_number(self):
        """Remember this number so it is never handed out again. After a
        successful save, so a failed create leaves no gap."""
        from billing.models import IssuedNumberHighWater
        from django.db.models import Value
        from django.db.models.functions import Greatest

        prefix, _, seq = (self.ticket_number or "").rpartition("-")
        if prefix != self._NUMBER_PREFIX or not seq.isdigit():
            return
        updated = IssuedNumberHighWater.objects.filter(prefix=prefix).update(
            last_seq=Greatest("last_seq", Value(int(seq)))
        )
        if not updated:
            # Its own atomic block: a savepoint, so losing this race cannot
            # poison the transaction the caller is holding.
            try:
                with transaction.atomic():
                    IssuedNumberHighWater.objects.create(prefix=prefix, last_seq=int(seq))
            except IntegrityError:
                IssuedNumberHighWater.objects.filter(prefix=prefix).update(
                    last_seq=Greatest("last_seq", Value(int(seq)))
                )

    def save(self, *args, **kwargs):
        if self.ticket_number:
            return super().save(*args, **kwargs)

        last_error = None
        for _ in range(5):
            self.ticket_number = self._next_ticket_number()
            try:
                with transaction.atomic():
                    result = super().save(*args, **kwargs)
                    self._record_issued_number()
                    return result
            except IntegrityError as exc:
                if "ticket_number" not in str(exc):
                    raise
                last_error = exc
                self.ticket_number = ""
        raise last_error

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
