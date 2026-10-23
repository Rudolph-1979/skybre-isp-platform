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


# ---------------------------------------------------------------------------
# Per-customer connection state for the Customers list
# ---------------------------------------------------------------------------
#
# Deliberately built on _live_usernames above rather than on a second
# definition of "online". This module's docstring makes the point for the
# call list and it holds here too: the Customers column, the Live Sessions
# view and the Recently offline page all have to agree, or support ends up
# arguing with the platform about whether a customer is up.
#
# THREE states, not two. "Offline" and "has no line here" are different
# facts and only one of them is worth acting on:
#
#   True   - a session is open and still reporting
#   False  - they have an active RADIUS line and it is not reporting
#   None   - no active RADIUS line at all, so this platform has no opinion
#
# Without the third state, every customer on someone else's network (a
# Vumatel or MTD line this platform doesn't authenticate) would read
# "Offline" forever -- a permanently red column that means nothing and
# trains people to ignore it.


def _radius_usernames_for(customers):
    """{radius_username: customer_id} for lines that are supposed to be up.

    Same filter as recently_offline: a suspended or terminated service being
    down is the system working, so it is not a line whose silence means
    anything.
    """
    from billing.models import Service
    from customers.models import Customer

    rows = (
        Service.objects.filter(customer__in=customers, status=Service.Status.ACTIVE)
        .exclude(radius_username="")
        .exclude(radius_username__isnull=True)
        .exclude(customer__status__in=Customer.OFF_STATUSES)
        .values("radius_username", "customer_id")
    )
    return {row["radius_username"]: row["customer_id"] for row in rows}


def connection_state(customers):
    """{customer_pk: {"online": bool, "last_seen": datetime|None}}.

    Three queries for the whole page regardless of how many customers --
    one for the services, one for the live check, one for the last-seen
    aggregate. Customers with no active RADIUS line are simply absent from
    the map, which is what the serializer turns into None.

    A customer holding two lines is online if EITHER is up, and their
    last_seen is the more recent of the two. Anything else would report a
    customer as down while they are sitting on a working second line.
    """
    if not customers:
        return {}

    owner_of = _radius_usernames_for(customers)
    if not owner_of:
        return {}

    usernames = list(owner_of.keys())
    live = _live_usernames(usernames)

    # Last time each login was seen at all -- an interim update, a session
    # start, or a clean stop. Open-but-silent rows are covered by
    # last_update, which is the case a `where acctstoptime is not null`
    # query would miss entirely (see this module's docstring).
    last_seen_by_username = {}
    for row in (
        RadAcct.objects.filter(username__in=usernames)
        .values("username")
        .annotate(
            last_stop=Max("acctstoptime"),
            last_update=Max("acctupdatetime"),
            last_start=Max("acctstarttime"),
        )
    ):
        stamps = [t for t in (row["last_stop"], row["last_update"], row["last_start"]) if t]
        if stamps:
            last_seen_by_username[row["username"]] = max(stamps)

    state = {}
    for username, customer_id in owner_of.items():
        entry = state.setdefault(customer_id, {"online": False, "last_seen": None, "lines": 0, "lines_offline": 0})
        entry["lines"] += 1
        if username in live:
            entry["online"] = True
        else:
            # Counted per line (8 Oct 2026): a client with one line up and
            # one down is Online, but the column says a line is down.
            entry["lines_offline"] += 1
        seen = last_seen_by_username.get(username)
        if seen and (entry["last_seen"] is None or seen > entry["last_seen"]):
            entry["last_seen"] = seen
    return state


def customer_ids_by_connection(queryset):
    """(online_ids, with_line_ids, line_down_ids) for filtering the
    Customers list. line_down_ids: at least one active line not up -- what
    the Offline filter shows (Rudolph, 8 Oct 2026: a client with one of two
    lines offline must be on it).

    Takes a queryset rather than a list so the service lookup runs as a
    subquery -- filtering 1,200 customers must not drag 1,200 rows into
    memory to decide which ones to show.

    `with_line_ids` is everyone who has an active RADIUS line at all;
    offline is that set minus the online one, and "no line" is everyone
    else. Returning the two raw sets rather than three keeps the arithmetic
    in one place at the call site. Per line: service_connection_state below.
    """
    from billing.models import Service
    from customers.models import Customer

    rows = (
        Service.objects.filter(customer__in=queryset, status=Service.Status.ACTIVE)
        .exclude(radius_username="")
        .exclude(radius_username__isnull=True)
        .exclude(customer__status__in=Customer.OFF_STATUSES)
        .values_list("radius_username", "customer_id")
    )
    owner_of = dict(rows)
    if not owner_of:
        return set(), set(), set()

    live = _live_usernames(list(owner_of.keys()))
    online_ids = {cid for username, cid in owner_of.items() if username in live}
    line_down_ids = {cid for username, cid in owner_of.items() if username not in live}
    return online_ids, set(owner_of.values()), line_down_ids


def service_connection_state(services):
    """{service_pk: {"online": bool, "last_seen": datetime|None}} for one
    page of services -- the client's Services table shows each line's own
    state (Rudolph, 8 Oct 2026: "Vuma WB is online, the Vuma 100/50 went
    offline").

    Same definition as connection_state, per line instead of per client:
    only ACTIVE lines with a RADIUS login on a client that isn't switched
    off get an entry; the rest are absent (the serializer says None), so a
    suspended or not-yet-live line is never painted Offline. Three queries
    for the page; the Dashboard totals are offline_service_counts below.
    """
    from customers.models import Customer

    login_of = {}
    for service in services:
        username = (getattr(service, "radius_username", "") or "").strip()
        if not username or service.status != "active":
            continue
        customer = getattr(service, "customer", None)
        if customer is not None and customer.status in Customer.OFF_STATUSES:
            continue
        login_of[service.pk] = username
    if not login_of:
        return {}

    usernames = list(set(login_of.values()))
    live = _live_usernames(usernames)
    last_seen = {}
    for row in (
        RadAcct.objects.filter(username__in=usernames)
        .values("username")
        .annotate(last_stop=Max("acctstoptime"), last_update=Max("acctupdatetime"), last_start=Max("acctstarttime"))
    ):
        stamps = [t for t in (row["last_stop"], row["last_update"], row["last_start"]) if t]
        if stamps:
            last_seen[row["username"]] = max(stamps)
    return {
        pk: {"online": username in live, "last_seen": last_seen.get(username)}
        for pk, username in login_of.items()
    }


def offline_service_counts():
    """(lines down, clients with a line down) across the whole book -- the
    Dashboard's Offline services tile (Rudolph, 8 Oct 2026: "Maybe the
    dashboard should show offline Services?"). Same lines and same rule as
    the Customers list Offline filter: an Active line with a RADIUS login on
    a client that isn't switched off, with no session heard from in the
    last 15 minutes -- however long ago it dropped, never-connected included.
    """
    from billing.models import Service
    from customers.models import Customer

    rows = list(
        Service.objects.filter(status=Service.Status.ACTIVE)
        .exclude(radius_username="")
        .exclude(radius_username__isnull=True)
        .exclude(customer__status__in=Customer.OFF_STATUSES)
        .values_list("radius_username", "customer_id")
    )
    if not rows:
        return 0, 0
    live = _live_usernames(list({username for username, _ in rows}))
    down = [(username, cid) for username, cid in rows if username not in live]
    return len(down), len({cid for _, cid in down})

