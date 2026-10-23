"""Resolves outgoing-email (SMTP) configuration -- the DB-stored
EmailSettings singleton (editable from Configs -> Email Settings) takes
precedence field-by-field over the server's .env-driven defaults in
config/settings.py. A field left blank/unset in the DB falls back to its
.env default individually, so a fresh install with nothing configured here
keeps behaving exactly as it always has, and an admin can override just
one field (e.g. only the password, after rotating it) without needing to
fill in everything else.

Every place in the codebase that actually sends mail (currently
notifications/services.py and accounts/password_reset.py) should go
through get_email_config()/get_email_connection() here rather than reading
django.conf.settings.EMAIL_* directly, so a change saved in the UI takes
effect on the very next email sent -- no container restart required.
"""

# Seconds to wait on the SMTP socket. See get_email_connection for why a
# limit here is not optional.
SMTP_TIMEOUT_SECONDS = 20

from django.conf import settings
from django.core.mail import get_connection

from .models import EmailSettings


def get_email_config() -> dict:
    row = EmailSettings.load()
    return {
        "host": row.smtp_host or settings.EMAIL_HOST,
        "port": row.smtp_port if row.smtp_port is not None else settings.EMAIL_PORT,
        "username": row.smtp_username or settings.EMAIL_HOST_USER,
        "password": row.smtp_password or settings.EMAIL_HOST_PASSWORD,
        "use_tls": settings.EMAIL_USE_TLS if row.use_tls is None else row.use_tls,
        "use_ssl": settings.EMAIL_USE_SSL if row.use_ssl is None else row.use_ssl,
        "from_email": row.default_from_email or settings.DEFAULT_FROM_EMAIL,
        "company_name": row.company_name or settings.COMPANY_NAME,
        "site_url": row.site_url or settings.SITE_URL,
    }


def get_email_connection():
    """A ready-to-use mail connection reflecting the current config. Falls
    back to the console backend (prints instead of sending) if no SMTP
    host is configured anywhere -- same fallback the server itself uses
    when EMAIL_HOST is unset in .env."""
    cfg = get_email_config()
    if not cfg["host"]:
        return get_connection(backend="django.core.mail.backends.console.EmailBackend")
    return get_connection(
        backend="django.core.mail.backends.smtp.EmailBackend",
        host=cfg["host"],
        port=cfg["port"],
        username=cfg["username"],
        password=cfg["password"],
        use_tls=cfg["use_tls"],
        use_ssl=cfg["use_ssl"],
        # Without a timeout Django passes None to smtplib.SMTP, which is a
        # blocking socket with no deadline -- so a half-open connection to
        # the mail relay hangs the caller forever.
        #
        # That matters most where nobody is watching. A recurring-billing
        # run sends one email per customer, serially, inside the same
        # process: customers 1-299 get their invoices, customer 300 blocks
        # on the socket, and 301-1592 are never billed at all, with the
        # RecurringBillingRun row left on PROCESSED and zeroed counts
        # because the summary write is never reached.
        #
        # 20 seconds is deliberately generous for a handshake and still
        # bounded. Compare the RouterOS API at 8s and CoA at 4s -- mail was
        # the only outbound call in the platform with no limit at all.
        timeout=SMTP_TIMEOUT_SECONDS,
    )
