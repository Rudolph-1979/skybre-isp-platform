"""Speed over time: the Splynx-style "In speed / Out speed" graph.

Rudolph, 5 Oct 2026, comparing our usage bars to Splynx's MRTG graph:
"This shows very nicely when customer over utilised his line". GB per day
answers how much a customer used; this answers how hard they were pushing
the line, and against what plan speed.

Four rolling windows, like Splynx's daily/weekly/monthly/yearly graphs:

    day    last 24 hours, one point per 5 minutes    (SpeedBucket)
    week   last 7 days,   one point per 30 minutes   (SpeedBucket)
    month  last 31 days,  one point per 2 hours      (UsageBucket)
    year   last 365 days, one point per day          (UsageBucket)

Each point is an AVERAGE speed over its interval: bytes moved x 8 / seconds.
That is exactly what Splynx/MRTG plot. "Maximum" is the highest point,
"Average" is the whole window's bytes over the whole window's time, and
"Last" is the most recent complete point.

SpeedBucket only exists from the day this shipped and is kept for
SPEED_RETENTION_DAYS. Where the 5-minute detail isn't there yet, a day or
week point falls back to its hour's UsageBucket average, so the graph has
history from the start; `detail_since` says where the fine detail begins.
"""
import datetime

from django.db.models import F, Sum
from django.utils import timezone

from .models import SpeedBucket, UsageBucket

SLOT_SECONDS = 300
SPEED_RETENTION_DAYS = 8

RANGES = {
    # key: (window, step seconds, source)
    "day": (datetime.timedelta(hours=24), 300, "speed"),
    "week": (datetime.timedelta(days=7), 1800, "speed"),
    "month": (datetime.timedelta(days=31), 7200, "usage"),
    "year": (datetime.timedelta(days=365), 86400, "usage"),
}


def _floor(when, seconds):
    ts = int(when.timestamp())
    return datetime.datetime.fromtimestamp(ts - ts % seconds, tz=datetime.timezone.utc)


def bank_speed(username, since, until, download, upload):
    """Spread a counter delta over the 5-minute slots between `since` and
    `until`, in proportion to how much of each slot it covers.

    A delta covering one interim interval (often 1 or 5 minutes) mostly
    lands in one or two slots; one covering a longer gap (the sampler was
    down, a 10-minute interim) is spread evenly rather than piled into the
    slot the sampler happened to notice it in -- which would draw a spike
    that never happened.
    """
    if not username:
        return
    download = max(int(download or 0), 0)
    upload = max(int(upload or 0), 0)
    if not download and not upload:
        return
    if since is None or since >= until:
        since = until - datetime.timedelta(seconds=1)

    total = (until - since).total_seconds()
    shares = []
    cursor = since
    while cursor < until:
        slot = _floor(cursor, SLOT_SECONDS)
        slot_end = slot + datetime.timedelta(seconds=SLOT_SECONDS)
        part_end = min(slot_end, until)
        shares.append((slot, (part_end - cursor).total_seconds() / total))
        cursor = part_end

    # Integer shares that still add up to the whole delta: the rounding
    # remainder goes to the last slot.
    given_down = given_up = 0
    for i, (slot, frac) in enumerate(shares):
        last = i == len(shares) - 1
        d = download - given_down if last else int(download * frac)
        u = upload - given_up if last else int(upload * frac)
        given_down += d
        given_up += u
        if not d and not u:
            continue
        row, created = SpeedBucket.objects.get_or_create(
            username=username, bucket_start=slot, defaults={"download_bytes": d, "upload_bytes": u},
        )
        if not created:
            SpeedBucket.objects.filter(pk=row.pk).update(
                download_bytes=F("download_bytes") + d, upload_bytes=F("upload_bytes") + u,
            )


def prune_speed_buckets(now=None):
    now = now or timezone.now()
    cutoff = now - datetime.timedelta(days=SPEED_RETENTION_DAYS)
    return SpeedBucket.objects.filter(bucket_start__lt=cutoff).delete()[0]


def detail_since():
    return SpeedBucket.objects.order_by("bucket_start").values_list("bucket_start", flat=True).first()


def _points_from(model, usernames, start, end, step):
    """{point_start: (down_bytes, up_bytes)} summed into `step` intervals."""
    sums = {}
    rows = (
        model.objects.filter(username__in=usernames, bucket_start__gte=start, bucket_start__lt=end)
        .values("bucket_start")
        .annotate(down=Sum("download_bytes"), up=Sum("upload_bytes"))
    )
    for r in rows:
        key = _floor(r["bucket_start"], step)
        d, u = sums.get(key, (0, 0))
        sums[key] = (d + (r["down"] or 0), u + (r["up"] or 0))
    return sums


def speed_series(usernames, range_key="day", now=None, plan=None):
    """Points (average bps per interval) plus Maximum / Average / Last."""
    if range_key not in RANGES:
        raise ValueError(f"range must be one of {', '.join(RANGES)}")
    window, step, source = RANGES[range_key]
    now = now or timezone.now()
    tz = timezone.get_current_timezone()

    if step == 86400:
        # Whole local days, ending with yesterday (today isn't complete).
        today = timezone.localtime(now, tz).date()
        end = timezone.make_aware(datetime.datetime.combine(today, datetime.time.min), tz)
        start = end - datetime.timedelta(days=365)
        starts, cursor = [], start
        while cursor < end:
            starts.append(cursor)
            cursor = timezone.make_aware(
                datetime.datetime.combine(timezone.localtime(cursor, tz).date() + datetime.timedelta(days=1),
                                          datetime.time.min), tz)
    else:
        # Up to the last COMPLETE interval: the one in progress would read low.
        end = _floor(now, step)
        start = end - window
        starts = [start + datetime.timedelta(seconds=step * i) for i in range(int(window.total_seconds() // step))]

    usernames = list(usernames or [])
    fine_from = detail_since() if source == "speed" else None

    if not usernames:
        sums, hourly = {}, {}
    elif source == "speed":
        sums = _points_from(SpeedBucket, usernames, start, end, step)
        # Hourly fallback for the stretch before 5-minute detail existed.
        hourly = _points_from(UsageBucket, usernames, start, min(end, fine_from) if fine_from else end, 3600)
    else:
        if step == 86400:
            sums = {}
            rows = (
                UsageBucket.objects.filter(username__in=usernames, bucket_start__gte=start, bucket_start__lt=end)
                .values("bucket_start").annotate(down=Sum("download_bytes"), up=Sum("upload_bytes"))
            )
            for r in rows:
                local = timezone.localtime(r["bucket_start"], tz).date()
                key = timezone.make_aware(datetime.datetime.combine(local, datetime.time.min), tz)
                d, u = sums.get(key, (0, 0))
                sums[key] = (d + (r["down"] or 0), u + (r["up"] or 0))
        else:
            sums = _points_from(UsageBucket, usernames, start, end, step)
        hourly = {}

    points = []
    total_down = total_up = 0
    total_seconds = 0
    for i, at in enumerate(starts):
        seconds = ((starts[i + 1] if i + 1 < len(starts) else end) - at).total_seconds()
        down, up = sums.get(at, (0, 0))
        if source == "speed" and (fine_from is None or at < fine_from):
            # No 5-minute detail yet for this point: use its hour's average.
            hd, hu = hourly.get(_floor(at, 3600), (0, 0))
            down, up = int(hd * seconds / 3600), int(hu * seconds / 3600)
        total_down += down
        total_up += up
        total_seconds += seconds
        points.append({
            "at": at,
            "download_bps": int(down * 8 / seconds) if seconds else 0,
            "upload_bps": int(up * 8 / seconds) if seconds else 0,
        })

    def stat(fn, key):
        return fn(p[key] for p in points) if points else 0

    return {
        "range": range_key,
        "interval_seconds": step,
        "start": start,
        "end": end,
        "points": points,
        "max": {"download_bps": stat(max, "download_bps"), "upload_bps": stat(max, "upload_bps")},
        "avg": {
            "download_bps": int(total_down * 8 / total_seconds) if total_seconds else 0,
            "upload_bps": int(total_up * 8 / total_seconds) if total_seconds else 0,
        },
        "last": {
            "download_bps": points[-1]["download_bps"] if points else 0,
            "upload_bps": points[-1]["upload_bps"] if points else 0,
        },
        "plan": plan,
        "detail_since": fine_from,
    }


def customer_lines(customer):
    """Each RADIUS login on the customer with its plan speed, for the line
    picker and the plan line on the graph. Terminated services left out."""
    lines = []
    for s in (
        customer.services.select_related("tariff")
        .exclude(status="terminated")
        .exclude(radius_username="")
        .exclude(radius_username__isnull=True)
        .order_by("id")
    ):
        t = s.tariff
        lines.append({
            "username": s.radius_username,
            "label": f"{t.name} · {s.radius_username}" if t else s.radius_username,
            "download_bps": (t.speed_download_kbps or 0) * 1000 if t else 0,
            "upload_bps": (t.speed_upload_kbps or 0) * 1000 if t else 0,
        })
    return lines


def customer_speed(customer, range_key="day", line=None, now=None):
    lines = customer_lines(customer)
    chosen = [l for l in lines if l["username"] == line] if line else lines
    usernames = [l["username"] for l in chosen]
    plan = None
    if chosen and all(l["download_bps"] for l in chosen):
        plan = {
            "download_bps": sum(l["download_bps"] for l in chosen),
            "upload_bps": sum(l["upload_bps"] for l in chosen),
        }
    data = speed_series(usernames, range_key, now=now, plan=plan)
    data["lines"] = lines
    data["line"] = line if chosen and line else None
    return data
