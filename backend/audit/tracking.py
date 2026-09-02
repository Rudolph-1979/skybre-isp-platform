"""Turns model saves into audit rows.

The registry below is deliberately an explicit list rather than "track
everything". Tracking every model in the project would bury the handful
of events anyone ever looks for under MonitoringReading rows, live-rate
samples and usage buckets -- tens of thousands a day of machine writes,
none of which anybody did "using their credentials". What belongs here
is what a person changes on purpose and might later have to justify.
"""
import logging

from django.db.models.signals import m2m_changed, post_delete, post_save, pre_save
from django.dispatch import receiver

from .context import current_actor, current_request_meta, current_system_label

logger = logging.getLogger(__name__)

# Never recorded with a value, on any model, whatever the registry says.
# The audit trail is read by more people than the thing it is auditing,
# so a password or token landing here would be a downgrade in secrecy,
# not an upgrade in accountability. That a secret CHANGED is recorded --
# that is the part which matters for "who did this" -- and the value on
# neither side is.
SENSITIVE_FIELDS = frozenset(
    {
        "password",
        "secret",
        "api_password",
        "totp_secret",
        "token",
        "access_token",
        "refresh_token",
        "radius_password",
        "smtp_password",
        "api_key",
    }
)

# Fields that change on nearly every save and mean nothing on their own.
NOISE_FIELDS = frozenset({"updated_at", "modified_at", "last_login", "last_seen_at"})


class Tracked:
    """One registry entry.

    fields   -- names to watch, or None for "every concrete field".
    exclude  -- names to ignore on top of NOISE_FIELDS.
    customer -- dotted path from the instance to the Customer this event
                belongs to, for the per-customer History tab. None if the
                model has nothing to do with a customer.
    """

    def __init__(self, fields=None, exclude=(), customer=None):
        self.fields = fields
        self.exclude = frozenset(exclude) | NOISE_FIELDS
        self.customer = customer


REGISTRY = {
    "customers.Customer": Tracked(customer="self"),
    "customers.Partner": Tracked(),
    "billing.Service": Tracked(customer="customer"),
    "billing.Tariff": Tracked(),
    "billing.Invoice": Tracked(customer="customer"),
    "billing.Payment": Tracked(customer="customer"),
    "accounts.User": Tracked(
        # Explicit rather than "all fields": AbstractUser carries a pile
        # of Django internals nobody edits, and allowed_sections /
        # allowed_partners / role / is_active are the ones that change
        # what a person can reach.
        fields=[
            "username", "email", "first_name", "last_name", "role",
            "is_active", "is_staff", "is_superuser", "allowed_sections",
            "allowed_partners", "password",
        ],
    ),
    "tickets.Ticket": Tracked(customer="customer"),
    # Who moved a lead to Lost, and when. The stage a deal died at is a
    # thing people argue about later; LeadNote records the narrative, this
    # records the fields.
    "sales.Lead": Tracked(customer="customer"),
    "network.NetworkSite": Tracked(),
    "network.Device": Tracked(),
}


def model_key(instance):
    meta = instance._meta
    return f"{meta.app_label}.{meta.object_name}"


def _render(instance, field):
    """One field's value as a person would read it.

    Choice fields go through get_FOO_display so the trail says
    "Suspended" rather than "suspended", and foreign keys are rendered
    by str() so it says the tariff's name rather than its id -- an audit
    row reading "Tariff: 4 -> 7" requires a database to interpret, which
    defeats the point of writing it down.
    """
    name = field.name
    if field.many_to_many:
        # Rendered by the m2m_changed path, not here -- at pre_save time
        # the through-table rows for a new object don't exist yet.
        return None
    if field.is_relation:
        related = getattr(instance, name, None)
        return str(related) if related is not None else ""
    display = getattr(instance, f"get_{name}_display", None)
    if callable(display):
        try:
            return str(display())
        except Exception:  # pragma: no cover - defensive only
            pass
    value = getattr(instance, name, None)
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (list, tuple)):
        # allowed_sections / allowed_partners are arrays. "['tickets',
        # 'finance']" is a Python repr leaking onto an audit screen;
        # "tickets, finance" is the same information in English. Empty
        # means unrestricted throughout this platform, so it is spelled
        # out rather than shown as a blank that reads like "removed".
        return ", ".join(str(v) for v in value) if value else "(none)"
    return str(value)


def _watched_fields(instance, config):
    for field in instance._meta.concrete_fields:
        if field.primary_key:
            continue
        if config.fields is not None and field.name not in config.fields:
            continue
        if field.name in config.exclude:
            continue
        yield field


def _diff(old, new, config):
    changes = []
    for field in _watched_fields(new, config):
        name = field.name
        if name in SENSITIVE_FIELDS:
            # Compare the raw stored value (a hash, for password) so a
            # real change is still detected, but never show either side.
            if getattr(old, field.attname, None) != getattr(new, field.attname, None):
                changes.append(
                    {"field": name, "label": _label(field), "from": "•••", "to": "••• (changed)"}
                )
            continue
        # Compare by the raw column (attname: `tariff_id`, not `tariff`)
        # so an unchanged FK costs no query. Only fields that actually
        # moved are then rendered.
        if getattr(old, field.attname, None) == getattr(new, field.attname, None):
            continue
        changes.append(
            {
                "field": name,
                "label": _label(field),
                "from": _render(old, field),
                "to": _render(new, field),
            }
        )
    return changes


def _label(field):
    verbose = getattr(field, "verbose_name", "") or field.name
    return str(verbose).replace("_", " ").capitalize()


def _customer_for(instance, config):
    if config.customer is None:
        return None
    if config.customer == "self":
        return instance if instance.pk else None
    try:
        customer = getattr(instance, config.customer, None)
    except Exception:
        # Mid-cascade the related row can already be gone. Losing the
        # per-customer link is acceptable; losing the event is not.
        return None
    return customer if (customer is not None and customer.pk) else None


def record(action, instance=None, changes=None, detail="", actor=None, customer=None):
    """Write one audit row.

    Deliberately swallows its own exceptions. An audit trail that can
    break a customer edit is a worse outcome than one with a gap in it --
    nobody should ever be unable to suspend a line because logging that
    suspension failed. The failure goes to the container log instead.
    """
    from .models import AuditEvent

    try:
        who = actor if actor is not None else current_actor()
        meta = current_request_meta()
        label = ""
        if who is not None:
            full = f"{who.first_name} {who.last_name}".strip()
            label = f"{full} ({who.username})" if full else who.username
        elif current_system_label():
            label = current_system_label()

        target_type = model_key(instance) if instance is not None else ""
        AuditEvent.objects.create(
            actor=who if (who is not None and who.pk) else None,
            actor_label=label[:255],
            action=action,
            target_type=target_type,
            target_id=str(instance.pk) if instance is not None and instance.pk else "",
            target_label=str(instance)[:255] if instance is not None else "",
            customer=customer,
            changes=changes or [],
            detail=detail[:500],
            ip_address=meta["ip_address"],
            user_agent=meta["user_agent"],
        )
    except Exception:
        logger.exception("Failed to write audit event (%s)", action)


@receiver(pre_save)
def _stash_previous(sender, instance, **kwargs):
    key = model_key(instance)
    if key not in REGISTRY or instance.pk is None:
        return
    try:
        # _base_manager, not objects: a model whose default manager
        # filters anything out would make those rows look like creates on
        # every save.
        instance._audit_previous = sender._base_manager.get(pk=instance.pk)
    except sender.DoesNotExist:
        # A save with an explicit pk that doesn't exist yet -- a create,
        # not an edit.
        instance._audit_previous = None


@receiver(post_save)
def _on_save(sender, instance, created, **kwargs):
    key = model_key(instance)
    config = REGISTRY.get(key)
    if config is None:
        return
    if kwargs.get("raw"):
        # Loading a fixture. Not somebody's edit.
        return

    previous = getattr(instance, "_audit_previous", None)
    instance._audit_previous = None

    if created or previous is None:
        record("created", instance=instance, customer=_customer_for(instance, config))
        return

    changes = _diff(previous, instance, config)
    if not changes:
        # A save that changed nothing tracked. Recording it would fill the
        # trail with rows that say a person did something when they did
        # not -- every list refresh that calls .save() would appear as an
        # edit.
        return
    record("updated", instance=instance, changes=changes, customer=_customer_for(instance, config))


@receiver(m2m_changed)
def _on_m2m_change(sender, instance, action, pk_set, reverse, model, **kwargs):
    """Many-to-many edits, which a field diff structurally cannot see.

    A site's partners live in a through table, not on the row, so
    `_diff` compares two identical NetworkSite records and reports
    nothing while the set of partners who can see that site changes
    underneath it. That is exactly the kind of edit an audit trail is
    for, so it gets its own path.
    """
    if action not in ("post_add", "post_remove", "post_clear"):
        return
    if reverse:
        # The change is being made from the far side; it will be
        # recorded against the far object if that one is tracked.
        return
    config = REGISTRY.get(model_key(instance))
    if config is None:
        return
    field_name = ""
    for field in instance._meta.many_to_many:
        if getattr(instance, field.name).through is sender:
            field_name = field.name
            break
    if not field_name:
        return

    if action == "post_clear":
        summary = "(none)"
        verb = "cleared"
    else:
        names = [str(obj) for obj in model._base_manager.filter(pk__in=pk_set or [])]
        summary = ", ".join(names) if names else "(none)"
        verb = "added" if action == "post_add" else "removed"

    now = list(getattr(instance, field_name).all())
    record(
        "updated",
        instance=instance,
        changes=[
            {
                "field": field_name,
                "label": _label_for_m2m(field_name),
                "from": f"{verb}: {summary}",
                "to": ", ".join(str(o) for o in now) if now else "(none)",
            }
        ],
        customer=_customer_for(instance, config),
    )


def _label_for_m2m(field_name):
    return field_name.replace("_", " ").capitalize()


@receiver(post_delete)
def _on_delete(sender, instance, **kwargs):
    key = model_key(instance)
    config = REGISTRY.get(key)
    if config is None:
        return
    customer = _customer_for(instance, config)
    # A customer deleting themselves can't be filed under themselves --
    # the row is about to be gone, and the FK would null out immediately.
    if key == "customers.Customer":
        customer = None
    record("deleted", instance=instance, customer=customer)
