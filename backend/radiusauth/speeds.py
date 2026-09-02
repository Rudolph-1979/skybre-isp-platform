"""What speed should this line be running at, right now?

One question, one answer, one place. Everything that sets a customer's
speed goes through `effective_speeds()`:

  * the Mikrotik-Rate-Limit written into radreply, used at LOGIN
  * the CoA sent to a session that is already up (enforcement.py)
  * the scheduled run that pushes changes at window boundaries
  * the readout on screen that explains why a line is slow

They must never be able to disagree. A customer whose radreply says one
thing and whose live session says another is the single hardest support
call this platform can generate, because both halves look correct on
their own screen.

The rules, in order:

  1. Start from the plan speed (or the service's Connection Rule
     override, which already exists for per-device shaping).
  2. If a speed window is on, apply its percentage.
  3. If the line is past its fair-use threshold, apply the shaped
     percentage.

and then, deliberately, THE WINDOW WINS. A customer who has been shaped
still gets the off-peak boost. It costs nothing -- the boost exists
precisely because those hours are empty -- and it turns "my internet is
slow" into something they can work around themselves at 01:00 instead of
a phone call at 19:00.
"""
import datetime
from dataclasses import dataclass

from django.db.models import Q, Sum
from django.utils import timezone


@dataclass
class EffectiveSpeed:
    upload_kbps: int
    download_kbps: int
    # "plan", "window", "fup", or "window over fup" -- what a human should
    # be told when they ask why the line is running at this speed.
    reason: str
    window_name: str = ""
    shaped: bool = False
    used_gb: float = 0.0
    threshold_gb: int = None

    @property
    def rate_limit(self):
        """The Mikrotik-Rate-Limit string: "rx/tx", rx being the rate FROM
        the client (their upload)."""
        return f"{self.upload_kbps}k/{self.download_kbps}k"


# A line with no plan speed set falls back to this rather than going out
# unthrottled. Matches the long-standing fallback in signals.py.
DEFAULT_KBPS = 10240


def _pct(value, pct):
    """Percentage of a speed, never rounding down to nothing.

    A 512k line shaped to 10% is 51k, but integer maths on a small enough
    number reaches 0 -- and 0k on a Mikrotik rate limit is not "very
    slow", it is a line that passes no traffic at all. Somebody would be
    disconnected by a fair-use rule that was only ever meant to slow them
    down.
    """
    return max(1, int(value * pct / 100))


def plan_speeds(service):
    """The line's unmodified speed: its Connection Rule override if it has
    one, else its tariff's."""
    rule = getattr(service, "connection_rule", None)
    if rule is not None:
        return rule.speed_up_kbps or DEFAULT_KBPS, rule.speed_down_kbps or DEFAULT_KBPS
    tariff = getattr(service, "tariff", None)
    if tariff is None:
        return DEFAULT_KBPS, DEFAULT_KBPS
    return (tariff.speed_upload_kbps or DEFAULT_KBPS, tariff.speed_download_kbps or DEFAULT_KBPS)


def active_window(service, at=None):
    """The speed window covering `at`, or None.

    Windows attached to this tariff are considered before network-wide
    ones, so a plan can opt out of the general schedule by carrying its
    own. Where several overlap, the most generous wins -- overlapping
    windows are a configuration mistake, and the mistake that annoys
    nobody is the one that gives a bit too much speed at 3am.
    """
    from .models import SpeedWindow

    at = at or timezone.localtime()
    tariff_id = getattr(service, "tariff_id", None)
    candidates = SpeedWindow.objects.filter(is_active=True).filter(
        Q(tariff__isnull=True) | Q(tariff_id=tariff_id)
    )
    covering = [w for w in candidates if w.covers(at)]
    if not covering:
        return None
    return max(covering, key=lambda w: w.speed_pct)


def fup_settings(service):
    """(threshold_gb, shaped_pct) for this line, or (None, _) for no policy.

    The service's own values win over the tariff's; `is None` rather than
    falsiness, because 0 GB is a real threshold meaning "shape from the
    first byte".
    """
    if service.fup_exempt:
        return None, 100
    tariff = getattr(service, "tariff", None)
    threshold = service.fup_threshold_gb
    if threshold is None:
        threshold = getattr(tariff, "fup_threshold_gb", None)
    pct = service.fup_speed_pct
    if pct is None:
        pct = getattr(tariff, "fup_speed_pct", 30)
    return threshold, pct


def countable_usage_gb(service, at=None):
    """Month-to-date usage that counts toward fair use, in GB.

    Traffic moved inside a window marked `counts_toward_fup=False` is
    excluded -- which is the whole reason to run an off-peak window at
    all. A window that still counts gives nobody any reason to move their
    downloads into it, and the evening stays exactly as congested as it
    was.

    Hour-resolution, because UsageBucket is hourly. A window that starts
    at 22:30 therefore excludes the whole 22:00 hour; the alternative is
    per-minute buckets, which is a great deal more data for a number
    nobody bills on.
    """
    from .models import SpeedWindow, UsageBucket

    at = at or timezone.localtime()
    username = (service.radius_username or "").strip()
    if not username:
        return 0.0

    start = at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    buckets = UsageBucket.objects.filter(username=username, bucket_start__gte=start, bucket_start__lte=at)

    free_windows = [
        w
        for w in SpeedWindow.objects.filter(is_active=True, counts_toward_fup=False).filter(
            Q(tariff__isnull=True) | Q(tariff_id=getattr(service, "tariff_id", None))
        )
    ]
    if not free_windows:
        total = buckets.aggregate(d=Sum("download_bytes"), u=Sum("upload_bytes"))
        return ((total["d"] or 0) + (total["u"] or 0)) / 1024 ** 3

    counted = 0
    for bucket in buckets.only("bucket_start", "download_bytes", "upload_bytes"):
        local = timezone.localtime(bucket.bucket_start)
        if any(w.covers(local) for w in free_windows):
            continue
        counted += bucket.download_bytes + bucket.upload_bytes
    return counted / 1024 ** 3


def effective_speeds(service, at=None):
    """The whole rule set, resolved. See this module's docstring."""
    at = at or timezone.localtime()
    up, down = plan_speeds(service)
    reason = "plan"
    window_name = ""
    shaped = False

    threshold, pct = fup_settings(service)
    used = 0.0
    if threshold is not None:
        used = countable_usage_gb(service, at)
        if used >= threshold:
            shaped = True

    window = active_window(service, at)

    if window is not None and window.speed_pct != 100:
        up, down = _pct(up, window.speed_pct), _pct(down, window.speed_pct)
        window_name = window.name
        # The window wins over the shaping, deliberately -- see the module
        # docstring. Reported as such rather than silently, so nobody
        # concludes the fair-use rule has stopped working.
        reason = "window over fup" if shaped else "window"
    elif shaped:
        up, down = _pct(up, pct), _pct(down, pct)
        reason = "fup"

    return EffectiveSpeed(
        upload_kbps=up,
        download_kbps=down,
        reason=reason,
        window_name=window_name,
        shaped=shaped,
        used_gb=round(used, 2),
        threshold_gb=threshold,
    )


def describe(effective):
    """One sentence a support agent can read down the phone."""
    if effective.reason == "plan":
        return "Running at the full plan speed."
    if effective.reason == "window":
        return f"Boosted by the “{effective.window_name}” window."
    if effective.reason == "window over fup":
        return (
            f"Past fair use ({effective.used_gb} GB of {effective.threshold_gb} GB), "
            f"but boosted right now by the “{effective.window_name}” window."
        )
    return (
        f"Shaped — past fair use ({effective.used_gb} GB of {effective.threshold_gb} GB "
        "this month)."
    )
