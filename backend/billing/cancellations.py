"""Ending a service on its billing end date, and cancelling the customer.

Service.end_date already existed and did nothing at all -- staff could set it,
and the date would pass with the customer still connected and still being
billed. This is what makes it mean something.

Two steps, in order:

1. Any service whose end date has arrived is TERMINATED. Saved one at a time
   through .save() rather than a queryset .update(), because the signals ARE
   the work: terminating a service rewrites its RADIUS rows to reject, drops
   the live session, releases its IP and updates the router's block list. A
   bulk update would leave a cancelled customer online indefinitely.

2. A customer whose services have ALL ended is set to Inactive -- "cancelled"
   in the words staff use for it.

`<=` rather than `==` throughout, so a cancellation dated Monday still happens
if the job did not run until Thursday. The same lesson as tariff changes: a
scheduled job that silently missed a day must not silently skip the work.

A customer with NO services is never touched. "All their services have ended"
and "they never had one" are different situations, and cancelling somebody
mid-signup because their first service has not been added yet would be its own
kind of wrong.

**The boundary.** The end date is the first day WITHOUT service. Set 31
August and the line stops at 00:00 on the 31st -- so the last full day of
service is the 30th. That is why the job wants to run just after midnight:
the cut and the date turning are meant to be the same moment.

Run it later in the day and the customer keeps service for however long the
job was late by. Nothing breaks, but they get hours they were not sold, so
the recommended crontab entry is 00:01 rather than the small hours.
"""
import logging

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

# `lte`: a service is due the moment its end date arrives, so a job running at
# 00:01 on the 31st ends a service dated the 31st. Switching to `lt` would give
# them the whole of that day and cut off the following midnight instead.
DUE_FILTER = "lte"


def due_services(as_of=None, partner_ids=None):
    """Services whose end date has arrived and which are still running.

    `partner_ids` scopes the work to those resellers' customers. A
    recurring-billing run can be scoped to selected partners on the
    Finance screen and calls this job as part of itself -- unscoped, it
    disconnected customers belonging to resellers the operator had
    deliberately left out of the run. None (the cron default) means every
    customer, which is what the nightly job wants.

    The scope is `partner_id__in`, so a customer with NO partner -- which
    most of Skybre's own customers are -- is only ever touched by an
    unscoped run. They belong to no reseller, so no reseller's run should
    end their service.
    """
    from .models import Service

    as_of = as_of or timezone.localdate()
    lookup = {f"end_date__{DUE_FILTER}": as_of}
    qs = Service.objects.filter(end_date__isnull=False, **lookup)
    if partner_ids:
        qs = qs.filter(customer__partner_id__in=partner_ids)
    return (
        qs.exclude(status=Service.Status.TERMINATED)
        .select_related("customer")
        .order_by("pk")
    )


def customers_fully_ended(customer_ids, ending_service_ids=()):
    """Of these customers, the ones whose every service is now terminated.

    Excludes customers with no services at all -- see the module docstring.

    `ending_service_ids` are services the caller is about to terminate but
    has not yet. It exists for the dry run, and without it the dry run
    could not answer this question at all: it asked the database which
    customers have nothing left running, and in a dry run nothing has
    been terminated, so the answer was always "none". The preview listed
    the services it would end and an empty cancellation list -- hiding
    the consequence staff actually care about, that the customer gets
    marked Inactive, in the one screen that exists to show it before
    committing.

    On the commit path these services are already terminated, so passing
    them changes nothing; the two paths now compute the same answer from
    the same inputs, which is what makes the preview worth reading.
    """
    from customers.models import Customer

    from .models import Service

    still_running = set(
        Service.objects.filter(customer_id__in=customer_ids)
        .exclude(status=Service.Status.TERMINATED)
        .exclude(pk__in=ending_service_ids)
        .values_list("customer_id", flat=True)
    )
    has_any = set(
        Service.objects.filter(customer_id__in=customer_ids).values_list("customer_id", flat=True)
    )
    finished = (has_any - still_running)
    return Customer.objects.filter(pk__in=finished).exclude(status=Customer.Status.INACTIVE)


def apply_due_cancellations(as_of=None, commit=True, partner_ids=None):
    """End every due service, then cancel any customer left with none running.

    Returns (ended, cancelled) as lists of (id, label) so a caller -- a cron
    run, a billing run, or a dry run -- can report exactly what happened
    rather than a bare count.

    `partner_ids` scopes it to those resellers' customers; see due_services.
    """
    from customers.models import Customer

    from .models import Service

    as_of = as_of or timezone.localdate()
    services = list(due_services(as_of, partner_ids=partner_ids))
    ended = []
    touched_customers = set()

    for service in services:
        label = f"{service.customer.full_name} — {service.tariff.name}"
        touched_customers.add(service.customer_id)
        if commit:
            with transaction.atomic():
                service.status = Service.Status.TERMINATED
                service.save()
        ended.append((service.pk, label))
        logger.info("Service %s ended on its billing end date (%s)", service.pk, service.end_date)

    cancelled = []
    if touched_customers:
        for customer in customers_fully_ended(
            touched_customers, ending_service_ids=[pk for pk, _label in ended]
        ):
            cancelled.append((customer.pk, customer.full_name))
            if commit:
                # Through .save() so the customer signals run -- though by
                # this point every service is already terminated, so there is
                # nothing left for them to cascade to.
                customer.status = Customer.Status.INACTIVE
                customer.save()
            logger.info("Customer %s cancelled -- every service has ended", customer.pk)

    return ended, cancelled
