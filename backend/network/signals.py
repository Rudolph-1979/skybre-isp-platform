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
import atexit
import logging
import threading

from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from billing.models import Service

from . import router_sync

logger = logging.getLogger(__name__)

# Every background router push this module has started and not yet seen
# finish. Tracked so the interpreter can wait for them at exit -- see
# _drain_pending_work below for why that is not optional.
_pending = set()
_pending_lock = threading.Lock()

# Longest the process will wait at exit for outstanding router work. Two
# MikroTik operations at an 8-second connect timeout each, plus slack.
# Bounded on purpose: a hung router must not stop the process exiting.
DRAIN_TIMEOUT_SECONDS = 25


def _drain_pending_work(timeout=DRAIN_TIMEOUT_SECONDS):
    """Wait for outstanding router pushes before the interpreter exits.

    Registered with atexit, and it is the difference between this module
    working and not working outside a web server.

    The threads are daemons, which is right under gunicorn: the worker
    process lives for months, so a push always outlives the request that
    started it. Under `manage.py` it was catastrophic and silent. A
    management command's handle() returns, the interpreter exits, and
    Python kills daemon threads without joining them -- so a push that
    needs eight seconds to reach a router got microseconds.

    Which meant every unattended job's router half simply did not happen:

      * apply_cancellations --commit terminated the service, printed
        "Ended 3 service(s)", and never dropped the session. FreeRADIUS
        is only consulted at login, so those customers kept full internet
        on the session they already had -- weeks, on a stable PPPoE link.
      * apply_tariff_changes printed "Live sessions were dropped so the
        new speed applies now", which the cron path could not make true,
        so a downgraded customer kept the speed they had stopped paying
        for.
      * run_recurring_billing's suspensions, and
        check_suspension_enforcement --fix, the same.

    And nothing recorded it: RadiusAction rows are written by
    enforcement.apply_change, which never ran. The bug survived because
    the two commands a human runs and watches -- resync_radius --kick and
    apply_speed_policies -- call apply_change inline and were unaffected.

    atexit rather than making the threads non-daemon, because a
    non-daemon thread blocked on an unreachable router would hold the
    process open indefinitely; this waits, but only for `timeout`, and
    says so in the log if it gives up.
    """
    with _pending_lock:
        outstanding = list(_pending)
    if not outstanding:
        return

    logger.info("Waiting for %d background router push(es) to finish", len(outstanding))
    deadline = threading.TIMEOUT_MAX if timeout is None else timeout
    for thread in outstanding:
        thread.join(timeout=deadline)

    with _pending_lock:
        stuck = [t for t in _pending if t.is_alive()]
    if stuck:
        logger.error(
            "Gave up waiting for %d background router push(es) after %ss; "
            "those changes did not reach the router. Re-run "
            "`manage.py resync_radius --kick` to reconcile.",
            len(stuck), timeout,
        )


atexit.register(_drain_pending_work)


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

    Registered in `_pending` for the lifetime of the push, so
    _drain_pending_work can wait for it at process exit. Without that, a
    management command exits before any of this runs.
    """

    def _worker():
        try:
            work()
        except Exception:                                   # noqa: BLE001
            # Nothing upstream can act on this -- the request is long gone.
            logger.exception("Background router sync failed: %s", description)
        finally:
            with _pending_lock:
                _pending.discard(threading.current_thread())

    def _start():
        thread = threading.Thread(target=_worker, daemon=True)
        # Registered BEFORE start(), so a push can never finish and
        # deregister itself before the set has heard of it -- and so a
        # process exiting immediately after the save still sees it.
        with _pending_lock:
            _pending.add(thread)
        thread.start()

    transaction.on_commit(_start)


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
