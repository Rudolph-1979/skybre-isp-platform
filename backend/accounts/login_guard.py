"""Rate limiting for the login endpoint.

`/api/token/` had no throttle, no lockout and no delay of any kind, so an
attacker could guess staff passwords at whatever rate the server would
answer -- against a 6-character minimum, and against a 6-digit TOTP code
with `valid_window=1` (three codes valid at any moment), which makes the
second factor guessable too.

Deliberately counted out of the audit log rather than the cache:

  * DRF's throttles keep their counters in Django's cache, and this
    project configures no CACHES, so it falls back to per-process
    LocMemCache. With `gunicorn --workers 3` that multiplies every limit
    by three and resets it on each deploy -- the same reason the existing
    `public_usage` throttle is looser than it reads.
  * The failures are already written, by audit.auth_events.record_login_failure,
    with the attempted username and a timestamp. Counting rows that
    already exist needs no new table, no `createcachetable` deploy step,
    and no Redis added to the stack -- and it is shared by every worker
    automatically.

Rolling window, not a sticky lock: the count only looks at the last
WINDOW minutes, so a locked account frees itself.

TWO THINGS THIS DELIBERATELY DOES NOT DO, both of which the first
version got wrong:

  * It does not count the "password correct, 2FA code required" row.
    The SPA signs a 2FA user in with TWO posts -- the first returns 400
    with `two_factor_required` and writes that audit row, the second
    carries the code. Counting it meant ten CORRECT sign-ins exhausted a
    limit of ten, and a 2FA-enabled admin was locked out of their own
    platform by using it properly. Only genuine credential failures
    count, and _COUNTED_DETAILS is the list of them.

  * It does not rate-limit by IP. request._audit_ip prefers the
    left-most X-Forwarded-For entry, which the client sets -- so an
    attacker rotating that header bypassed the per-IP limit entirely,
    while anyone could lock a whole office out of the platform by
    sending failures with that office's address in it. audit.middleware's
    own docstring says that value is safe precisely BECAUSE nothing
    authorises on it; making it an authorisation input contradicted the
    one condition that made it safe. A real per-IP limit needs a
    trusted-proxy hop count (there are two proxies here, both appending),
    which is a settings change and a deployment question rather than
    something to infer from a header.
"""
from datetime import timedelta

from django.utils import timezone
from rest_framework.exceptions import Throttled

WINDOW = timedelta(minutes=15)

# Per attempted username. Scoped to one account, and a person who has
# genuinely forgotten their password does not need 10 tries in a quarter
# of an hour.
MAX_PER_USERNAME = 10

# The `detail` strings record_login_failure writes for an actual failed
# credential. Anything else it records -- notably the 2FA-code-required
# step of a successful sign-in -- is not a guess and must not count.
# Matched exactly against audit.auth_events' call sites in
# accounts.views.CustomTokenObtainPairView.
_COUNTED_DETAILS = (
    "Wrong username or password",
    "Password correct, 2FA code rejected",
)


def check_login_allowed(username, ip_address=None):
    """Raise Throttled (429) if this username has failed too often.

    Called before the password is checked, so a locked-out attacker gets
    no signal about whether the credentials they just tried were right.

    `ip_address` is accepted and ignored -- see the module docstring for
    why there is no per-IP limit. It stays in the signature so the caller
    reads honestly and so adding one back is a one-place change.

    Never raises anything else: a failure to count must not become a
    failure to log in, the same principle the audit writers already
    follow. If the audit table can't be read, the login proceeds.
    """
    if not username:
        return
    try:
        from audit.models import AuditEvent

        failures = AuditEvent.objects.filter(
            action=AuditEvent.Action.LOGIN_FAILED,
            actor_label=username[:255],
            detail__in=_COUNTED_DETAILS,
            created_at__gte=timezone.now() - WINDOW,
        ).count()
    except Throttled:
        raise
    except Exception:  # pragma: no cover - never break a login response
        return

    if failures >= MAX_PER_USERNAME:
        raise Throttled(
            detail=(
                "Too many failed sign-in attempts for this account. "
                "Wait 15 minutes and try again, or reset your password."
            )
        )
