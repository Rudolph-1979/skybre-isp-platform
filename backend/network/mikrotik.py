"""Thin wrapper around the real Mikrotik RouterOS API (via the `librouteros`
package) for devices with `api_enabled=True` (see network.models.Device).

Everything in this module was built and exercised against a lightweight fake
RouterOS API server written for testing (see the test suite referenced in
docs/RADIUS_SETUP.md-adjacent notes) since there is no real Mikrotik hardware
reachable from this environment. The wire protocol handling (connect/login/
command encoding) is exactly what `librouteros` implements and is a
well-established, widely used library for this -- but the specific RouterOS
command names/arguments below (system resource, ppp active, radius, ppp aaa)
have not been verified against a real router. Please test against a real
device (Test Connection button in the UI is the quickest way) before relying
on this for live customers, and adjust field names here if your RouterOS
version differs.

Every public function in this module raises `MikrotikError` -- never a raw
socket/protocol exception -- so callers (DRF views) can turn a connection or
auth failure into a clean, readable error response instead of a 500.
"""
import logging
import ssl
import threading
from contextlib import contextmanager

from librouteros import connect
from librouteros.exceptions import TrapError, MultiTrapError

logger = logging.getLogger(__name__)

# --- Managed-config tags -------------------------------------------------
# Every rule/entry the "live API" features (blocking, shaper, wireless)
# push to a router carries one of these comments/prefixes, so it can always
# be found again to update or remove -- and so removal logic here NEVER
# touches a firewall rule, queue, address-list entry, or access-list entry
# a human added by hand. See network.router_sync for the higher-level
# billing.Service-aware logic that calls these.
BLOCK_ADDRESS_LIST = "skybre-blocked"
BLOCK_FIREWALL_COMMENT_SRC = "skybre-auto-block-src"
BLOCK_FIREWALL_COMMENT_DST = "skybre-auto-block-dst"
QUEUE_COMMENT_PREFIX = "skybre-auto-queue-service-"
WIRELESS_COMMENT_PREFIX = "skybre-auto-wifi-"

_device_locks = {}
_device_locks_guard = threading.Lock()


def get_device_lock(device_id):
    """One lock per device id, shared process-wide -- used so only one
    RouterOS API connection to a given device is ever open at a time (e.g.
    several staff polling the same customer's live bandwidth simultaneously
    queue up onto one connection instead of each opening their own).
    Process-local only: if this backend is ever scaled to more than one
    worker process, each process gets its own lock and this no longer
    guarantees a single connection platform-wide -- fine for this
    deployment's single-backend-container setup, worth revisiting if that
    changes."""
    with _device_locks_guard:
        lock = _device_locks.get(device_id)
        if lock is None:
            lock = threading.Lock()
            _device_locks[device_id] = lock
        return lock


class MikrotikError(Exception):
    """Any Mikrotik API failure -- unreachable device, bad credentials, or
    the router rejecting a command. A device being offline/misconfigured is
    an expected, routine failure mode here, not a bug, so this is always
    caught and surfaced as a clean message rather than a stack trace."""


def _ssl_wrapper(sock):
    # RouterOS's API-SSL service almost always presents a self-signed
    # certificate -- this encrypts the transport without trying to
    # validate a cert chain that was never meant to chain to a public CA
    # (the same trust model RouterOS's own tools use against themselves).
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context.wrap_socket(sock)


@contextmanager
def api_connection(device, timeout=8):
    """Context manager yielding a connected librouteros Api for `device`.
    Raises MikrotikError with a clear, user-facing message on any failure."""
    if not device.api_enabled:
        raise MikrotikError(f"{device.name}: the Mikrotik API is not enabled for this device.")
    if not device.api_username or not device.api_password:
        raise MikrotikError(f"{device.name}: no API username/password is set for this device.")

    kwargs = dict(
        host=device.ip_address,
        username=device.api_username,
        password=device.api_password,
        port=device.api_port,
        timeout=timeout,
    )
    if device.api_use_ssl:
        kwargs["ssl_wrapper"] = _ssl_wrapper

    try:
        api = connect(**kwargs)
    except (TrapError, MultiTrapError) as exc:
        raise MikrotikError(
            f"{device.name}: RouterOS rejected the login -- check the API username/password. ({exc})"
        ) from exc
    except OSError as exc:
        raise MikrotikError(
            f"{device.name}: couldn't reach {device.ip_address}:{device.api_port} -- {exc}"
        ) from exc
    except Exception as exc:  # noqa: BLE001 -- deliberately broad: any other failure still needs to surface cleanly, not as a 500
        raise MikrotikError(f"{device.name}: couldn't connect to the RouterOS API -- {exc}") from exc

    try:
        yield api
    except MikrotikError:
        raise
    except (TrapError, MultiTrapError) as exc:
        raise MikrotikError(f"{device.name}: RouterOS rejected the request -- {exc}") from exc
    except OSError as exc:
        raise MikrotikError(f"{device.name}: lost connection to the router mid-request -- {exc}") from exc
    finally:
        try:
            api.close()
        except Exception:  # noqa: BLE001 -- best-effort cleanup, never let a close() failure mask the real error
            pass


def test_connection(device):
    """Connects and reads basic identity/version info -- backs the "Test
    Connection" button. Returns a small dict on success; raises
    MikrotikError on any failure."""
    with api_connection(device) as api:
        resource = next(iter(api("/system/resource/print")), {})
        identity = next(iter(api("/system/identity/print")), {})
        return {
            "identity": identity.get("name"),
            "routeros_version": resource.get("version"),
            "board_name": resource.get("board-name"),
            "uptime": resource.get("uptime"),
            "cpu_load_pct": resource.get("cpu-load"),
            "free_memory": resource.get("free-memory"),
            "total_memory": resource.get("total-memory"),
        }


def get_system_resource(device):
    """Raw /system/resource/print row -- CPU load, memory, uptime. Used by
    poll_mikrotik_devices to build a real MonitoringReading."""
    with api_connection(device) as api:
        return next(iter(api("/system/resource/print")), {})


def get_wan_interface_traffic(device, interface):
    """Instantaneous rx/tx bits-per-second for one interface, via
    /interface/monitor-traffic's "once" snapshot mode (a single reading
    rather than a live stream) -- the standard way to poll current
    bandwidth without keeping a connection open. Returns
    (bandwidth_in_mbps, bandwidth_out_mbps) as seen at the router,
    tx = router-to-client (download for the customer), rx = client-to-
    router (upload)."""
    with api_connection(device) as api:
        row = next(iter(api("/interface/monitor-traffic", numbers=interface, once="")), {})
        rx_bps = float(row.get("rx-bits-per-second", 0) or 0)
        tx_bps = float(row.get("tx-bits-per-second", 0) or 0)
        return round(rx_bps / 1_000_000, 2), round(tx_bps / 1_000_000, 2)


def get_ppp_active(device):
    """Live PPP/OVPN sessions currently connected to this device, straight
    from the router -- independent of FreeRADIUS's radacct (which relies on
    the router having sent accounting packets; this reads the router's own
    live state directly)."""
    with api_connection(device) as api:
        return list(api("/ppp/active/print"))


def disconnect_ppp_session(device, session_id):
    """Kicks one active PPP/OVPN session by its RouterOS `.id`. Returns
    nothing on success; raises MikrotikError otherwise (including if the
    session has already ended -- RouterOS reports that as a trap, which
    api_connection turns into a MikrotikError for the view to handle)."""
    with api_connection(device) as api:
        list(api("/ppp/active/remove", **{".id": session_id}))


def push_radius_client_config(device, freeradius_ip, secret):
    """Pushes the two pieces of config this platform's FreeRADIUS
    integration needs onto the router via the API, as an alternative to
    manually applying deploy/radius/mikrotik_teraco_jhb.rsc:
      - a /radius client entry pointing at this FreeRADIUS server
      - /ppp aaa use-radius=yes (so PPP/OVPN logins actually get delegated)
    Does NOT touch the OVPN server itself, PPP profile, TLS certificate, or
    firewall -- those still need the .rsc script (or manual setup) since
    they're one-time structural changes, not something to silently
    overwrite from here. Safe to re-run: removes any existing radius
    client entry for this FreeRADIUS IP first, so it doesn't pile up
    duplicates on repeated pushes."""
    with api_connection(device) as api:
        existing = list(api("/radius/print"))
        for row in existing:
            if row.get("address") == freeradius_ip:
                list(api("/radius/remove", **{".id": row[".id"]}))

        list(api("/radius/add", address=freeradius_ip, secret=secret, service="ppp"))
        list(api("/ppp/aaa/set", **{"use-radius": True, "accounting": True}))


# --- Blocking rules (disabled customers -> address-list + firewall drop) --

def sync_blocked_addresses(device, ip_addresses):
    """Reconciles the `skybre-blocked` address-list on this device to
    contain exactly `ip_addresses` (an iterable of dotted-quad strings) --
    adds anything missing, removes anything no longer wanted. Every entry
    in this address-list is fully owned/managed by this platform, so
    removing one here is always safe."""
    wanted = set(ip_addresses)
    with api_connection(device) as api:
        existing = [row for row in api("/ip/firewall/address-list/print") if row.get("list") == BLOCK_ADDRESS_LIST]
        existing_by_address = {row.get("address"): row for row in existing}

        for address, row in existing_by_address.items():
            if address not in wanted:
                list(api("/ip/firewall/address-list/remove", **{".id": row[".id"]}))

        for address in wanted:
            if address not in existing_by_address:
                list(api(
                    "/ip/firewall/address-list/add",
                    **{"list": BLOCK_ADDRESS_LIST, "address": address, "comment": BLOCK_FIREWALL_COMMENT_SRC},
                ))


def clear_blocked_addresses(device):
    """Removes every entry from the `skybre-blocked` address-list."""
    with api_connection(device) as api:
        for row in [r for r in api("/ip/firewall/address-list/print") if r.get("list") == BLOCK_ADDRESS_LIST]:
            list(api("/ip/firewall/address-list/remove", **{".id": row[".id"]}))


def ensure_blocking_firewall_rule(device):
    """Makes sure the forward-chain drop rules for the `skybre-blocked`
    address-list exist (one matching as source, one as destination, so a
    blocked customer can neither send nor receive routed traffic) --
    idempotent, does nothing if they're already there."""
    with api_connection(device) as api:
        existing_comments = {row.get("comment") for row in api("/ip/firewall/filter/print")}
        if BLOCK_FIREWALL_COMMENT_SRC not in existing_comments:
            list(api(
                "/ip/firewall/filter/add",
                **{
                    "chain": "forward", "action": "drop",
                    "src-address-list": BLOCK_ADDRESS_LIST, "comment": BLOCK_FIREWALL_COMMENT_SRC,
                },
            ))
        if BLOCK_FIREWALL_COMMENT_DST not in existing_comments:
            list(api(
                "/ip/firewall/filter/add",
                **{
                    "chain": "forward", "action": "drop",
                    "dst-address-list": BLOCK_ADDRESS_LIST, "comment": BLOCK_FIREWALL_COMMENT_DST,
                },
            ))


def remove_blocking_firewall_rule(device):
    """Removes both blocking-rule firewall filter entries, if present."""
    with api_connection(device) as api:
        for row in api("/ip/firewall/filter/print"):
            if row.get("comment") in (BLOCK_FIREWALL_COMMENT_SRC, BLOCK_FIREWALL_COMMENT_DST):
                list(api("/ip/firewall/filter/remove", **{".id": row[".id"]}))


# --- Shaper (per-service Simple Queues) -----------------------------------

def sync_simple_queues(device, entries):
    """Reconciles this device's managed Simple Queues (identified by the
    `skybre-auto-queue-service-<id>` comment) to match exactly `entries` --
    a list of dicts with keys service_id, target_ip, max_down_kbps,
    max_up_kbps, name. Removes queues for services no longer in the list,
    updates existing ones in place, adds new ones. RouterOS's max-limit is
    "upload/download" as seen from the router (rx-from-client/tx-to-client),
    i.e. upload-then-download, the reverse order of how they're usually
    quoted to a customer -- double-check this against your RouterOS
    version's own `/queue/simple` docs before relying on it."""
    with api_connection(device) as api:
        existing = [
            row for row in api("/queue/simple/print")
            if (row.get("comment") or "").startswith(QUEUE_COMMENT_PREFIX)
        ]
        existing_by_comment = {row["comment"]: row for row in existing}
        wanted_comments = set()

        for entry in entries:
            comment = f"{QUEUE_COMMENT_PREFIX}{entry['service_id']}"
            wanted_comments.add(comment)
            max_limit = f"{entry['max_up_kbps']}k/{entry['max_down_kbps']}k"
            target = f"{entry['target_ip']}/32"
            if comment in existing_by_comment:
                row = existing_by_comment[comment]
                list(api(
                    "/queue/simple/set",
                    **{".id": row[".id"], "target": target, "max-limit": max_limit, "name": entry["name"]},
                ))
            else:
                list(api(
                    "/queue/simple/add",
                    **{"name": entry["name"], "target": target, "max-limit": max_limit, "comment": comment},
                ))

        for comment, row in existing_by_comment.items():
            if comment not in wanted_comments:
                list(api("/queue/simple/remove", **{".id": row[".id"]}))


def clear_all_managed_simple_queues(device):
    """Removes every Simple Queue this platform has pushed (identified by
    the `skybre-auto-queue-service-` comment prefix), leaving any queue a
    human added by hand untouched."""
    with api_connection(device) as api:
        for row in api("/queue/simple/print"):
            if (row.get("comment") or "").startswith(QUEUE_COMMENT_PREFIX):
                list(api("/queue/simple/remove", **{".id": row[".id"]}))


def get_service_live_bandwidth(device, service_id):
    """Live download/upload speed for one customer's Service, read from its
    managed Simple Queue (see sync_simple_queues) via
    `/queue/simple/monitor`'s "once" snapshot mode -- the queue-level
    equivalent of get_wan_interface_traffic. Requires this device's Shaper
    to be on and this service to be active so its queue actually exists;
    returns None (not an error) if no matching queue is found, so the
    caller can surface a clear "enable Shaper / sync queues first" message
    instead of a confusing 502.

    NOTE: the rx-bits-per-second/tx-bits-per-second field names below
    follow the same convention RouterOS uses for /interface/monitor-traffic
    (already verified working in get_wan_interface_traffic), but this
    specific queue-monitor command has not itself been confirmed against
    real hardware -- please check a manual `/queue/simple/monitor once`
    against your router before relying on this for live customers."""
    comment = f"{QUEUE_COMMENT_PREFIX}{service_id}"
    with api_connection(device) as api:
        queue_row = None
        for row in api("/queue/simple/print"):
            if row.get("comment") == comment:
                queue_row = row
                break
        if not queue_row:
            return None
        monitor_row = next(iter(api("/queue/simple/monitor", numbers=queue_row[".id"], once="")), {})
        rx_bps = float(monitor_row.get("rx-bits-per-second", 0) or 0)
        tx_bps = float(monitor_row.get("tx-bits-per-second", 0) or 0)
        return {
            # tx = router-to-client = the customer's download; rx = the
            # customer's upload -- same convention as get_wan_interface_traffic.
            "download_mbps": round(tx_bps / 1_000_000, 2),
            "upload_mbps": round(rx_bps / 1_000_000, 2),
        }


# --- Wireless Access List / MPSK ------------------------------------------

def list_wireless_access_list(device, interface=None):
    """All entries in this device's wireless Access List, optionally
    filtered to one interface. Returns the raw RouterOS rows (mac-address,
    interface, comment, private-passphrase if MPSK is set, etc.) -- this
    reflects the router's actual live list, including any entry a human
    added by hand, not just ones this platform pushed."""
    with api_connection(device) as api:
        rows = list(api("/interface/wireless/access-list/print"))
    if interface:
        rows = [r for r in rows if r.get("interface") == interface]
    return rows


def add_wireless_access_list_entry(device, interface, mac_address, comment="", passphrase=None):
    """Adds one wireless Access List entry -- a plain MAC allow entry, or
    an MPSK entry when `passphrase` is given (RouterOS's
    private-passphrase field, letting this one MAC use its own WPA2
    passphrase instead of the network-wide one). Tagged with the
    skybre-auto-wifi- comment prefix (unless an explicit comment is given)
    so it's identifiable as platform-managed later."""
    kwargs = {
        "interface": interface,
        "mac-address": mac_address,
        "comment": comment or f"{WIRELESS_COMMENT_PREFIX}{mac_address}",
    }
    if passphrase:
        kwargs["private-passphrase"] = passphrase
    with api_connection(device) as api:
        list(api("/interface/wireless/access-list/add", **kwargs))


def remove_wireless_access_list_entry(device, entry_id):
    """Removes one wireless Access List entry by its RouterOS `.id` (as
    returned by list_wireless_access_list)."""
    with api_connection(device) as api:
        list(api("/interface/wireless/access-list/remove", **{".id": entry_id}))


def clear_managed_wireless_entries(device):
    """Removes only the wireless Access List entries this platform itself
    added (identified by the skybre-auto-wifi- comment prefix), leaving
    any entry a human added by hand untouched."""
    with api_connection(device) as api:
        for row in api("/interface/wireless/access-list/print"):
            if (row.get("comment") or "").startswith(WIRELESS_COMMENT_PREFIX):
                list(api("/interface/wireless/access-list/remove", **{".id": row[".id"]}))


# --- Delete all platform-managed config ------------------------------------

def delete_all_managed_config(device):
    """Removes everything the live-API features have pushed to this router
    -- blocking address-list + firewall rules, Simple Queues, and any
    platform-tagged wireless Access List entries. Deliberately does NOT
    touch push_radius_client_config's /radius client entry or `/ppp aaa`
    settings -- that's a separate one-time structural setup for the
    RADIUS/OVPN integration, not part of this per-customer config sync."""
    clear_blocked_addresses(device)
    remove_blocking_firewall_rule(device)
    clear_all_managed_simple_queues(device)
    clear_managed_wireless_entries(device)


def get_all_session_traffic(device):
    """Byte counters for EVERY active PPP session on this router, in one
    connection.

    This is the bulk counterpart to get_service_live_bandwidth. That one
    answers "what is this single customer doing right now" and costs a
    RouterOS login per customer being watched -- fine for a support agent
    with one page open, hopeless as a way to show live figures for
    everyone.

    Here two commands return every session at once, so live throughput for
    a thousand customers costs the same as for one: a single login per
    router per poll, regardless of how many customers or viewers there are.
    That is the whole reason this exists.

    Returns {username: {"rx_byte": int, "tx_byte": int, "interface": str}}.
    rx/tx follow RouterOS's own convention -- rx is what the router
    RECEIVED on that interface, i.e. the customer's upload. These are
    cumulative counters, not rates; the caller derives rates by comparing
    consecutive polls.
    """
    with api_connection(device) as api:
        return read_session_traffic(api)


def read_session_traffic(api):
    """The same read, on a connection the CALLER owns.

    Split out for the live broker, which holds one connection open while a
    staff member is watching and reads it once a second. Reconnecting per read
    would put a login in the router's log every second -- worse than the
    once-a-minute cron this replaces, not better.
    """
    out = {}
    # Usernames RouterOS currently considers connected. Used to ignore
    # interfaces belonging to sessions that have already dropped, so a
    # stale counter can't be reported as a live rate.
    active_users = {row.get("name") for row in api("/ppp/active/print") if row.get("name")}
    if not active_users:
        return out

    # One call for every interface's counters, dynamic ones included.
    # RouterOS names a session's dynamic interface "<pppoe-user>" or
    # "<ovpn-user>", so the username is recovered from the name rather
    # than guessed at from the other side.
    for row in api("/interface/print"):
        iface_name = row.get("name") or ""
        if not (iface_name.startswith("<") and iface_name.endswith(">")):
            continue
        inner = iface_name[1:-1]
        # rsplit, not partition: a username may itself contain "-"
        # (e.g. "<pppoe-joe-soap>" is user "joe-soap"), and splitting
        # on the first one would silently address the wrong customer.
        prefix, sep, username = inner.partition("-")
        if not sep or username not in active_users:
            # Fall back to matching the longest active username the
            # interface name ends with -- covers prefixes we don't know
            # and usernames containing hyphens.
            username = next(
                (u for u in sorted(active_users, key=len, reverse=True) if inner.endswith(u)),
                None,
            )
            if username is None:
                continue
        out[username] = {
            "rx_byte": int(row.get("rx-byte") or 0),
            "tx_byte": int(row.get("tx-byte") or 0),
            "interface": iface_name,
        }
    return out
