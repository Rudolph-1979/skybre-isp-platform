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
import datetime
import json
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


def count_client_blocks(text):
    """How many `client "..." {` blocks a rendered config contains.

    This does not parse FreeRADIUS config and does not need to. The only
    distinction that has ever mattered operationally is "has clients"
    versus "has none" -- a config with none is perfectly valid, starts
    cleanly, reports itself healthy, and ignores every NAS on the network.
    """
    return sum(1 for line in text.splitlines() if line.startswith("client "))


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


def _running_against_test_database():
    """True when the connected database is a Django test database.

    Checked on the CONNECTION, not on sys.argv and not on a settings flag,
    because the failure this prevents does not care how the tests were
    started. `manage.py test`, pytest, a CI runner and a developer's shell
    all arrive here by the same route: a signal fires, the renderer runs
    against whatever rows the test just made, and the result is written
    over the live RADIUS server's configuration.

    That is not a hypothetical. On 2026-09-20 it happened. A test suite
    run deleted every RadiusNasClient as part of its ordinary work;
    post_delete fired, the render came back empty because the TEST
    database had no clients left in it, and the host installed that empty
    config on the production RADIUS server. FreeRADIUS restarted cleanly
    with no clients at all and ignored every router on the network for
    ten hours -- routers retransmitting into silence, accounting stopped,
    every customer reading as offline, and the daemon reporting itself
    healthy throughout.

    The other renders that evening failed validation for a related
    reason: the test fixture's NAS sits on 127.0.0.1, which collides with
    FreeRADIUS's stock `client localhost`, and a duplicate client is a
    fatal config error. So of everything the tests produced, the only
    thing that could install itself was the empty file.
    """
    from django.db import connection

    return str(connection.settings_dict.get("NAME") or "").startswith("test_")


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

    # A test run must never write the live RADIUS configuration. See
    # _running_against_test_database() for the outage that makes this
    # non-negotiable.
    #
    # Tests that genuinely mean to exercise the spool opt in with
    # RADIUS_CLIENTS_SPOOL_TEST_OK and point RADIUS_CLIENTS_SPOOL at a
    # directory of their own. Everything else gets silence -- debug
    # rather than error, because during a test run this is the expected
    # outcome many times over and a warning per occurrence would train
    # people to ignore the log.
    if _running_against_test_database() and not getattr(
        settings, "RADIUS_CLIENTS_SPOOL_TEST_OK", False
    ):
        logger.debug(
            "Not writing the clients.conf spool: running against a test "
            "database. (%s)", reason or "no reason given",
        )
        return None

    try:
        rendered = render_clients_conf_text()

        # A render with no clients in it is not a config, it is an outage.
        #
        # 2026-09-20: this function rendered an empty config. Every NAS row
        # was in the database and active the whole time -- why the query
        # came back empty was never established -- and the host installed
        # the result because an empty clients.conf validates perfectly.
        # FreeRADIUS then ignored every router on the network for ten
        # hours while the journal reported a healthy restart.
        #
        # The check deliberately compares against the FILE rather than
        # re-running the query. Whatever made the query return nothing
        # would make a second query return nothing too; what is already on
        # disk is independent evidence that clients existed a moment ago.
        if count_client_blocks(rendered) == 0 and not getattr(
            settings, "RADIUS_ALLOW_EMPTY_CLIENTS", False
        ):
            try:
                with open(path) as existing:
                    live = count_client_blocks(existing.read())
            except OSError:
                live = 0
            if live:
                logger.error(
                    "REFUSING to spool a clients.conf with no clients over one "
                    "holding %s. Rendering found no active RadiusNasClient rows, "
                    "but %s client(s) are live on the RADIUS server, so writing "
                    "this would take every NAS off the network. The spool file "
                    "is unchanged. Reason given: %s",
                    live, live, reason or "no reason given",
                )
                return None
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


def clients_conf_state():
    """What FreeRADIUS is actually running, as far as this side can see.

    write_clients_conf_spool's docstring has promised the frontend this
    function since the spool was built. It never existed -- which is part
    of why the 2026-09-20 outage stayed invisible for ten hours: the
    platform had no way to say "the config this screen describes is not
    the config the RADIUS server is running", so a dead network and a
    healthy one looked identical on every page.

    The host writes apply_status.json beside the spool file on every
    attempt, successful or not. Reading it here is what turns a journal
    entry nobody is watching into something a screen can show.
    """
    from .models import RadiusNasClient

    path = spool_path()
    state = {
        "spool_configured": bool(path),
        "active_clients": RadiusNasClient.objects.filter(is_active=True).count(),
        "spool_clients": None,
        "spool_written_at": None,
        "apply": None,
        "problems": [],
    }

    if not path:
        state["problems"].append(
            "RADIUS_CLIENTS_SPOOL is not configured, so RADIUS client changes "
            "made here never reach FreeRADIUS."
        )
        return state

    try:
        with open(path) as spool:
            state["spool_clients"] = count_client_blocks(spool.read())
        state["spool_written_at"] = datetime.datetime.fromtimestamp(
            os.path.getmtime(path), datetime.timezone.utc
        ).isoformat()
    except OSError:
        state["problems"].append(
            "The spool file has never been written, so no RADIUS client change "
            "has ever reached the host."
        )
        return state

    try:
        with open(os.path.join(os.path.dirname(path), "apply_status.json")) as status:
            state["apply"] = json.load(status)
    except (OSError, ValueError):
        state["problems"].append(
            "The host has not reported back on any apply attempt, so what "
            "FreeRADIUS is running right now is unknown."
        )
        return state

    applied = state["apply"] or {}
    detail = str(applied.get("detail") or "")
    live = applied.get("live_clients")

    if applied.get("state") == "outage":
        state["problems"].append(
            "FreeRADIUS is running a config with no NAS clients in it. Every "
            "router is being ignored and all customers will read as offline. "
            + detail
        )
    elif applied.get("state") == "refused":
        state["problems"].append(
            "The host refused the last config because it contained no clients. "
            "FreeRADIUS is still running the previous, working one. " + detail
        )
    elif applied.get("state") == "failed":
        state["problems"].append(
            "The last attempt to apply RADIUS clients failed: " + detail
        )
    elif (
        applied.get("state") == "ok"
        and live is not None
        and state["spool_clients"] is not None
        and live != state["spool_clients"]
    ):
        # Not an error on its own -- the watcher may simply not have run
        # yet -- but it is the difference between what staff see and what
        # the network is doing, which is the thing worth surfacing.
        state["problems"].append(
            "The host last applied %s client(s) but the current config holds "
            "%s, so a change has not been applied yet."
            % (live, state["spool_clients"])
        )

    return state
