"""Sign-in, failed sign-in, sign-out.

Django's `user_logged_in` / `user_login_failed` signals are not used here
because they never fire: this platform authenticates with JWT through
SimpleJWT's token endpoint, which does not go through
django.contrib.auth.login(). Hooking the signals would produce an
authentication log that is permanently empty and looks like it works,
which is worse than not having one.
"""
from .tracking import record


def record_login(user, request=None):
    record("login", actor=user, detail="Signed in")


def record_login_failure(username, reason):
    """A failed attempt is recorded against the NAME THAT WAS TRIED, not
    against a user, because usually there is no user -- and when there is,
    attributing a failure to them implies they did it. Somebody guessing
    at another person's account would otherwise fill that person's own
    history with failures they never made.
    """
    from .models import AuditEvent
    from .context import current_request_meta

    meta = current_request_meta()
    try:
        AuditEvent.objects.create(
            actor=None,
            actor_label=(username or "(no username given)")[:255],
            action=AuditEvent.Action.LOGIN_FAILED,
            detail=reason[:500],
            ip_address=meta["ip_address"],
            user_agent=meta["user_agent"],
        )
    except Exception:  # pragma: no cover - never break a login response
        pass


def record_logout(user):
    record("logout", actor=user, detail="Signed out")
