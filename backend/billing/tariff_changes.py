"""Applying tariff changes that were booked for a future date.

Why this exists at all: a tariff change agreed today usually belongs at the
start of the customer's next billing period, not now. Editing Service.tariff
straight away would bill them on the new price immediately AND rate-limit them
to the new speed immediately, which is wrong in both directions -- an upgrade
handed over early, or a downgrade applied to a period they already paid for at
the higher rate.

So the change is held on the service (pending_tariff / pending_tariff_date)
and applied here, on or after the day.

Deliberately idempotent: applying a change clears the pending fields, so
running this twice in a day -- or from both cron and a billing run -- does the
work once and finds nothing the second time.
"""

import logging

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


def due_tariff_changes(as_of=None, partner_ids=None):
    """Services whose booked change has come due, oldest first.

    `__lte` rather than `==` on purpose: if nothing ran for three days -- cron
    was down, the box was rebooted -- a change dated Monday must still apply
    on Thursday rather than being skipped forever because its exact day passed.

    `partner_ids` scopes the work to those resellers' customers, for the
    same reason billing.cancellations.due_services takes it: a
    recurring-billing run scoped to one partner calls this, and unscoped
    it changed the plan -- and, through the signals, the live rate limit
    -- of customers belonging to resellers deliberately left out of that
    run. Worse than a wrong invoice, because applying the change CLEARS
    the booking, so the change the other reseller had scheduled is gone
    and the next run finds nothing to do. None means every customer,
    which is what the nightly cron wants; a customer with no partner is
    only ever touched by an unscoped run.
    """
    from .models import Service

    as_of = as_of or timezone.localdate()
    qs = Service.objects.filter(
        pending_tariff__isnull=False,
        pending_tariff_date__isnull=False,
        pending_tariff_date__lte=as_of,
    )
    if partner_ids:
        qs = qs.filter(customer__partner_id__in=partner_ids)
    return (
        qs.select_related("customer", "tariff", "pending_tariff", "device")
        .order_by("pending_tariff_date", "pk")
    )


def apply_due_tariff_changes(as_of=None, commit=True, partner_ids=None):
    """Switch every due service onto its pending tariff.

    Returns a list of (service, old_tariff, new_tariff) for reporting. With
    commit=False nothing is written -- the caller gets the same list as a
    preview, which is what the management command's --dry-run prints.

    `partner_ids` scopes it to those resellers' customers; see
    due_tariff_changes.

    Saved one at a time through the normal .save() path, never a queryset
    .update(): the signals ARE the rest of the job. Service's post_save
    rewrites the RADIUS reply rows for the new speed, and network.signals
    drops the live session so the new rate limit actually takes effect --
    without which the customer keeps their old speed until they happen to
    reconnect, which on a stable link can be weeks.
    """
    applied = []
    for service in due_tariff_changes(as_of, partner_ids=partner_ids):
        old_tariff = service.tariff
        new_tariff = service.pending_tariff
        applied.append((service, old_tariff, new_tariff))
        if not commit:
            continue
        with transaction.atomic():
            service.tariff = new_tariff
            service.pending_tariff = None
            service.pending_tariff_date = None
            # update_fields lists the FK so the pre_save capture and the
            # post_save signals both see the tariff move -- that is what
            # triggers the RADIUS rewrite and the session drop.
            service.save(update_fields=["tariff", "pending_tariff", "pending_tariff_date"])
        logger.info(
            "Service %s (%s): tariff changed %s -> %s",
            service.pk, service.customer.full_name, old_tariff.name, new_tariff.name,
        )
    return applied
