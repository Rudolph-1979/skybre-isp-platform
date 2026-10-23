"""FNB access / refresh tokens, encrypted at rest (#185).

Fernet (from `cryptography`, already on the server as a dependency of the
PDF stack) with a key derived from SECRET_KEY, so a database dump alone
does not hand anybody a live bank connection. If SECRET_KEY is ever
rotated the stored tokens can no longer be read: the account then shows
"Reconnect needed" and somebody connects it again on Integration Channel.
That is the right failure -- rotating SECRET_KEY should cut old sessions.
"""
import base64
import hashlib

from django.conf import settings


class TokenUnreadable(Exception):
    """The stored token was encrypted with a different SECRET_KEY."""


def _fernet():
    from cryptography.fernet import Fernet

    digest = hashlib.sha256(b"skybre-bankfeeds-fnb-token:" + settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value: str) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    if not value:
        return ""
    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise TokenUnreadable(
            "The stored FNB token can't be read (SECRET_KEY has changed since it was saved). "
            "Connect this account to FNB again."
        ) from exc
