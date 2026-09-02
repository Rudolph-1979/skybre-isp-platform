"""Binds the incoming request to audit context for its lifetime."""
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
    """
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.META.get("REMOTE_ADDR") or None


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
