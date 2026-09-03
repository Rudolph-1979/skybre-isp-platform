"""Binds the incoming request to audit context for its lifetime."""
import ipaddress

from .context import bind_request, clear_request


def client_ip(request):
    """The caller's address, not the reverse proxy's.

    Nginx terminates HTTPS on the host and proxies to the container, so
    REMOTE_ADDR is 127.0.0.1 for every single request -- an audit trail
    full of 127.0.0.1 tells you nothing about where a sign-in came from.

    The LEFTMOST X-Forwarded-For entry is the original client. It is also
    trivially forged by whoever sends it, and it is deliberately still
    used here: this value is only ever DISPLAYED, never trusted for a
    decision, and an approximate origin somebody could lie about is far
    more useful on screen than 127.0.0.1 on every row. Nothing in this
    platform authorises anything based on it.

    Forgeable is fine. Unvalidated was not. AuditEvent.ip_address is a
    GenericIPAddressField over a Postgres `inet` column, and a header that
    isn't an address at all reached the INSERT and was rejected there --
    where both audit writers swallow every exception (audit.tracking and
    audit.auth_events both `except Exception: pass`, so that a failure to
    log can never break the action being logged). The result was that one
    junk header made the sender's failed logins, successful logins and
    record edits silently vanish from the trail meant to be tamper-evident,
    and where the write sat inside an atomic block it poisoned the
    transaction instead. So the value is parsed here and dropped back to
    REMOTE_ADDR if it isn't an IP.
    """
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        # Some proxies append a port ("203.0.113.9:51520"); IPv6 brackets
        # likewise. Neither survives ip_address(), so strip before parsing.
        candidate = first.strip("[]")
        if candidate.count(":") == 1 and "." in candidate:
            candidate = candidate.split(":")[0]
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            pass
    remote = request.META.get("REMOTE_ADDR") or None
    if remote:
        try:
            return str(ipaddress.ip_address(remote.strip()))
        except ValueError:
            return None
    return None


class AuditActorMiddleware:
    """Holds the request in a contextvar so model signals can find out
    who is behind the save they're reacting to.

    The request is bound rather than the user, because at middleware time
    a JWT request has not been authenticated yet -- see context.py.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request._audit_ip = client_ip(request)
        bind_request(request)
        try:
            return self.get_response(request)
        finally:
            # Worker threads are reused. Leaving a finished request bound
            # would attribute the next request's saves to the last
            # request's user, which is worse than recording nobody.
            clear_request()
