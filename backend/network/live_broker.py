"""Live per-session throughput, read only while somebody is watching.

The problem this replaces
------------------------
`poll_live_traffic` ran from cron every minute, opened a RouterOS API
connection, polled for 55 seconds and disconnected -- about 1,440 logins per
router per day, whether or not a single person was looking at a single
customer. A router's own log makes the waste plain: our username appears every
minute, forever, while a comparable platform's appears once an hour for six
seconds.

Six seconds an hour is not a background poller. It is a connection opened
because a staff member opened a page, and closed when they left it. That is
what this module does.

How it works
------------
A reader thread per device holds ONE connection open and reads every session's
counters about once a second, deriving rates and writing them to
RouterLiveRate. It starts when a viewer asks for live figures and stops within
seconds of the last one going away.

Two things make that safe on this deployment:

**Only one reader per device, cluster-wide.** Gunicorn runs several worker
processes and any of them may serve the request that first wants live data, so
without coordination each worker would start its own reader and open its own
connection. A Postgres advisory lock settles it: the worker that gets the lock
reads, the others don't, and everyone serves reads out of RouterLiveRate.

**The browser never holds a worker.** Server-sent events would have been the
obvious way to push per-second data, but a streaming response occupies a
gunicorn worker for its entire life and this box runs three SYNC workers --
three people watching live graphs would have blocked the whole platform. So
viewers poll a cheap endpoint that reads the last value, and the expensive
part (the router connection) lives here, off the request path entirely.
"""
import logging
import threading
import time

from django.db import close_old_connections, connection
from django.utils import timezone

from network import mikrotik
from network.models import Device

logger = logging.getLogger(__name__)

# How often to read the router while somebody is watching.
POLL_SECONDS = 1.0

# A viewer's interest is good for this long. Comfortably more than the
# frontend's poll interval, so one dropped request doesn't tear the connection
# down and rebuild it a second later -- that would put exactly the login churn
# in the router log that this exists to remove.
INTEREST_TTL_SECONDS = 20

# Belt and braces: no reader runs longer than this without renewed interest,
# whatever the interest table says. A stuck row must not hold a router
# connection open indefinitely.
MAX_RUN_SECONDS = 60 * 60

# Postgres advisory locks are keyed on a bigint. Namespaced so this can never
# collide with another feature's lock.
_LOCK_NAMESPACE = 0x5CB7  # "SKBR"

_threads = {}
_threads_lock = threading.Lock()


def register_interest(device_id):
    """Record that somebody wants this device's live figures, and make sure a
    reader is running. Returns immediately -- the caller must not wait on a
    router."""
    from radiusauth.models import LiveTrafficInterest

    LiveTrafficInterest.objects.update_or_create(
        device_id=device_id, defaults={"last_requested_at": timezone.now()}
    )
    _ensure_reader(device_id)


def _ensure_reader(device_id):
    with _threads_lock:
        existing = _threads.get(device_id)
        if existing and existing.is_alive():
            return
        thread = threading.Thread(
            target=_run_reader, args=(device_id,), daemon=True,
            name=f"live-traffic-{device_id}",
        )
        _threads[device_id] = thread
        thread.start()


def _interest_is_fresh(device_id):
    from radiusauth.models import LiveTrafficInterest

    cutoff = timezone.now() - timezone.timedelta(seconds=INTEREST_TTL_SECONDS)
    return LiveTrafficInterest.objects.filter(
        device_id=device_id, last_requested_at__gte=cutoff
    ).exists()


def _try_lock(device_id):
    """True if this process now owns the reader slot for the device.

    pg_try_advisory_lock never blocks -- it either takes the lock or says no,
    which is exactly right here: a second worker finding the lock taken should
    do nothing at all rather than queue up behind the first.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s, %s)", [_LOCK_NAMESPACE, device_id])
        return bool(cursor.fetchone()[0])


def _unlock(device_id):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s, %s)", [_LOCK_NAMESPACE, device_id])
    except Exception:                                          # noqa: BLE001
        # The connection may already be gone if the process is shutting down.
        # The lock is session-scoped, so Postgres releases it anyway.
        pass


def _run_reader(device_id):
    """Hold one connection open and publish rates until interest dries up."""
    close_old_connections()
    if not _try_lock(device_id):
        # Another worker is already reading this router. Nothing to do -- the
        # rates it publishes are read from the database by every worker.
        return

    started = time.monotonic()
    try:
        device = Device.objects.filter(pk=device_id, api_enabled=True).first()
        if device is None:
            return

        with mikrotik.get_device_lock(device.pk):
            # The same per-device lock the rest of the live-API features take,
            # so a staff member clicking "Read now" or a queue sync can never
            # contend with this on the same router.
            try:
                with mikrotik.api_connection(device, timeout=15) as api:
                    _read_loop(device, api, started)
            except mikrotik.MikrotikError as exc:
                logger.warning("Live traffic reader for %s stopped: %s", device, exc)
    except Exception:                                          # noqa: BLE001
        logger.exception("Live traffic reader for device %s crashed", device_id)
    finally:
        _unlock(device_id)
        close_old_connections()
        with _threads_lock:
            _threads.pop(device_id, None)


def _read_loop(device, api, started):
    from radiusauth.models import RouterLiveRate

    previous = {}
    last_at = None

    while True:
        if time.monotonic() - started > MAX_RUN_SECONDS:
            logger.info("Live traffic reader for %s hit its time limit; closing.", device)
            return
        if not _interest_is_fresh(device.pk):
            logger.info("Nobody is watching %s any more; closing the connection.", device)
            return

        now = timezone.now()
        sessions = mikrotik.read_session_traffic(api)

        elapsed = (now - last_at).total_seconds() if last_at else 0
        rows = []
        for username, counters in sessions.items():
            rx, tx = counters["rx_byte"], counters["tx_byte"]
            before = previous.get(username)
            # A first sighting, or counters that went backwards because the
            # session restarted, gives no usable rate -- reporting one would
            # divide a whole session's traffic by a single interval.
            if before and elapsed > 0 and rx >= before[0] and tx >= before[1]:
                upload_bps = int((rx - before[0]) * 8 / elapsed)
                download_bps = int((tx - before[1]) * 8 / elapsed)
            else:
                upload_bps = download_bps = 0
            previous[username] = (rx, tx)
            rows.append(
                RouterLiveRate(
                    username=username, device=device, interface=counters["interface"],
                    last_rx_byte=rx, last_tx_byte=tx,
                    download_bps=download_bps, upload_bps=upload_bps, sampled_at=now,
                )
            )

        if rows:
            RouterLiveRate.objects.bulk_create(
                rows,
                update_conflicts=True,
                update_fields=[
                    "device", "interface", "last_rx_byte", "last_tx_byte",
                    "download_bps", "upload_bps", "sampled_at",
                ],
                unique_fields=["username"],
            )
        last_at = now

        # Sleep the remainder of the interval rather than a flat second, so a
        # slow router read doesn't stretch the cadence and understate the rate.
        spent = (timezone.now() - now).total_seconds()
        time.sleep(max(0.1, POLL_SECONDS - spent))
