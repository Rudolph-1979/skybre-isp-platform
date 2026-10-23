"""Changes booked for a date that are not a plan change (package #20).

Decided with Rudolph on 2026-09-24, for Christo Van Den Heever moving from a
Vuma line to a Carfone 50 Mbps line on 1 October: "choose partner from
1 October and new service 1 October". So a move is three bookings, each
with its own date, all made ahead of time on the customer page:

  1. the customer's partner changes on a date (Customer.pending_partner /
     pending_partner_date) -- applied here;
  2. the old service ends (Service.end_date, the first day WITHOUT service)
     -- applied by billing.cancellations, as it always has been;
  3. the new service starts on its own (a Pending Activation service with
     Service.activate_on) -- applied here.

Run from the daily apply_tariff_changes cron (02:15 UTC = 04:15 SAST), which
is BEFORE the cancellations cron (02:30 UTC), so on the day the new service
is already active when the old one is terminated and the customer is never
left with no live service (which would set them Inactive). Also run by a
committed billing run, like tariff changes, so a run never bills around a
booking that is due.

Partner moves go first: a new service on the new partner's plan is only
valid once the customer is on that partner.

`<=` on the date, like tariff changes and cancellations: a job that missed a
day still does the work the next day instead of silently skipping it.
"""
import logging

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


def due_partner_moves(as_of=None, partner_ids=None):
    from customers.models import Customer

    as_of = as_of or timezone.localdate()
    qs = Customer.objects.filter(
        pending_partner__isnull=False, pending_partner_date__isnull=False,
        pending_partner_date__lte=as_of,
    )
    if partner_ids:
        qs = qs.filter(partner_id__in=partner_ids)
    return qs.select_related("partner", "pending_partner").order_by("pk")


def due_activations(as_of=None, partner_ids=None):
    from .models import Service

    as_of = as_of or timezone.localdate()
    qs = Service.objects.filter(
        status=Service.Status.PENDING, activate_on__isnull=False, activate_on__lte=as_of,
    )
    if partner_ids:
        qs = qs.filter(customer__partner_id__in=partner_ids)
    return qs.select_related("customer", "tariff", "replaces").order_by("activate_on", "pk")


def apply_service_handover(service):
    """If `service` REPLACES another one, take over its PPPoE login and
    Customer IP Pool address now (package #22) -- so a genuinely new
    service record can stand in for the old one at cutover without staff
    ever retyping a username the old row still holds, or fighting over an
    address still marked assigned to it.

    Only carries over what the new service didn't already set for itself:
    an explicit radius_username, or a specific IP already assigned to it,
    is left exactly as staff chose it. Mutates `service` in memory (the
    caller saves it) and writes the old service's cleared credentials and
    the moved IP Pool row(s) directly -- both inside the caller's own
    transaction, so a failure anywhere undoes the whole handover rather
    than leaving the login stranded on neither service.
    """
    from .models import Service

    old = service.replaces
    if old is None:
        return
    if not service.radius_username and old.radius_username:
        service.radius_username = old.radius_username
        service.radius_password = old.radius_password
        # Cleared and saved BEFORE `service` takes the value: the unique
        # constraint is checked per statement, not at commit, so writing
        # it here first is what lets the same username go straight onto
        # the new row in the very same transaction.
        old.radius_username = None
        old.radius_password = None
        old.save(update_fields=["radius_username", "radius_password"])
    if service.radius_connection_type == Service.ConnectionType.PPPOE and service.ip_assignment_mode in (
        Service.IPAssignmentMode.POOL, Service.IPAssignmentMode.AUTO,
    ):
        from network.models import IPPool

        already_has_one = service.ip_addresses.filter(pool__category=IPPool.Category.CUSTOMER).exists()
        if not already_has_one:
            # A queryset .update(), not assign_specific_customer_ip: this is
            # the system moving an address between two services it already
            # knows are the same connection, not staff picking an address
            # that might belong to someone else -- that check does not
            # apply here. radiusauth.signals._allocate_customer_ip reuses
            # whatever is already assigned_service=service, so the next
            # sync_service_radius (the caller's .save()) hands it the same
            # Framed-IP-Address the old service had.
            old.ip_addresses.filter(pool__category=IPPool.Category.CUSTOMER).update(assigned_service=service)


def apply_due_scheduled_changes(as_of=None, commit=True, partner_ids=None):
    """Apply every partner move and service start due on or before `as_of`.

    Returns {"moved": [(customer, old_partner, new_partner)],
             "started": [service], "skipped": [(service, reason)],
             "resumed": [service], "paused": [service]}.
    With commit=False nothing is written -- the same lists, as a preview.

    A service is NOT started for a customer who is Suspended, Bad Debt or
    Inactive: starting it would put a line back on for someone who is off
    (and a Suspended customer whose service turns Active is lifted by
    customers.signals). It is reported as skipped and stays Pending with its
    date, so it starts on the first run after the customer is back on.

    Pauses (#211, billing.service_pause): a paused line whose resume date has
    come is switched back on (Suspended with the customer, if the customer is
    off), and a booked pause whose first day has come switches the line off.
    """
    from customers.models import Customer

    from .models import Service

    moved, started, skipped = [], [], []
    resumed, paused = [], []

    for customer in due_partner_moves(as_of, partner_ids):
        old, new = customer.partner, customer.pending_partner
        moved.append((customer, old, new))
        if not commit:
            continue
        with transaction.atomic():
            customer.partner = new
            customer.pending_partner = None
            customer.pending_partner_date = None
            customer.save(update_fields=["partner", "pending_partner", "pending_partner_date"])
        logger.info(
            "Customer %s moved partner %s -> %s",
            customer.customer_id, getattr(old, "name", "none"), getattr(new, "name", "none"),
        )

    for service in due_activations(as_of, partner_ids):
        owner = Customer.objects.filter(pk=service.customer_id).only("status").first()
        if owner is not None and owner.status in Customer.OFF_STATUSES:
            skipped.append((service, f"customer is {owner.get_status_display()}"))
            continue
        started.append(service)
        if not commit:
            continue
        with transaction.atomic():
            apply_service_handover(service)
            service.status = Service.Status.ACTIVE
            service.activate_on = None
            # Through .save(): the signals write the RADIUS rows that let the
            # line authenticate, exactly as when staff set a service Active.
            # radius_username/radius_password are in update_fields even when
            # apply_service_handover left them untouched -- harmless, and it
            # keeps this one save() covering everything the handover changed.
            service.save(update_fields=["status", "activate_on", "radius_username", "radius_password"])
        logger.info("Service %s (%s) started on its booked date", service.pk, service.customer.customer_id)

    from . import service_pause

    as_of_day = as_of or timezone.localdate()
    for pause in service_pause.due_resumes(as_of_day, partner_ids):
        resumed.append(pause.service)
        if not commit:
            continue
        service_pause.resume(pause, None, today=as_of_day, scheduled=True)
        logger.info("Service %s (%s) resumed on its booked date", pause.service_id, pause.service.customer.customer_id)

    for pause in service_pause.due_pause_starts(as_of_day, partner_ids):
        paused.append(pause.service)
        if not commit:
            continue
        with transaction.atomic():
            service_pause.start_pause(pause)
        logger.info("Service %s (%s) paused on its booked date", pause.service_id, pause.service.customer.customer_id)

    return {"moved": moved, "started": started, "skipped": skipped, "resumed": resumed, "paused": paused}
