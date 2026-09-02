"""Customers whose line went down recently and has not come back.

The point is a call list, not a statistic. So the definition is narrower than
"not currently connected", in three ways that each remove people it would be
embarrassing or pointless to phone:

**Anyone we cut off ourselves is excluded.** A suspended customer is offline
because we suspended them. Putting them on a "can we help?" list would have
support ringing people to ask why their internet is off, which we know.

**Anyone who already came back is excluded.** A router reboot or a two-minute
drop is not an outage worth a phone call, and a list full of them is a list
nobody reads.

**A session that died without saying so still counts.** A CPE that loses power
never sends an Accounting-Stop, so radacct shows the session open forever.
Those are the genuinely broken lines -- the ones most worth a call -- and a
naive "where acctstoptime is not null" query misses every one of them.

Terminate cause is carried through because it changes what you say when they
answer. Lost-Carrier is a line fault; User-Request usually means they switched
the router off themselves.
"""
from django.db.models import Count, Max, Q
from django.utils import timezone

from .models import RadAcct
from .usage import LIVE_SESSION_STALE_SECONDS

DEFAULT_HOURS = 24

# What RouterOS/FreeRADIUS put in acctterminatecause, in words that mean
# something to whoever is about to pick up the phone. Anything not listed is
# shown as-is rather than hidden -- an unfamiliar cause is information too.
TERMINATE_CAUSES = {
    "User-Request": "They disconnected (often the router being switched off)",
    "Lost-Carrier": "The line dropped — usually a fault worth chasing",
    "Lost-Service": "The service stopped",
    "Idle-Timeout": "Idle too long",
    "Session-Timeout": "Session time limit",
    "Admin-Reset": "Reset by an administrator",
    "Admin-Reboot": "The router was rebooted",
    "Port-Error": "Port error on the router",
    "NAS-Error": "Router error",
    "NAS-Request": "Ended by the router",
    "NAS-Reboot": "The router rebooted",
    "Port-Unneeded": "Port no longer needed",
    "Host-Request": "Ended by request",
}


def _live_usernames(usernames):
    """Logins with a session that is open AND still reporting.

    The heartbeat check is the whole point -- see the module docstring. Uses
    the same staleness window as everything else that asks "is this live", so
    two screens can never disagree about whether one customer is online.
    """
    if not usernames:
        return set()
    cutoff = timezone.now() - timezone.timedelta(seconds=LIVE_SESSION_STALE_SECONDS)
    rows = RadAcct.objects.filter(
        username__in=usernames, acctstoptime__isnull=True
    ).values("username", "acctupdatetime", "acctstarttime")
    live = set()
    for row in rows:
        heartbeat = row["acctupdatetime"] or row["acctstarttime"]
        if heartbeat and heartbeat >= cutoff:
            live.add(row["username"])
    return live


def recently_offline(customers, hours=DEFAULT_HOURS, now=None):
    """Customers seen online within `hours` who are not online now.

    Two queries regardless of how many customers: one for the sessions, one
    for the live check. Both are keyed on the same username list.
    """
    from billing.models import Service
    from customers.models import Customer

    now = now or timezone.now()
    cutoff = now - timezone.timedelta(hours=hours)

    # Only lines that are SUPPOSED to be up. A suspended or terminated
    # service being offline is the system working, not a fault.
    services = (
        Service.objects.filter(customer__in=customers, status=Service.Status.ACTIVE)
        .exclude(radius_username="")
        .exclude(radius_username__isnull=True)
        .exclude(customer__status__in=Customer.OFF_STATUSES)
        .select_related("customer")
        .values("radius_username", "customer_id")
    )
    owner_of = {row["radius_username"]: row["customer_id"] for row in services}
    if not owner_of:
        return []

    usernames = list(owner_of.keys())
    live = _live_usernames(usernames)
    candidates = [u for u in usernames if u not in live]
    if not candidates:
        return []

    # Seen at all inside the window -- either the session ended in it, or it
    # is one of the "open but silent" rows whose last heartbeat falls in it.
    rows = (
        RadAcct.objects.filter(username__in=candidates)
        .filter(Q(acctstoptime__gte=cutoff) | Q(acctstoptime__isnull=True, acctupdatetime__gte=cutoff))
        .values("username")
        .annotate(
            last_stop=Max("acctstoptime"),
            last_update=Max("acctupdatetime"),
            last_start=Max("acctstarttime"),
        )
    )

    # The most recent session per login, for the terminate cause and the
    # address they last held. Cheap: only for logins already known to be down.
    detail = {}
    for row in (
        RadAcct.objects.filter(username__in=[r["username"] for r in rows])
        .order_by("username", "-acctstarttime")
        .values("username", "acctterminatecause", "framedipaddress", "acctstoptime", "acctstarttime")
    ):
        detail.setdefault(row["username"], row)

    # Drops inside the window, so a line that flapped ten times reads
    # differently from one that dropped once and stayed down.
    drop_counts = {
        row["username"]: row["n"]
        for row in RadAcct.objects.filter(username__in=candidates, acctstoptime__gte=cutoff)
        .values("username")
        .annotate(n=Count("radacctid"))
    }

    by_id = {c.pk: c for c in customers}
    results = []
    for row in rows:
        username = row["username"]
        customer = by_id.get(owner_of.get(username))
        if customer is None:
            continue

        last_seen = max(
            [t for t in (row["last_stop"], row["last_update"], row["last_start"]) if t],
            default=None,
        )
        if last_seen is None or last_seen < cutoff:
            continue

        info = detail.get(username, {})
        cause = info.get("acctterminatecause") or ""
        # An open row with a stale heartbeat has no terminate cause, because
        # nothing ever told us it ended. Say that rather than leaving a blank.
        clean_stop = bool(info.get("acctstoptime"))
        results.append({
            "customer": customer.pk,
            "customer_ref": customer.customer_id,
            "full_name": customer.full_name,
            "phone": customer.phone,
            "email": customer.email,
            "username": username,
            "last_seen": last_seen,
            "offline_seconds": int((now - last_seen).total_seconds()),
            "last_ip": info.get("framedipaddress"),
            "terminate_cause": cause,
            "terminate_reason": (
                TERMINATE_CAUSES.get(cause, cause) if clean_stop
                else "Stopped reporting without disconnecting — usually power or a dead CPE"
            ),
            "clean_disconnect": clean_stop,
            "drops_in_period": drop_counts.get(username, 0),
        })

    # Longest down first: whoever has been off the longest is the one nobody
    # has called yet.
    results.sort(key=lambda r: (-r["offline_seconds"], r["full_name"]))
    return results
