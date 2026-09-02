"""Signal hooks that keep the routers in step with Service status changes,
so staff never have to remember to click "Sync now".

Two separate things happen when a service stops being active:

  1. Its IP goes into the router-side block address-list, if that device has
     `block_disabled_customers` on (see
     router_sync.block_or_unblock_single_service).
  2. Its LIVE session gets dropped (see
     router_sync.disconnect_service_sessions).

The second one is what actually cuts a suspended customer off today.
FreeRADIUS is only consulted when a client logs IN, so rewriting a suspended
service's RADIUS rows -- which radiusauth.signals does correctly -- has no
effect at all on the session the customer is already using. Without the
disconnect, "suspended" means "suspended from their next reconnection,
whenever that happens to be", which on a stable PPPoE link can be weeks.
"""
import logging
import threading

from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from billing.models import Service

from . import router_sync

logger = logging.getLogger(__name__)


def _after_commit_in_background(description, work):
    """Run router I/O after the transaction commits, off the request thread.

    Both things this module does talk to a MikroTik over the network, with an
    8-second connect timeout each. Doing that inline meant a slow or
    unreachable router added seconds to whatever HTTP request happened to
    trigger the save -- and a customer being reactivated re-saves every one of
    their services, so the delays stacked up until the request could outlive
    the proxy's timeout and 504. From the browser that looked exactly like
    "I set them back to Active and nothing happened", even though the database
    write had already gone through.

    on_commit because the worker reads the row back and must not race the
    transaction; daemon thread because the outcome is advisory -- the database
    is already correct and the reconciliation is idempotent, so a failure here
    costs a log line, not consistency. Same pattern as the bulk email sender.
    """

    def _worker():
        try:
            work()
        except Exception:                                   # noqa: BLE001
            # Nothing upstream can act on this -- the request is long gone.
            logger.exception("Background router sync failed: %s", description)

    transaction.on_commit(lambda: threading.Thread(target=_worker, daemon=True).start())


@receiver(pre_save, sender=Service)
def _service_pre_save_capture_status(sender, instance, **kwargs):
    """Remember the status and tariff this service currently has in the database.

    Captured under a name of this module's own rather than reusing the
    `_old_status` radiusauth stashes, so neither app depends on the other's
    private attribute or on signal registration order. Only the one column is
    fetched.
    """
    if not instance.pk:
        instance._network_old_status = None
        instance._network_old_tariff_id = None
        return
    previous = (
        Service.objects.filter(pk=instance.pk).values_list("status", "tariff_id").first()
    )
    instance._network_old_status, instance._network_old_tariff_id = previous or (None, None)


@receiver(post_save, sender=Service)
def _service_post_save_sync_blocking(sender, instance, **kwargs):
    _after_commit_in_background(
        f"blocking rules for service {instance.pk}",
        lambda: router_sync.block_or_unblock_single_service(instance),
    )


@receiver(post_save, sender=Service)
def _service_post_save_drop_live_session(sender, instance, created, **kwargs):
    """Drop the live session whenever something changes what RADIUS would answer.

    RADIUS is only consulted at login, so a session established under the old
    answer keeps running under the terms it was granted. Everything below is
    the same bug wearing a different hat:

      * active -> suspended: they carry on browsing on the session they
        already have, so "suspended" would mean "suspended from their next
        reconnection", which on a stable link can be weeks.

      * suspended -> active: they stay on the WALLED-GARDEN address they were
        given while suspended, so paying up doesn't restore their internet.
        This was the "unsuspending doesn't work unless I kick them off the
        core" case -- kicking them off the core is what this does now.

      * tariff changed: the rate limit was applied by the router at login, so
        an upgrade you sold them, or a scheduled change that just landed,
        wouldn't reach them until they happened to reconnect. Same for a
        downgrade, which is worse -- they'd keep the faster speed they no
        longer pay for.

    Hence the rule: kick on any change to status OR tariff. Not on an
    unchanged re-save, because a session already running under the current
    answer has nothing that needs correcting, and a router round trip per save
    adds up across a billing run.

    A brand-new service has no session to drop. If the previous values can't
    be determined the change can't be established either, so nothing happens
    -- which fails toward "don't touch the router", the safe direction.
    """
    if created:
        return
    old_status = getattr(instance, "_network_old_status", None)
    old_tariff_id = getattr(instance, "_network_old_tariff_id", None)
    if old_status is None:
        return

    status_changed = old_status != instance.status
    tariff_changed = old_tariff_id is not None and old_tariff_id != instance.tariff_id
    if not status_changed and not tariff_changed:
        return

    # A status change re-addresses the session, so it needs a disconnect. A
    # tariff change only needs the speed re-programmed, which CoA does WITHOUT
    # dropping the customer -- delivering an upgrade by cutting someone off has
    # always been the wrong shape, it was just the only one available.
    #
    # radiusauth.enforcement picks between them, tries CoA first and the
    # RouterOS API second, and records the outcome either way. The call this
    # replaced swallowed every failure into a log line in this same background
    # thread, which is how a broken enforcement path survived weeks of use
    # while the screen said "Saved".
    reason = "status" if status_changed else "tariff"
    _after_commit_in_background(
        f"push {reason} change for service {instance.pk} to its live session",
        lambda: _apply_radius_change(instance, reason),
    )


def _apply_radius_change(service, reason):
    # Imported at call time, not module scope: radiusauth.enforcement reaches
    # back into network.router_sync for its fallback, and network imports this
    # module during app-ready.
    from radiusauth.enforcement import apply_change

    return apply_change(service, reason)
