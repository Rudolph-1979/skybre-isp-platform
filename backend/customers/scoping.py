"""Who a staff member is allowed to see customers -- and customer-linked
records -- for.

One module, imported by everything, because this rule has already drifted
twice. CustomerDeletionRequestViewSet.bulk_delete had a copy-pasted
version that silently stopped filtering while its comment still claimed
it did (one reseller-restricted Management account could delete the whole
book), and billing never applied the rule at all, so resellers could read
and edit each other's invoices.

The rule itself, unchanged from where it started in
CustomerViewSet.get_queryset:

  * User.allowed_partners empty means unrestricted -- the same convention
    as allowed_sections.
  * Admin always sees everything, whatever is in allowed_partners.
  * A restricted staff member sees their partners' customers, and
    direct customers (no partner) ONLY when "Direct" is ticked for them --
    stored as DIRECT (0) in allowed_partners. Rudolph, 5 Oct 2026: "user
    should only see information regarding his partner and not direct user
    if it is not selected". (It used to show direct customers to every
    restricted user.)
  * A customer-role user sees only their own record. Anyone with neither
    a staff role nor a customer profile sees nothing.

Lives in its own module rather than in views.py so that any app can
import it without importing another app's viewsets.
"""
from django.db.models import Q

#: In User.allowed_partners, stands for "direct customers (no partner)".
#: Partner ids start at 1, so 0 can never collide with a real one.
DIRECT = 0


def is_partner_restricted(user):
    """True when this user's view is narrowed by allowed_partners."""
    allowed = getattr(user, "allowed_partners", None) or []
    return bool(allowed) and user.role != user.Role.ADMIN


def allowed_partner_ids(user):
    """The real partner ids in allowed_partners (DIRECT left out)."""
    return [p for p in (getattr(user, "allowed_partners", None) or []) if p != DIRECT]


def sees_direct(user):
    """May this user see / assign customers with no partner?"""
    return not is_partner_restricted(user) or DIRECT in (user.allowed_partners or [])


def partner_condition(user, path="", services=True):
    """The Q a restricted user's rows must match, or None when unrestricted.

    `path` is the lookup to the customer (or other partner-carrying model)
    from the queryset's model, e.g. "customer" -> customer__partner_id__in.

    For customers (`services=True`, the default) a client also matches when
    any of its non-terminated services is with one of the user's partners
    (Service.partner -- several partners on one client, 5 Oct 2026), and
    then the WHOLE client is visible (Rudolph's choice). Pass
    services=False for a model that isn't a customer (sales leads).
    """
    if not is_partner_restricted(user):
        return None
    prefix = f"{path}__" if path else ""
    ids = allowed_partner_ids(user)
    condition = Q(**{f"{prefix}partner_id__in": ids})
    if services and ids:
        from billing.models import Service  # local: billing imports customers

        with_line = Service.objects.filter(partner_id__in=ids).exclude(
            status=Service.Status.TERMINATED
        ).values("customer_id")
        condition |= Q(**{f"{path}__in" if path else "pk__in": with_line})
    if DIRECT in user.allowed_partners:
        condition |= Q(**{f"{prefix}partner__isnull": True})
    return condition


def may_assign_partner(user, partner_id):
    """Can this user put a customer under this partner (None = direct)?"""
    if not is_partner_restricted(user):
        return True
    if partner_id is None:
        return DIRECT in user.allowed_partners
    return partner_id in allowed_partner_ids(user)


def scope_customers_to_user(qs, user):
    """Narrow a queryset OF customers to what `user` may see."""
    if user.is_staff_member:
        condition = partner_condition(user)
        return qs if condition is None else qs.filter(condition)
    customer_profile = getattr(user, "customer_profile", None)
    if customer_profile is None:
        return qs.none()
    return qs.filter(pk=customer_profile.pk)


def scope_by_customer(qs, user, path="customer", allow_null=True):
    """Narrow a queryset of records that each POINT AT a customer.

    `path` is the lookup from the model to customers.Customer, so a
    record related through another hop can use e.g. "invoice__customer".

    `allow_null` keeps rows whose customer is NULL. That is the right
    default for the models that have one: a scheduling Job with no
    customer is an office task, and an EmailLog with no customer is a
    staff notification -- neither belongs to a reseller, the same way a
    no-partner customer doesn't. Pass False where a null should be hidden.

    Only the partner dimension is applied here. Whether a customer-role
    user may see the model at all is a per-app question -- most of these
    endpoints are staff-only at the permission-class level -- so this
    deliberately does not narrow non-staff callers. Use
    scope_customers_to_user for that, or the app's own rule.
    """
    if not user.is_staff_member:
        return qs
    condition = partner_condition(user, path)
    if condition is None:
        return qs
    if allow_null:
        condition |= Q(**{f"{path}__isnull": True})
    else:
        # The "no partner" branch above is a LEFT JOIN test, so a row whose
        # own FK is NULL joins to nothing, reads back as partner IS NULL and
        # matches anyway -- swallowing the very rows allow_null=False exists
        # to hide. The live case is an approved InvoiceDeletionRequest: the
        # invoice is deleted and the FK goes NULL, but the row keeps the
        # quote number, the customer's name and the reason, so every
        # reseller-restricted user inherited another partner's audit trail.
        # Say NOT NULL out loud instead of leaning on the OR.
        condition &= Q(**{f"{path}__isnull": False})
    return qs.filter(condition)
