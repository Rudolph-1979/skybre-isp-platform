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
  * A restricted staff member sees their partners' customers PLUS
    customers with no partner at all, because a direct customer is not
    "owned" by any reseller.
  * A customer-role user sees only their own record. Anyone with neither
    a staff role nor a customer profile sees nothing.

Lives in its own module rather than in views.py so that any app can
import it without importing another app's viewsets.
"""
from django.db.models import Q


def scope_customers_to_user(qs, user):
    """Narrow a queryset OF customers to what `user` may see."""
    if user.is_staff_member:
        allowed = getattr(user, "allowed_partners", None) or []
        if allowed and user.role != user.Role.ADMIN:
            return qs.filter(Q(partner_id__in=allowed) | Q(partner__isnull=True))
        return qs
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
    allowed = getattr(user, "allowed_partners", None) or []
    if not allowed or user.role == user.Role.ADMIN:
        return qs
    condition = Q(**{f"{path}__partner_id__in": allowed}) | Q(
        **{f"{path}__partner__isnull": True}
    )
    if allow_null:
        condition |= Q(**{f"{path}__isnull": True})
    return qs.filter(condition)
