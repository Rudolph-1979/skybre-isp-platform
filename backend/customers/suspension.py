"""Which customers have a suspended LINE, and why -- for display only.

When the billing run suspends a customer for non-payment it suspends their
SERVICES (billing.recurring._process_blocking sets the service to Suspended
and marks suspended_for_nonpayment) and leaves Customer.status alone. That
is deliberate: Customer.status drives its own cascade onto the services
(customers.signals), and the proven suspend -> pay -> reconnect cycle
depends on it staying untouched. The result was a customer whose only line
was off still reading "Active" everywhere.

Rudolph's decision, 2026-09-23: show it, don't change the status. So this
module only REPORTS. Nothing here writes to a customer or a service, and
nothing here is read by enforcement, blocking or reactivation.

Resolved once per page by CustomerViewSet (like radiusauth.offline
.connection_state) and handed to CustomerSerializer through the context,
so fifty customers cost one query, not fifty.
"""
from django.db.models import Count, Q


def suspension_state(customers):
    """{customer_pk: {"suspended": n, "nonpayment": m}} for the customers
    given, counting only services whose status is Suspended right now.
    `nonpayment` is how many of those the billing run suspended
    (Service.suspended_for_nonpayment). Customers with no suspended line
    are left out of the map; the serializer reads that as 0."""
    # Imported here: billing imports customers, so a module-level import of
    # billing.models from customers is circular.
    from billing.models import Service

    ids = [c.pk for c in customers]
    if not ids:
        return {}
    rows = (
        Service.objects.filter(customer_id__in=ids, status=Service.Status.SUSPENDED)
        .values("customer_id")
        .annotate(
            n=Count("id"),
            nonpayment=Count("id", filter=Q(suspended_for_nonpayment=True)),
        )
    )
    return {r["customer_id"]: {"suspended": r["n"], "nonpayment": r["nonpayment"]} for r in rows}


def suspended_services(nonpayment_only=False):
    """Services Suspended right now; with nonpayment_only, just the ones the
    billing run suspended. The one definition the Customers filter and the
    dashboard tile both use, so the tile's number and the list it opens
    cannot disagree."""
    from billing.models import Service

    services = Service.objects.filter(status=Service.Status.SUSPENDED)
    if nonpayment_only:
        services = services.filter(suspended_for_nonpayment=True)
    return services
