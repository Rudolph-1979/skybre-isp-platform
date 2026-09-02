"""Keeping a Customer's status and their Services' statuses in step.

Setting a customer to Suspended used to do nothing whatsoever to their
connection. Every piece of enforcement in this platform keys off
Service.status -- RADIUS rows (radiusauth.signals.sync_service_radius), the
router block list and the live-session disconnect (network.signals) -- and
nothing read Customer.status at all. So a customer marked Suspended on their
own page carried on browsing exactly as before, which is not what anyone
means by that word.

The two directions are deliberately not symmetric:

  * Customer -> Suspended suspends every ACTIVE service.
  * Customer -> Active restores only the services THIS mechanism suspended
    (auto_suspended_with_customer), never one suspended for non-payment or
    for a fault. Reactivating a customer must not hand internet back to
    somebody who still owes money.

Terminated and pending services are never touched in either direction: those
states mean something a customer-level flag has no business overriding.

The third rule is the one that was missing, and it goes the other way. Staff
looking at a suspended customer's page naturally restore the LINE -- Services
-> Edit -> Active -- rather than the customer, because that is the row the
problem is on. That worked: the service came back, RADIUS was rewritten, the
session reconnected. But nothing moved the customer, so the badge at the top
of the page went on saying Suspended over a customer who was online. Two
fields disagreeing about the same fact, with the wrong one in the bigger type.

So restoring a service now lifts the customer too, and the cascade is
suppressed while it happens: reactivating ONE line must not quietly reactivate
the customer's other suspended lines. It only ever restores what staff
actually clicked.

It also keeps auto_suspended_with_customer honest. That flag is the whole
basis of the asymmetry above, and it used to survive any change staff made by
hand -- so a service suspended with its customer, restored manually, then
suspended again for non-payment, was still carrying the flag and got handed
back the next time the customer was reactivated. Exactly the case the flag
exists to prevent. Any status change made outside this file now clears it.
"""
import logging

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from .models import Customer

logger = logging.getLogger(__name__)


@receiver(pre_save, sender=Customer)
def _customer_pre_save_capture_status(sender, instance, **kwargs):
    if not instance.pk:
        instance._old_customer_status = None
        return
    instance._old_customer_status = (
        Customer.objects.filter(pk=instance.pk).values_list("status", flat=True).first()
    )


@receiver(post_save, sender=Customer)
def _customer_post_save_apply_status_to_services(sender, instance, created, **kwargs):
    from billing.models import Service

    if created:
        return
    old_status = getattr(instance, "_old_customer_status", None)
    if old_status is None or old_status == instance.status:
        return

    # Set when the customer was lifted BECAUSE one of their services was
    # restored by hand (see _service_post_save_reconcile_customer). Cascading
    # from there would restore the customer's other suspended lines as well,
    # which nobody asked for -- staff reactivated one service, not all of them.
    if getattr(instance, "_skip_service_cascade", False):
        return

    # Saved one at a time, through the normal .save() path, on purpose: a
    # queryset .update() would skip every signal, and the signals ARE the
    # enforcement -- the RADIUS rewrite, the router block list, and dropping
    # the live session all hang off Service's post_save. A bulk update here
    # would leave the customer suspended in the database and online in reality,
    # which is the bug this file exists to fix.
    # Bad Debt cuts them off exactly as a suspension does. Leaving somebody
    # connected after writing off what they owe would be the one status where
    # the label and the reality disagree most expensively.
    if instance.status in (Customer.Status.SUSPENDED, Customer.Status.BAD_DEBT):
        services = instance.services.filter(status=Service.Status.ACTIVE)
        for service in services:
            service.status = Service.Status.SUSPENDED
            service.auto_suspended_with_customer = True
            # Marks this as our own doing, so the Service handler below leaves
            # the flag alone instead of clearing it as a manual suspension.
            service._customer_cascade = True
            service.save()
        if services:
            logger.info(
                "Customer %s set to %s -- suspended %d service(s)",
                instance.pk, instance.status, len(services),
            )
        return

    if instance.status == Customer.Status.ACTIVE:
        services = instance.services.filter(
            status=Service.Status.SUSPENDED, auto_suspended_with_customer=True
        )
        for service in services:
            service.status = Service.Status.ACTIVE
            service.auto_suspended_with_customer = False
            service._customer_cascade = True
            service.save()
        if services:
            logger.info("Customer %s reactivated -- restored %d service(s)", instance.pk, len(services))


# ---------------------------------------------------------------------------
# Service -> Customer
# ---------------------------------------------------------------------------
#
# Lazy string sender rather than an import: billing imports customers, so
# importing Service at the top of this module is a circular import.


@receiver(pre_save, sender="billing.Service")
def _service_pre_save_capture_status(sender, instance, **kwargs):
    # Deliberately its own attribute. network.signals captures the same thing
    # under _network_old_status for its own purposes, and two handlers sharing
    # one scratch attribute is a bug waiting for whichever runs second.
    if not instance.pk:
        instance._customers_old_service_status = None
        return
    instance._customers_old_service_status = (
        sender.objects.filter(pk=instance.pk).values_list("status", flat=True).first()
    )


@receiver(post_save, sender="billing.Service")
def _service_post_save_reconcile_customer(sender, instance, created, **kwargs):
    if created:
        return
    old_status = getattr(instance, "_customers_old_service_status", None)
    if old_status is None or old_status == instance.status:
        return

    cascade = getattr(instance, "_customer_cascade", False)

    # Any status change staff made themselves means this service's state is no
    # longer "down because the customer is", whichever way it went. Written
    # with .update() rather than .save() so it doesn't re-enter the signals.
    if not cascade and instance.auto_suspended_with_customer:
        sender.objects.filter(pk=instance.pk).update(auto_suspended_with_customer=False)
        instance.auto_suspended_with_customer = False

    if cascade or instance.status != sender.Status.ACTIVE:
        return

    customer = instance.customer
    # Suspended only, deliberately NOT Bad Debt. Suspension is purely about
    # connectivity, so a live service contradicts it and the badge has to
    # follow. Bad Debt is about money owed -- a written-off customer whose line
    # staff have turned back on, pending a dispute or as a goodwill gesture, is
    # a coherent state, and silently clearing the write-off because somebody
    # restored a service would erase an accounting decision nobody made here.
    if customer.status != Customer.Status.SUSPENDED:
        return

    customer.status = Customer.Status.ACTIVE
    customer._skip_service_cascade = True
    # updated_at is auto_now, and auto_now only writes when the field is in
    # update_fields -- leave it out and the row silently keeps its old
    # timestamp, which is the one thing an audit trail can't afford.
    customer.save(update_fields=["status", "updated_at"])
    logger.info(
        "Service %s reactivated by hand -- customer %s lifted from suspended to active",
        instance.pk,
        customer.pk,
    )
