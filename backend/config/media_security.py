"""Signed, short-lived download links for files under MEDIA_ROOT.

Right now the only model storing files here is `inventory.StockReceipt`
(supplier-invoice attachments/receipts) — internal, staff-only data, not
anything customer-facing. It was previously served by a plain
`django.views.static.serve` view with no permission check at all: anyone
who found or guessed a `/media/...` URL could download the file, with
no login required.

The frontend links to these with a plain `<a href>` — a normal browser
navigation, which never carries the JWT bearer token the rest of the API
uses (that lives in localStorage, not a cookie). So a session/JWT check
on the file-serving view itself wouldn't actually see any credentials to
check. Instead, we sign the file's path with a short expiry at the
moment a *permitted* API response (already gated by IsStaffMember, same
as every other inventory endpoint) hands the URL to the frontend. A
bare, unsigned, or expired `/media/...` URL is rejected — the only way
to get a working link is to already be an authenticated staff user
hitting the real API.
"""

from django.core.signing import BadSignature, SignatureExpired, TimestampSigner

_SIGNER_SALT = "config.media_security"

# Long enough for a click-through open/download, short enough that a
# leaked/cached/forwarded link stops working well within the same day.
LINK_MAX_AGE_SECONDS = 300


def sign_media_path(relative_path: str) -> str:
    """Sign a path relative to MEDIA_ROOT (e.g. an attachment's `.name`)."""
    return TimestampSigner(salt=_SIGNER_SALT).sign(relative_path)


def verify_media_path(relative_path: str, signature: str) -> bool:
    """True only if `signature` is a still-valid signature for exactly `relative_path`."""
    if not signature:
        return False
    try:
        unsigned = TimestampSigner(salt=_SIGNER_SALT).unsign(signature, max_age=LINK_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return False
    return unsigned == relative_path
