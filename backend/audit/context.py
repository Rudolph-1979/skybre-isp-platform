"""Who is acting, for code that is too far from the request to be told.

Model signals fire deep inside a save() with no idea a request exists.
The alternative to this module is threading a `user` argument through
every serializer, view, command and helper that touches a tracked model
-- which works right up until one caller forgets, and then the audit
trail quietly starts recording "system" for real human edits. Getting
the actor from ambient context makes forgetting impossible instead.

contextvars rather than threading.local: async views and the live-broker
threads both exist here, and a threading.local would leak one request's
actor into another's work on a reused worker thread.
"""
from contextlib import contextmanager
from contextvars import ContextVar

# The live HttpRequest, not the user pulled off it. DRF authenticates
# inside the view, well after middleware has run, so a user read at
# middleware time is always AnonymousUser -- every staff edit would be
# filed under "system". Holding the request and reading .user at signal
# time instead means the lookup happens after authentication, whenever
# the save actually occurs.
_request: ContextVar = ContextVar("audit_request", default=None)
# Explicit override, for management commands, background jobs and tests
# where there is no request to read from.
_actor_override: ContextVar = ContextVar("audit_actor_override", default=None)
# Set while a management command or background job is running, so those
# events are attributed to the job by name instead of to nobody.
_system_label: ContextVar = ContextVar("audit_system_label", default="")


def bind_request(request):
    _request.set(request)


def clear_request():
    _request.set(None)


def current_actor():
    override = _actor_override.get()
    if override is not None:
        return override
    request = _request.get()
    user = getattr(request, "user", None) if request is not None else None
    return user if (user is not None and getattr(user, "is_authenticated", False)) else None


def current_request_meta():
    request = _request.get()
    if request is None:
        return {"ip_address": None, "user_agent": ""}
    return {
        "ip_address": getattr(request, "_audit_ip", None),
        "user_agent": (request.META.get("HTTP_USER_AGENT", "") or "")[:300],
    }


def current_system_label():
    return _system_label.get()


@contextmanager
def acting_as(user):
    """Attribute everything in this block to `user` explicitly."""
    token = _actor_override.set(user)
    try:
        yield
    finally:
        _actor_override.reset(token)


@contextmanager
def acting_as_system(label):
    """For management commands and scheduled jobs.

    `apply_cancellations` ending a customer's service is a real change
    that a person will one day need explained. Recording it as "system"
    with no idea which job did it is barely better than not recording it,
    so the job names itself: "apply_cancellations".
    """
    token = _system_label.set(label)
    try:
        yield
    finally:
        _system_label.reset(token)
