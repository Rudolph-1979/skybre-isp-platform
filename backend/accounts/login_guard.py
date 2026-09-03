"""Rate limiting for the login endpoint.

`/api/token/` had no throttle, no lockout and no delay of any kind, so an
attacker could guess staff passwords at whatever rate the server would
answer -- against a 6-character minimum, and against a 6-digit TOTP code
with `valid_window=1` (three codes valid at any moment), which makes the
second factor guessable too given enough attempts.

Deliberately counted out of the audit log rather than the cache:

  * DRF's throttles keep their counters in Django's cache, and this
    project configures no CACHES, so it falls back to per-process
    LocMemCache. With `gunicorn --workers 3` that multiplies every limit
    by three and resets it on each deploy -- the same reason the existing
    `public_usage` throttle is looser than it reads.
  * The failures are already written, by audit.auth_events.record_login_failure,
    with the attempted username, the IP and a timestamp. Counting rows
    that already exist needs no new table, no `createcachetable` deploy
    step, and no Redis added to the stack -- and it is shared by every
    worker automatically.

Rolling window, not a sticky lock: the count only looks at the last
WINDOW minutes, so a locked account frees itself. A hard lock would hand
anyone a way to keep a real staff member out of their own platform by
failing their login on purpose.
"""
from datetime import timedelta

from django.utils import timezone
from rest_framework.exceptions import Throttled

WINDOW = timedelta(minutes=15)

# Per attempted username. Low, because it is scoped to one account and a
# person who has genuinely forgotten their password does not need 10 tries
# inside a quarter of an hour.
MAX_PER_USERNAME = 10

# Per source IP, across all usernames. Higher, because a whole office
# behind one NAT address shares it -- this is aimed at credential
# stuffing, where one host works through a list of accounts.
MAX_PER_IP = 30


def _failures_since(cutoff, **filters):
    from audit.models import AuditEvent

    return AuditEvent.objects.filter(
        action=AuditEvent.Action.LOGIN_FAILED, created_at__gte=cutoff, **filters
    ).count()


def check_login_allowed(username, ip_address):
    """Raise Throttled (429) if this username or IP has failed too often.

    Called before the password is checked, so a locked-out attacker gets
    no signal about whether the credentials they just tried were right.

    Never raises anything else: a failure to count must not become a
    failure to log in, the same principle the audit writers already
    follow. If the audit table can't be read, the login proceeds.
    """
    cutoff = timezone.now() - WINDOW
    try:
        if username:
            if _failures_since(cutoff, actor_label=username[:255]) >= MAX_PER_USERNAME:
                raise Throttled(
                    detail=(
                        "Too many failed sign-in attempts for this account. "
                        "Wait 15 minutes and try again, or reset your password."
                    )
                )
        if ip_address:
            if _failures_since(cutoff, ip_address=ip_address) >= MAX_PER_IP:
                raise Throttled(
                    detail="Too many failed sign-in attempts from this network. Wait 15 minutes and try again."
                )
    except Throttled:
        raise
    except Exception:  # pragma: no cover - never break a login response
        return
