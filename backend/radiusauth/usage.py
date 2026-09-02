"""Turning RADIUS accounting into usage figures.

Reads three tables, none of which this module ever writes:

* `radacct` -- what FreeRADIUS records when the NAS reports. The source of
  truth for totals and therefore for anything billing-adjacent.
* `SessionUsageSnapshot` -- a rate derived from those counters. Costs
  nothing, but it is an AVERAGE over the NAS's reporting interval.
* `RouterLiveRate` -- what the line is doing right now, polled from the
  router itself by `poll_live_traffic`.

For a rate, live is preferred and the average is the fallback, with
`rate_source` on each session saying which one you got. Totals always come
from accounting: the router's counters restart with every session, so they
can measure speed but never consumption.

Two deliberate choices worth knowing:

1. **Totals are summed in the database.** The dashboard once counted rows
   in the browser and silently under-reported once the data outgrew one
   page of results; usage figures are worse to get wrong than a chart, so
   the aggregation stays server-side.

2. **A session is counted in the month it STARTED.** A session running
   from the 28th to the 3rd puts all of its bytes in the earlier month.
   Splitting it properly is impossible from this data -- radacct records
   cumulative totals, not a timeline -- so the approximation is stated
   rather than hidden. It matters only for always-on connections that
   never reconnect across a month boundary, and a monthly PPPoE
   reconnect makes it moot.
"""
from django.db.models import Sum
from django.utils import timezone

from .models import RadAcct, RouterLiveRate, SessionUsageSnapshot


def month_bounds(year, month):
    """First instant of the given month, and of the one after it."""
    start = timezone.datetime(year, month, 1, tzinfo=timezone.get_current_timezone())
    if month == 12:
        end = timezone.datetime(year + 1, 1, 1, tzinfo=timezone.get_current_timezone())
    else:
        end = timezone.datetime(year, month + 1, 1, tzinfo=timezone.get_current_timezone())
    return start, end


def usernames_for_customer(customer):
    """The RADIUS logins belonging to this customer -- one per service."""
    return [
        u
        for u in customer.services.exclude(radius_username="")
        .exclude(radius_username__isnull=True)
        .values_list("radius_username", flat=True)
    ]


def period_totals(usernames, start, end):
    """Bytes down/up across every session these logins started in the
    period. Down/up are named from the CUSTOMER's point of view, which is
    the reverse of RADIUS's: the NAS reports `acctinputoctets` as traffic
    coming IN to itself, i.e. the customer's upload."""
    if not usernames:
        return {"download_bytes": 0, "upload_bytes": 0, "total_bytes": 0, "sessions": 0}

    qs = RadAcct.objects.filter(
        username__in=usernames, acctstarttime__gte=start, acctstarttime__lt=end
    )
    agg = qs.aggregate(up=Sum("acctinputoctets"), down=Sum("acctoutputoctets"))
    down = agg["down"] or 0
    up = agg["up"] or 0
    return {
        "download_bytes": down,
        "upload_bytes": up,
        "total_bytes": down + up,
        "sessions": qs.count(),
    }


# A session with no accounting update for this long is treated as gone even
# though radacct still shows it open. Accounting-Stop packets get lost --
# a CPE that loses power never sends one -- so radacct accumulates sessions
# that look live forever. Observed on the bench: two "current" sessions for
# one user on the same IP, 37 minutes apart. Without this the customer page
# claims two connections and double-counts throughput.
#
# Wants to be comfortably longer than the NAS's interim-update interval.
LIVE_SESSION_STALE_SECONDS = 900

# How recent a router-polled rate has to be to count as "now". Should be a
# small multiple of poll_live_traffic's --interval, so a missed poll doesn't
# blank the figure but a stopped poller does.
ROUTER_RATE_STALE_SECONDS = 45


def live_state(usernames):
    """Current sessions and their throughput, from the derived snapshots.

    At most one session per login: RADIUS gives no way to distinguish "still
    connected" from "died without telling us", so the newest open session
    wins and older ones are treated as abandoned.

    `input_bps`/`output_bps` on the snapshot are in RADIUS's direction (in
    = towards the NAS = the customer's upload), so they are flipped here to
    read naturally for a customer.
    """
    if not usernames:
        return []

    cutoff = timezone.now() - timezone.timedelta(seconds=LIVE_SESSION_STALE_SECONDS)
    candidates = list(
        RadAcct.objects.filter(username__in=usernames, acctstoptime__isnull=True)
        .order_by("-acctstarttime")
        .values(
            "acctuniqueid", "username", "acctstarttime", "acctupdatetime", "framedipaddress",
            "acctinputoctets", "acctoutputoctets", "callingstationid",
        )
    )

    sessions = []
    seen = set()
    for row in candidates:
        if row["username"] in seen:
            # An older open session for a login we've already got. The NAS
            # only serves one at a time (only-one=yes on the PPP profile),
            # so this is a leftover.
            continue
        # Fall back to the start time when a session has had no interim yet.
        last_seen = row["acctupdatetime"] or row["acctstarttime"]
        if last_seen is not None and last_seen < cutoff:
            continue
        seen.add(row["username"])
        sessions.append(row)
    snaps = {
        s.acctuniqueid: s
        for s in SessionUsageSnapshot.objects.filter(
            acctuniqueid__in=[s["acctuniqueid"] for s in sessions]
        )
    }
    # Live rates straight from the router, if the poller has run recently.
    # These are what the line is doing NOW; the accounting-derived figures
    # above are averages over the NAS's reporting interval, so a speed test
    # reads low there. Prefer live and fall back to the average, which is
    # also what happens for a router with no API access configured.
    live_cutoff = timezone.now() - timezone.timedelta(seconds=ROUTER_RATE_STALE_SECONDS)
    live = {
        r.username: r
        for r in RouterLiveRate.objects.filter(
            username__in=[s["username"] for s in sessions], sampled_at__gte=live_cutoff
        )
    }

    out = []
    for s in sessions:
        snap = snaps.get(s["acctuniqueid"])
        live_row = live.get(s["username"])
        if live_row is not None:
            down_bps, up_bps = live_row.download_bps, live_row.upload_bps
            measured_at, source = live_row.sampled_at, "router"
        elif snap is not None:
            down_bps, up_bps = snap.output_bps, snap.input_bps
            measured_at, source = snap.sampled_at, "accounting"
        else:
            # Neither source has anything yet. Say so rather than
            # presenting a confident zero.
            down_bps = up_bps = 0
            measured_at, source = None, None

        out.append({
            "username": s["username"],
            "started_at": s["acctstarttime"],
            "ip_address": s["framedipaddress"],
            "mac_address": s["callingstationid"],
            "download_bytes": s["acctoutputoctets"] or 0,
            "upload_bytes": s["acctinputoctets"] or 0,
            "download_bps": down_bps,
            "upload_bps": up_bps,
            "rate_measured_at": measured_at,
            # "router" = live, to the second. "accounting" = an average over
            # the NAS's interim interval. Surfaced so the UI can be honest
            # about which it is showing.
            "rate_source": source,
        })
    return out


def parse_anchor(raw):
    """A YYYY-MM-DD query param as a date, or today. Raises ValueError."""
    if not raw:
        return timezone.localdate()
    return datetime.datetime.strptime(raw, "%Y-%m-%d").date()


def customer_usage(customer, year=None, month=None, period=None, anchor=None):
    """Everything a usage view needs for one customer.

    `period` adds the accumulated day/week/month/year series. The month-to-date
    totals above it are left exactly as they were -- they come from radacct and
    cover history from before accumulation started, so replacing them would
    blank out figures the page has always shown.
    """
    now = timezone.localtime()
    year = year or now.year
    month = month or now.month
    start, end = month_bounds(year, month)

    usernames = usernames_for_customer(customer)
    totals = period_totals(usernames, start, end)

    # The cap is per service; a customer with two services has two caps.
    # Summing them is the only sensible single number, and 0/None on any
    # service means uncapped, which makes the whole total uncapped.
    caps = [
        s.tariff.data_cap_gb
        for s in customer.services.select_related("tariff").all()
        if s.tariff_id
    ]
    if not caps or any(c in (None, 0) for c in caps):
        cap_bytes = None
    else:
        cap_bytes = sum(caps) * 1024 ** 3

    payload = {
        "customer_id": customer.pk,
        "customer_name": customer.full_name,
        "period": {"year": year, "month": month, "start": start, "end": end},
        "cap_bytes": cap_bytes,
        **totals,
        "live_sessions": live_state(usernames),
    }

    if period:
        payload["series"] = usage_series(usernames, period, anchor)
        # Where the accumulated record actually begins. On screen because an
        # empty year view is otherwise indistinguishable from a customer who
        # used nothing, and there is no way to reconstruct earlier hours.
        payload["measuring_since"] = measurement_start()
    return payload


# ---------------------------------------------------------------------------
# Accumulated usage: day / week / month / year
# ---------------------------------------------------------------------------
#
# Everything above this line reads radacct, where a session's bytes are a
# single cumulative number stamped with the session's START. That is why the
# monthly figure counts a session in the month it began, and why it cannot
# answer a per-day question at all.
#
# Everything below reads UsageBucket, which is accumulated hour by hour as the
# counters advance (see sample_session_usage). Attribution is correct by
# construction there: bytes land in the hour they were reported.

import calendar
import datetime

from django.db.models.functions import TruncDay, TruncHour, TruncMonth

from .models import UsageBucket

# Which bucket each period draws, and how fine the points are.
PERIODS = ("day", "week", "month", "year")


def bank_usage(username, when, download, upload):
    """Add bytes to the hour containing `when`. Safe to call repeatedly.

    Guards against negatives rather than trusting the caller: a counter that
    goes backwards is a real event on a NAS that restarts, and a negative
    delta banked here would silently subtract traffic a customer really used.

    The write is an UPDATE-or-INSERT against a unique (username, hour) row, so
    two samplers racing in the same minute add up instead of one overwriting
    the other.
    """
    if not username:
        return
    download = max(int(download or 0), 0)
    upload = max(int(upload or 0), 0)
    if not download and not upload:
        return

    hour = when.astimezone(datetime.timezone.utc).replace(minute=0, second=0, microsecond=0)
    bucket, created = UsageBucket.objects.get_or_create(
        username=username, bucket_start=hour,
        defaults={"download_bytes": download, "upload_bytes": upload},
    )
    if not created:
        # F() rather than read-modify-write: two samplers overlapping would
        # otherwise each read the same starting value and one would clobber
        # the other's addition.
        from django.db.models import F

        UsageBucket.objects.filter(pk=bucket.pk).update(
            download_bytes=F("download_bytes") + download,
            upload_bytes=F("upload_bytes") + upload,
        )


def period_bounds(period, anchor=None):
    """(start, end, label) for a period containing `anchor` (a local date).

    End is exclusive. Weeks run Monday to Sunday, which is what a South
    African billing conversation means by "this week".
    """
    tz = timezone.get_current_timezone()
    anchor = anchor or timezone.localdate()

    if period == "day":
        start_date, end_date = anchor, anchor + datetime.timedelta(days=1)
        label = anchor.strftime("%d %b %Y")
    elif period == "week":
        start_date = anchor - datetime.timedelta(days=anchor.weekday())
        end_date = start_date + datetime.timedelta(days=7)
        label = f"{start_date:%d %b} – {start_date + datetime.timedelta(days=6):%d %b %Y}"
    elif period == "month":
        start_date = anchor.replace(day=1)
        last = calendar.monthrange(anchor.year, anchor.month)[1]
        end_date = start_date + datetime.timedelta(days=last)
        label = anchor.strftime("%B %Y")
    elif period == "year":
        start_date = datetime.date(anchor.year, 1, 1)
        end_date = datetime.date(anchor.year + 1, 1, 1)
        label = str(anchor.year)
    else:
        raise ValueError(f"Unknown period {period!r}; expected one of {', '.join(PERIODS)}.")

    start = timezone.make_aware(datetime.datetime.combine(start_date, datetime.time.min), tz)
    end = timezone.make_aware(datetime.datetime.combine(end_date, datetime.time.min), tz)
    return start, end, label


def _truncation_for(period):
    # A day is shown by hour, a year by month, everything between by day.
    return {"day": (TruncHour, "hour"), "year": (TruncMonth, "month")}.get(period, (TruncDay, "day"))


def _walk(start, end, step):
    """Every point in the range, including the empty ones.

    Gaps have to be present as zeros rather than absent: a chart that simply
    omits a quiet Tuesday draws Monday next to Wednesday and silently
    compresses the timeline.
    """
    tz = timezone.get_current_timezone()
    points, cursor = [], start
    while cursor < end:
        points.append(cursor)
        if step == "hour":
            cursor = cursor + datetime.timedelta(hours=1)
        elif step == "day":
            cursor = timezone.make_aware(
                datetime.datetime.combine(
                    (cursor.astimezone(tz).date() + datetime.timedelta(days=1)), datetime.time.min
                ), tz,
            )
        else:
            local = cursor.astimezone(tz).date()
            year, month = (local.year + 1, 1) if local.month == 12 else (local.year, local.month + 1)
            cursor = timezone.make_aware(
                datetime.datetime.combine(datetime.date(year, month, 1), datetime.time.min), tz
            )
    return points


def _point_label(when, step):
    local = timezone.localtime(when)
    if step == "hour":
        return local.strftime("%H:00")
    if step == "month":
        return local.strftime("%b")
    return local.strftime("%d %b")


def usage_series(usernames, period="month", anchor=None):
    """Totals plus a point-per-interval series for one period.

    Returns zero-filled points, so the caller can plot the result directly
    without having to know which intervals had no traffic.
    """
    start, end, label = period_bounds(period, anchor)
    trunc, step = _truncation_for(period)

    rows = {}
    if usernames:
        aggregated = (
            UsageBucket.objects.filter(
                username__in=usernames, bucket_start__gte=start, bucket_start__lt=end
            )
            .annotate(point=trunc("bucket_start"))
            .values("point")
            .annotate(down=Sum("download_bytes"), up=Sum("upload_bytes"))
        )
        # Keyed on the truncated instant so a customer's two services land on
        # the same point instead of drawing two series.
        rows = {r["point"]: r for r in aggregated}

    points, total_down, total_up = [], 0, 0
    for when in _walk(start, end, step):
        row = rows.get(when)
        down = (row or {}).get("down") or 0
        up = (row or {}).get("up") or 0
        total_down += down
        total_up += up
        points.append({
            "at": when,
            "label": _point_label(when, step),
            "download_bytes": down,
            "upload_bytes": up,
            "total_bytes": down + up,
        })

    return {
        "period": period,
        "period_label": label,
        "start": start,
        "end": end,
        "interval": step,
        "download_bytes": total_down,
        "upload_bytes": total_up,
        "total_bytes": total_down + total_up,
        "points": points,
    }


def measurement_start():
    """When accumulation actually began, or None if it never has.

    Shown on screen because a year view that is empty before a certain date is
    otherwise indistinguishable from a customer who used nothing. There is no
    way to reconstruct earlier hours from accounting data, so the honest move
    is to say where the record starts.
    """
    first = UsageBucket.objects.order_by("bucket_start").values_list("bucket_start", flat=True).first()
    return first


def usage_report(customers, period="month", anchor=None, limit=None):
    """Totals per customer for one period, heaviest first.

    Built for "who is hammering the network this week" and "who is near their
    cap", so it reports the cap alongside and the percentage of it used.

    Two queries regardless of how many customers: one to map every RADIUS
    login back to its customer, one to sum the buckets. The obvious
    implementation -- usage_series per customer -- is one query each and turns
    a 500-customer report into 500 round trips.
    """
    from billing.models import Service

    start, end, label = period_bounds(period, anchor)

    services = (
        Service.objects.filter(customer__in=customers)
        .exclude(radius_username="")
        .exclude(radius_username__isnull=True)
        .select_related("customer", "tariff")
        .values("radius_username", "customer_id", "tariff__data_cap_gb")
    )

    owner_of = {}
    caps = {}
    for row in services:
        owner_of[row["radius_username"]] = row["customer_id"]
        # Uncapped anywhere means uncapped overall -- a customer with a capped
        # line and an uncapped one cannot meaningfully be "80% used".
        cap = row["tariff__data_cap_gb"]
        if row["customer_id"] in caps and (caps[row["customer_id"]] is None or not cap):
            caps[row["customer_id"]] = None
        else:
            caps[row["customer_id"]] = (caps.get(row["customer_id"]) or 0) + cap if cap else None

    if not owner_of:
        return {"period": period, "period_label": label, "start": start, "end": end, "results": []}

    totals = {}
    buckets = (
        UsageBucket.objects.filter(
            username__in=owner_of.keys(), bucket_start__gte=start, bucket_start__lt=end
        )
        .values("username")
        .annotate(down=Sum("download_bytes"), up=Sum("upload_bytes"))
    )
    for row in buckets:
        customer_id = owner_of.get(row["username"])
        if customer_id is None:
            continue
        entry = totals.setdefault(customer_id, {"download_bytes": 0, "upload_bytes": 0})
        entry["download_bytes"] += row["down"] or 0
        entry["upload_bytes"] += row["up"] or 0

    by_id = {c.pk: c for c in customers}
    results = []
    for customer_id, entry in totals.items():
        customer = by_id.get(customer_id)
        if customer is None:
            continue
        total = entry["download_bytes"] + entry["upload_bytes"]
        cap_gb = caps.get(customer_id)
        cap_bytes = cap_gb * 1024 ** 3 if cap_gb else None
        results.append({
            "customer": customer_id,
            "customer_ref": customer.customer_id,
            "full_name": customer.full_name,
            "download_bytes": entry["download_bytes"],
            "upload_bytes": entry["upload_bytes"],
            "total_bytes": total,
            "cap_bytes": cap_bytes,
            "cap_used_pct": round(total / cap_bytes * 100, 1) if cap_bytes else None,
        })

    # Heaviest first, then by name so equal totals don't reshuffle each load.
    results.sort(key=lambda r: (-r["total_bytes"], r["full_name"]))
    if limit:
        results = results[:limit]

    return {
        "period": period,
        "period_label": label,
        "start": start,
        "end": end,
        "measuring_since": measurement_start(),
        "results": results,
    }


def request_live_readings(customer):
    """Tell the platform somebody is watching this customer, so the live
    figures are fresh while they look and cost nothing when they don't.

    Returns the number of routers a reader was asked for. Never blocks on a
    router: registering interest is a database write, and the reading happens
    on a background thread (network.live_broker).

    Called from the usage endpoints rather than from a separate "start
    watching" call, so the act of asking for the figures IS the signal. A
    separate call would need a matching "stop" that a closed laptop lid would
    never send.
    """
    from billing.models import Service
    from network.live_broker import register_interest

    device_ids = set(
        Service.objects.filter(customer=customer, device__isnull=False, device__api_enabled=True)
        .exclude(status=Service.Status.TERMINATED)
        .values_list("device_id", flat=True)
    )
    for device_id in device_ids:
        register_interest(device_id)
    return len(device_ids)
