"""Rendering FreeRADIUS's `clients.conf` from RadiusNasClient rows, and
spooling it somewhere the host can pick up.

FreeRADIUS reads which NAS devices it accepts requests from -- and with
which shared secret -- from clients.conf on disk. It is read at
startup/reload, never queried live, so there is no way to make SQL rows
authoritative; something has to write a file and reload the daemon.

FreeRADIUS also runs on the VPS *host*, not in Docker (it needs to own
UDP 1812/1813 and read /etc/freeradius), while this app runs inside a
container. A container cannot write to /etc/freeradius or reload a host
systemd unit, and giving it the ability to do either (host PID namespace,
docker socket, systemd over dbus) would be a far worse trade than the
problem it solves.

So the split is: this module writes the rendered config to a spool
directory that is bind-mounted from the host (RADIUS_CLIENTS_SPOOL,
/srv/radius/clients.conf in the container = /var/lib/skybre/radius/ on
the host). A host-side systemd path unit notices the file change,
validates it with `freeradius -CX`, and only then installs it and
reloads. Validation on the host side is the important part: reloading
FreeRADIUS with a broken clients.conf would drop authentication for
every customer, so a bad render must never reach the live config.

The file holds shared secrets in plaintext -- same as clients.conf itself
-- so it is written 0600 into a 0700 directory.
"""
import logging
import os
import tempfile

from django.conf import settings

logger = logging.getLogger(__name__)


def _quote(value):
    """A double-quoted FreeRADIUS config value.

    Everything staff can type goes through this, for two separate reasons
    found by testing against real FreeRADIUS 3.2.5:

    1. A bare value containing a space breaks the parse outright. A
       shortname of "Bench Hennenman" renders `client Bench Hennenman {`
       and FreeRADIUS rejects the whole config -- so any client whose
       shortname had a space took the entire clients.conf down with it.
       Quoted section names parse fine, which is why this quotes rather
       than slugifying: the name staff typed is preserved exactly, and
       the UI and the live config can't drift apart.

    2. Worse, a bare value containing `#` does NOT fail -- it parses,
       with the value silently TRUNCATED at the comment character. A
       secret of "abc#def" becomes "abc", so every login fails against a
       config that looks perfectly valid and passes validation. Quoting
       is the only thing that catches this, since no amount of config
       checking can see a truncated-but-syntactically-fine value.
    """
    text = "" if value is None else str(value)
    # Strip newlines first: they'd end the directive early no matter how
    # it's quoted, and nothing here is legitimately multi-line.
    text = text.replace("\r", " ").replace("\n", " ")
    text = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def _comment_safe(value):
    """Text that's about to be emitted after a `#`, with newlines removed
    so it can't escape the comment and become a directive."""
    return ("" if value is None else str(value)).replace("\r", " ").replace("\n", " ")


def render_clients_conf_text():
    """The clients.conf snippet for every active NAS client, as a string."""
    from .models import RadiusNasClient

    clients = RadiusNasClient.objects.filter(is_active=True).order_by("name")

    lines = [
        "# Auto-generated from the RADIUS clients listed under Configs ->",
        "# RADIUS in the Skybre admin panel. Do not hand-edit: the next",
        "# change saved there overwrites this file.",
        "",
    ]

    if not clients:
        lines.append("# No active NAS clients configured yet.")

    for client in clients:
        description = _comment_safe(client.description)
        lines += [
            f'client {_quote(client.shortname)} {{',
            f'    ipaddr = {client.ip_address}',
            f'    secret = {_quote(client.secret)}',
            f'    shortname = {_quote(client.shortname)}',
            "    nastype = mikrotik",
            # BlastRADIUS (CVE-2024-3596). RADIUS's Response Authenticator is
            # MD5-based, so an attacker who can intercept and modify traffic
            # can forge a valid-looking reply. Requiring Message-Authenticator
            # on every packet is the mitigation; FreeRADIUS 3.2.5+ warns
            # loudly when a client sends one while this is unset.
            #
            # The NAS must send it, or its requests start being rejected the
            # moment this file is applied. On RouterOS, set this FIRST:
            #     /radius set 0 require-message-auth=yes
            "    require_message_authenticator = yes",
            f'    # {_comment_safe(client.name)}{" -- " + description if description else ""}',
        ]
        if client.realm:
            # Documentation only -- realm here is a reporting/segmentation
            # tag (see RadiusNasClient.realm's docstring), not a live
            # FreeRADIUS routing directive, so it stays a comment.
            lines.append(f"    # realm: {_comment_safe(client.realm)}")
        lines.append("}")
        lines.append("")

    # Trailing newline: config files should end with one, and without it
    # `cat` runs the last line into the shell prompt.
    return "\n".join(lines) + "\n"


def spool_path():
    """Where to write the rendered config, or None if spooling is off.

    Unset means "not configured" and every write becomes a no-op, so a
    dev machine or a deployment that hasn't added the bind mount yet
    behaves exactly as before rather than erroring on every save.
    """
    return getattr(settings, "RADIUS_CLIENTS_SPOOL", "") or None


def write_clients_conf_spool(reason=""):
    """Render and atomically write the spool file.

    Returns the path written, or None if spooling isn't configured.

    Never raises: this is called from model signals, and a spool problem
    (missing bind mount, read-only filesystem, full disk) must not be
    allowed to roll back the admin's save. It logs loudly instead --
    the frontend surfaces the same information via the API's
    `clients_conf_state`.
    """
    path = spool_path()
    if not path:
        return None

    try:
        rendered = render_clients_conf_text()
        directory = os.path.dirname(path) or "."
        os.makedirs(directory, mode=0o700, exist_ok=True)

        # Write to a temp file in the same directory and rename, so the
        # host watcher never sees a half-written config. os.replace is
        # atomic within a filesystem.
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".clients.conf.")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(rendered)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

        logger.info("Wrote FreeRADIUS clients.conf spool to %s (%s)", path, reason or "no reason given")
        return path
    except Exception:
        logger.exception(
            "Could not write the FreeRADIUS clients.conf spool to %s. RADIUS "
            "client changes made in the admin panel will NOT reach FreeRADIUS "
            "until this is fixed.", path,
        )
        return None
