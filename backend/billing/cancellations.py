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


def due_services(as_of=None):
    """Services whose end date has arrived and which are still running."""
    from .models import Service

    as_of = as_of or timezone.localdate()
    lookup = {f"end_date__{DUE_FILTER}": as_of}
    return (
        Service.objects.filter(end_date__isnull=False, **lookup)
        .exclude(status=Service.Status.TERMINATED)
        .select_related("customer")
        .order_by("pk")
    )


def customers_fully_ended(customer_ids):
    """Of these customers, the ones whose every service is now terminated.

    Excludes customers with no services at all -- see the module docstring.
    """
    from customers.models import Customer

    from .models import Service

    still_running = set(
        Service.objects.filter(customer_id__in=customer_ids)
        .exclude(status=Service.Status.TERMINATED)
        .values_list("customer_id", flat=True)
    )
    has_any = set(
        Service.objects.filter(customer_id__in=customer_ids).values_list("customer_id", flat=True)
    )
    finished = (has_any - still_running)
    return Customer.objects.filter(pk__in=finished).exclude(status=Customer.Status.INACTIVE)


def apply_due_cancellations(as_of=None, commit=True):
    """End every due service, then cancel any customer left with none running.

    Returns (ended, cancelled) as lists of (id, label) so a caller -- a cron
    run, a billing run, or a dry run -- can report exactly what happened
    rather than a bare count.
    """
    from customers.models import Customer

    from .models import Service

    as_of = as_of or timezone.localdate()
    services = list(due_services(as_of))
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
        for customer in customers_fully_ended(touched_customers):
            cancelled.append((customer.pk, customer.full_name))
            if commit:
                # Through .save() so the customer signals run -- though by
                # this point every service is already terminated, so there is
                # nothing left for them to cascade to.
                customer.status = Customer.Status.INACTIVE
                customer.save()
            logger.info("Customer %s cancelled -- every service has ended", customer.pk)

    return ended, cancelled
