"""What was done, by whom, from where.

Two questions this table exists to answer, which turn out to be the same
question asked from different ends:

  * "Who changed this customer's tariff, and what was it before?"
  * "What did this staff member do on Tuesday?"

Both are answered by one row per event, so they can never disagree. The
alternative -- a per-record history on each model plus a separate staff
activity feed -- gives you two tables that drift apart the first time
somebody forgets to write to one of them.
"""
from django.conf import settings
from django.db import models


class AuditEvent(models.Model):
    """One thing that happened, in a form that survives the thing it
    happened to.

    Nearly every field here is a SNAPSHOT rather than a live reference,
    and that is the whole design. An audit row whose meaning depends on
    the record it describes is worthless precisely when it matters most:
    delete the customer and a foreign key gives you "edited <deleted>",
    which is exactly the event somebody would be trying to look up. So
    the actor's name, the target's name and the values on both sides of
    the change are all copied in at write time and never chased
    afterwards.
    """

    class Action(models.TextChoices):
        CREATED = "created", "Created"
        UPDATED = "updated", "Updated"
        DELETED = "deleted", "Deleted"
        LOGIN = "login", "Signed in"
        LOGIN_FAILED = "login_failed", "Sign-in failed"
        LOGOUT = "logout", "Signed out"

    # SET_NULL, not CASCADE. Deleting a staff account must not delete the
    # record of what that account did -- that is the one deletion most
    # worth having a trail for. actor_label below carries the name on.
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    # Who it was, in words, at the time. Survives the account being
    # deleted, renamed, or handed to somebody else.
    actor_label = models.CharField(max_length=255, blank=True)

    action = models.CharField(max_length=20, choices=Action.choices, db_index=True)

    # "customers.Customer", "billing.Service" -- a string rather than a
    # ContentType FK so a removed app or model doesn't take its own
    # history with it, and so this table has no cross-app dependencies.
    target_type = models.CharField(max_length=100, blank=True, db_index=True)
    target_id = models.CharField(max_length=64, blank=True, db_index=True)
    # str() of the object at the time of the event.
    target_label = models.CharField(max_length=255, blank=True)

    # Denormalised so the per-customer History tab is one indexed lookup
    # instead of a scan across every row that might mention them. Set for
    # events on a Customer and on anything that hangs off one (their
    # services, invoices, payments, tickets). Null for events that aren't
    # about a customer at all -- sign-ins, tariff edits, staff accounts.
    customer = models.ForeignKey(
        "customers.Customer",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )

    # [{"field": "status", "label": "Status", "from": "Active",
    #   "to": "Suspended"}, ...]. Empty for logins and for creates, where
    # there is no "before" worth spelling out.
    changes = models.JSONField(default=list, blank=True)

    # Free text for the events a field diff can't express: why a sign-in
    # failed, which command applied a change, and so on.
    detail = models.CharField(max_length=500, blank=True)

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # The two questions in the module docstring, one index each.
            models.Index(fields=["target_type", "target_id", "-created_at"]),
            models.Index(fields=["actor", "-created_at"]),
            models.Index(fields=["customer", "-created_at"]),
            # accounts.login_guard counts recent failed sign-ins for one
            # attempted username on EVERY login -- staff and all ~1,600
            # portal subscribers -- so it runs on the hottest endpoint in
            # the platform, against a table that grows with every record
            # edit. `action` and `created_at` are indexed individually,
            # which leaves Postgres bitmap-ANDing two indexes and then
            # filtering actor_label (not indexed at all) by hand. This
            # covers the guard's exact predicate.
            models.Index(
                fields=["actor_label", "action", "-created_at"],
                name="audit_login_guard_idx",
            ),
        ]

    def __str__(self):
        who = self.actor_label or "system"
        if self.action in (self.Action.LOGIN, self.Action.LOGIN_FAILED, self.Action.LOGOUT):
            return f"{who}: {self.get_action_display()}"
        return f"{who}: {self.get_action_display()} {self.target_label or self.target_type}"

    @property
    def is_auth_event(self):
        return self.action in (self.Action.LOGIN, self.Action.LOGIN_FAILED, self.Action.LOGOUT)
