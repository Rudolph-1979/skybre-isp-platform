"""Making a change to a Service actually reach the customer's live session.

The rule this file encodes:

    A change to the customer's SPEED is a CoA. Their line does not drop.
    A change to the customer's ADDRESS is a disconnect. Their line drops once.

That distinction is the whole point. A tariff upgrade used to cost the
customer a reconnect, which is absurd -- they are being given something, and
the delivery mechanism was to cut them off. Suspension and restoration DO have
to re-address the session (customer pool <-> walled garden), and a live PPPoE
session cannot be re-addressed in place, so those still take one disconnect.

Transport order, and why there is a fallback at all:

    1. RFC 5176 dynamic authorization, UDP 3799 -- the mechanism designed for
       this. Needs `/radius incoming set accept=yes` on the router.
    2. The RouterOS API, /ppp/active/remove -- the old path. Kept because it
       is a genuinely independent route: it fails for different reasons (TCP
       8728, API credentials) than CoA does (UDP 3799, shared secret), so one
       covers the other's outage. It can only disconnect, never re-rate.

And the part that matters most: **every attempt is recorded**. The previous
implementation swallowed every failure into a log line in a daemon thread,
after the HTTP response had gone out. The platform reported success whether or
not anything reached the router, which is why a broken enforcement path
survived weeks of use. A RadiusAction row is written for every attempt,
including the ones that did nothing, and the reason is in plain words.
"""
import logging

from django.utils import timezone

from . import dynauth
from .models import RadAcct, RadiusAction, RadiusNasClient
from .signals import _mikrotik_rate_limit

logger = logging.getLogger(__name__)

# An accounting row whose last interim update is older than this is treated as
# a session that died without FreeRADIUS being told -- a router reboot, a lost
# Accounting-Stop. Matches radiusauth.usage.LIVE_SESSION_STALE_SECONDS: two
# different answers to "is this session live" would be a bug in waiting.
STALE_SESSION_SECONDS = 900


def find_live_session(username):
    """The customer's current accounting row, or None.

    Newest open session wins. A customer should only ever have one, but a
    router that restarted without sending Accounting-Stop leaves the old row
    open forever, and targeting a CoA at a dead session id gets a
    "Session context not found" NAK rather than the change they asked for.
    """
    if not username:
        return None
    cutoff = timezone.now() - timezone.timedelta(seconds=STALE_SESSION_SECONDS)
    for row in (
        RadAcct.objects.filter(username=username, acctstoptime__isnull=True)
        .order_by("-acctstarttime")[:5]
    ):
        heartbeat = row.acctupdatetime or row.acctstarttime
        if heartbeat and heartbeat >= cutoff:
            return row
    return None


def _secret_for(nas_ip):
    """The shared secret this NAS was configured with, or None.

    Matched on the NAS's own IP as it appears in accounting, which is the
    address the router sources RADIUS traffic from -- and therefore the
    address RouterOS expects dynamic-authorization packets to come back to.
    """
    client = RadiusNasClient.objects.filter(ip_address=nas_ip, is_active=True).first()
    return client.secret if client else None


def _record(service, action, transport, ok, detail):
    RadiusAction.objects.create(
        service=service,
        username=(service.radius_username or "")[:255],
        action=action,
        transport=transport,
        ok=ok,
        detail=detail[:500],
    )
    log = logger.info if ok else logger.warning
    log("RADIUS %s via %s for %s: %s", action, transport, service.radius_username, detail)
    return ok


def apply_change(service, reason, allow_disconnect_fallback=True):
    """Push a Service change out to the customer's live session.

    `reason` is one of "tariff" (speed only) or "status" (address changes).
    Returns True when the router confirmed it. Never raises: this runs from a
    post-commit background thread where nothing upstream can act on an
    exception -- but unlike before, the failure lands in RadiusAction where
    staff can see it.

    `allow_disconnect_fallback` decides what happens when CoA cannot be
    delivered. Dropping the session is a reasonable last resort for a
    change somebody just made and is watching for -- a speed upgrade they
    have paid for is worth one reconnect.

    It is NOT reasonable for the scheduled speed-policy run. That fires
    for every connected customer at a window boundary, so a CoA that fails
    network-wide -- a wrong shared secret, UDP 3799 blocked -- would
    disconnect the entire customer base at 22:00 in order to deliver a
    boost nobody asked for. The blast radius of the fallback is the whole
    book, and the thing being delivered is optional. So that caller passes
    False and accepts that the new speed waits for the next reconnect.
    """
    username = (service.radius_username or "").strip()
    if not username:
        return _record(service, "none", "none", True, "No RADIUS username on this service; nothing to push.")

    session = find_live_session(username)
    if session is None:
        # Not a failure. The new answer is already in radcheck/radreply, so
        # whenever they next connect they get it. Recorded anyway, because
        # "there was nothing to change" and "we couldn't change it" look
        # identical from the outside and have completely different fixes.
        return _record(
            service, "none", "none", True,
            "No live session, so nothing to push -- the new settings apply at their next connection.",
        )

    nas_ip = session.nasipaddress
    secret = _secret_for(nas_ip)

    wants_rate_only = reason == "tariff" and service.status == service.Status.ACTIVE
    action = "coa_rate" if wants_rate_only else "disconnect"

    if secret:
        try:
            if wants_rate_only:
                rate = _mikrotik_rate_limit(service)
                dynauth.change_rate_limit(
                    nas_ip, secret, username, rate,
                    acct_session_id=session.acctsessionid,
                    framed_ip=session.framedipaddress,
                    port=dynauth.coa_port(),
                )
                # Remember what the router was last told, so the
                # scheduled policy run pushes only what CHANGED rather
                # than re-sending an identical limit every few minutes.
                if service.pk:
                    type(service).objects.filter(pk=service.pk).update(last_pushed_rate_limit=rate)
                return _record(
                    service, action, "coa", True,
                    f"Speed changed to {rate} on the live session; the customer stayed connected.",
                )
            dynauth.disconnect(
                nas_ip, secret, username,
                acct_session_id=session.acctsessionid,
                framed_ip=session.framedipaddress,
                port=dynauth.coa_port(),
            )
            return _record(
                service, action, "coa", True,
                "Session disconnected via RFC 5176; they reconnect on the new settings within seconds.",
            )
        except dynauth.DynAuthError as exc:
            coa_error = str(exc)
    else:
        coa_error = (
            f"No active NAS record for {nas_ip}, so there's no shared secret to sign with. "
            "Add it under Networking -> RADIUS clients."
        )

    if not allow_disconnect_fallback:
        # Deliberately stops here. See allow_disconnect_fallback in the
        # docstring: the fallback is a disconnect, and dropping a customer
        # to hand them an off-peak boost is worse than them keeping their
        # current speed until they next reconnect.
        return _record(
            service, action, "none", False,
            f"CoA didn't work ({coa_error}) -- left alone rather than dropping the session. "
            "The new speed applies at their next reconnect.",
        )

    # --- fallback: the RouterOS API ---------------------------------------
    # A rate change can't be done this way -- the API has no equivalent of
    # CoA -- so it degrades to a disconnect, which at least delivers the new
    # speed at the cost of one reconnect. Said plainly in the record, because
    # a customer being dropped is something staff should be able to explain.
    from network import router_sync

    try:
        dropped = router_sync.disconnect_service_sessions(service)
    except Exception as exc:                                  # noqa: BLE001
        dropped = 0
        coa_error = f"{coa_error} RouterOS API also failed: {exc}"

    if dropped:
        note = "Speed change delivered by dropping the session instead" if wants_rate_only else "Session dropped"
        return _record(
            service, action, "api", True,
            f"CoA didn't work ({coa_error}) -- {note} via the RouterOS API.",
        )

    return _record(
        service, action, "none", False,
        f"Couldn't reach the live session. CoA: {coa_error} "
        "The RouterOS API didn't drop anything either. The customer is still running on their OLD settings.",
    )
