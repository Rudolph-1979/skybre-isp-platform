"""ICMP reachability checks for RadiusNasClient devices -- powers the
online/offline status shown on Networking -> RADIUS Clients.

This is a plain network-layer ping: it confirms the NAS host currently
responds on the network, nothing more. It does NOT confirm FreeRADIUS
itself is reachable on UDP 1812/1813, that the shared secret is
correct, or that RADIUS authentication would actually succeed -- for
that, an OVPN/PPP login attempt is the only real test. Treat "online"
here as "worth investigating further if a real login fails" and
"offline" as "start here."

Uses the system `ping` binary via subprocess rather than a raw ICMP
socket: raw sockets need CAP_NET_RAW (or root), which this container
doesn't run as, whereas the distro `ping` binary is already set up
(suid-root / CAP_NET_RAW file capability) for exactly this purpose --
see backend/Dockerfile, iputils-ping.
"""

import subprocess
from concurrent.futures import ThreadPoolExecutor

# How long a single ping waits for a reply before giving up.
PING_TIMEOUT_SECONDS = 1
# Overall subprocess watchdog -- a bit above PING_TIMEOUT_SECONDS so the
# ping binary always has a chance to hit its own timeout and exit
# cleanly first; this is just a backstop against a hung/missing binary.
SUBPROCESS_TIMEOUT_SECONDS = PING_TIMEOUT_SECONDS + 2
# Bounds how many pings run at once so a long RADIUS Clients list can't
# open dozens of subprocesses simultaneously.
MAX_WORKERS = 10


def ping_host(ip_address: str) -> bool:
    """Sends a single ICMP echo request and reports whether it was
    answered within PING_TIMEOUT_SECONDS. Any error (unreachable host,
    invalid address, missing ping binary, subprocess timeout) is
    treated as "not reachable" rather than raised -- this is a
    best-effort status indicator for a UI badge, not something that
    should ever break the RADIUS Clients page."""
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", str(PING_TIMEOUT_SECONDS), ip_address],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def check_many(ip_addresses) -> dict:
    """Pings every address in `ip_addresses` in parallel (bounded by
    MAX_WORKERS) and returns {ip_address: is_reachable}. Duplicate
    addresses are only pinged once. Wall-clock time is roughly
    PING_TIMEOUT_SECONDS regardless of list size (modulo the
    MAX_WORKERS cap), not PING_TIMEOUT_SECONDS * count."""
    unique_ips = list(dict.fromkeys(ip_addresses))
    if not unique_ips:
        return {}
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(unique_ips))) as pool:
        results = list(pool.map(ping_host, unique_ips))
    return dict(zip(unique_ips, results))
