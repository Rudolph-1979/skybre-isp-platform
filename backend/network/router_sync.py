"""Bridges billing.Service / network.ConnectionRule data with the raw
RouterOS command wrappers in network.mikrotik -- kept as its own module so
mikrotik.py itself stays a pure, Django-model-free RouterOS API wrapper.
Mirrors how radiusauth/signals.py already bridges billing.Service and
network.IPPool for the RADIUS side; imports billing.models locally inside
functions (rather than at module level) to avoid any import-order surprises
between the two apps, same caution radiusauth doesn't need to take only
because it's guaranteed to load after both.
"""
import logging

from . import mikrotik

logger = logging.getLogger(__name__)


def resolve_service_ip(service):
    """The IP address this service currently has assigned, straight from
    this platform's own database -- the same value radiusauth/signals.py
    hands RADIUS as Framed-IP-Address, just read directly instead of
    recomputed, so calling this never allocates a new address as a side
    effect of a status change or a shaper/blocking sync. Returns None if
    nothing's assigned yet."""
    from billing.models import Service

    if (
        service.radius_connection_type == Service.ConnectionType.PPPOE
        and service.ip_assignment_mode == Service.IPAssignmentMode.MANUAL
    ):
        return service.static_ip or None
    addr = service.ip_addresses.first()
    return addr.address if addr else None


def effective_speed_kbps(service):
    """(download_kbps, upload_kbps) to shape this service to RIGHT NOW.

    Returns (None, None) if no usable speed is configured, so the caller
    can skip pushing a queue rather than accidentally pushing a 0kbps
    (effectively blocked) one.

    Routed through radiusauth.speeds like everything else that decides a
    speed, and that is not decoration. This function builds the RouterOS
    SIMPLE QUEUE the shaper pushes; Mikrotik-Rate-Limit from RADIUS builds
    a separate dynamic queue for the PPPoE session. If the two disagree
    the customer gets the more restrictive of them -- so a shaper still
    reading the raw plan speed would hold a 4 Mbps static queue against a
    line RADIUS had just burst to 8 Mbps, and the boost would be sent,
    accepted, and then not felt by the customer at all. Nothing on either
    screen would look wrong.

    Note the ORDER here is (down, up) -- the reverse of speeds.py's
    (up, down) and of the Mikrotik-Rate-Limit string. Kept as it was so
    every existing caller is unaffected.
    """
    if service.connection_rule_id:
        rule = service.connection_rule
        down, up = rule.speed_down_kbps or None, rule.speed_up_kbps or None
    else:
        tariff = service.tariff
        # No conversion: tariff speeds are stored in Kbps, same unit as a
        # ConnectionRule's. They used to be multiplied by 1000 on the
        # assumption they were Mbps, so a 4 Mbps plan stored as 4096 was
        # shaped to roughly 4 Gbps -- i.e. not shaped.
        down, up = tariff.speed_download_kbps or None, tariff.speed_upload_kbps or None

    # "Nothing configured" has to stay "skip this one". Consulting the
    # policy first would turn it into speeds.py's fallback default and
    # start pushing queues for services that deliberately have none.
    if down is None or up is None:
        return down, up

    from radiusauth.speeds import effective_speeds

    effective = effective_speeds(service)
    return effective.download_kbps, effective.upload_kbps


def sync_device_blocking_rules(device):
    """Reconciles device's blocking address-list + firewall rules against
    its current non-active Services (with a resolved IP). Called by the
    sync-blocking-rules action (full reconciliation) and by
    block_or_unblock_single_service (a lighter per-service nudge). Clears
    everything back off the router if the toggle itself is off. Returns
    the list of IPs now blocked (or None when disabled)."""
    from billing.models import Service

    if not device.block_disabled_customers:
        mikrotik.clear_blocked_addresses(device)
        mikrotik.remove_blocking_firewall_rule(device)
        return None

    blocked_ips = []
    for service in device.services.exclude(status=Service.Status.ACTIVE):
        ip = resolve_service_ip(service)
        if ip:
            blocked_ips.append(ip)

    mikrotik.sync_blocked_addresses(device, blocked_ips)
    mikrotik.ensure_blocking_firewall_rule(device)
    return blocked_ips


def block_or_unblock_single_service(service):
    """Lightweight hook called from billing.Service's post_save signal
    (see network/signals.py) so a single status change (e.g. suspending
    one customer) is reflected on the router immediately, without staff
    needing to remember to click "Sync now". A full reconciliation is
    still cheap enough to just re-run in full here rather than trying to
    patch one address in/out -- this only fires when the device actually
    has blocking turned on and its API configured, and any router failure
    is logged and swallowed rather than turning an unrelated Service save
    into a 500."""
    device = service.device
    if not device or not device.block_disabled_customers or not device.api_enabled:
        return
    try:
        sync_device_blocking_rules(device)
    except mikrotik.MikrotikError as exc:
        logger.warning("Couldn't sync blocking rules on %s after a service save: %s", device, exc)


def disconnect_service_sessions(service):
    """Kick this service's live PPPoE/OVPN session off the router.

    THIS is what makes a status change take effect today rather than
    eventually, in BOTH directions -- see network.signals for the reactivation
    half, which is the same problem wearing a different hat.
    Suspending a service rewrites its FreeRADIUS rows correctly -- but RADIUS
    authorization is only ever consulted at LOGIN. An already-established
    PPPoE session is not re-checked against anything, so a suspended customer
    carries on browsing on the session they already have. A stable link can
    stay up for weeks, which is why "suspended" appeared to do nothing.

    So the router has to be told to drop the session. After that the client
    reconnects, RADIUS is consulted, and the suspension is what answers --
    walled-garden address for PPPoE, hard reject for OVPN.

    Matched on the RouterOS session's `name`, which for a RADIUS-authenticated
    PPPoE session is the username the client sent, i.e. Service.radius_username.
    Best-effort and silent on failure: a router being unreachable must never
    turn a Service save (or a nightly billing run) into a 500. Returns the
    number of sessions actually dropped, for the caller that wants to report it.
    """
    device = service.device
    username = (service.radius_username or "").strip()
    if not device or not device.api_enabled or not username:
        return 0

    dropped = 0
    try:
        for session in mikrotik.get_ppp_active(device):
            if (session.get("name") or "").strip().lower() != username.lower():
                continue
            session_id = session.get(".id")
            if not session_id:
                continue
            try:
                mikrotik.disconnect_ppp_session(device, session_id)
                dropped += 1
            except mikrotik.MikrotikError as exc:
                # RouterOS reports an already-ended session as a trap, which
                # surfaces here -- not worth a warning, the session is gone
                # either way, which is the outcome we wanted.
                logger.info("Session %s for %s was already gone: %s", session_id, username, exc)
    except mikrotik.MikrotikError as exc:
        logger.warning(
            "Couldn't drop live sessions for %s on %s -- they stay online until they reconnect: %s",
            username, device, exc,
        )
        return dropped

    if dropped:
        logger.info("Dropped %d live session(s) for %s on %s", dropped, username, device)
    return dropped


def sync_device_shaper_queues(device):
    """Reconciles device's Simple Queues against its current active
    Services. Clears every managed queue back off the router if the
    toggle itself is off. Returns the list of entries now pushed (or None
    when disabled)."""
    from billing.models import Service

    if not device.enable_shaper:
        mikrotik.clear_all_managed_simple_queues(device)
        return None

    entries = []
    for service in device.services.filter(status=Service.Status.ACTIVE):
        ip = resolve_service_ip(service)
        if not ip:
            continue
        down_kbps, up_kbps = effective_speed_kbps(service)
        if not down_kbps or not up_kbps:
            continue
        entries.append({
            "service_id": service.id,
            "target_ip": ip,
            "max_down_kbps": down_kbps,
            "max_up_kbps": up_kbps,
            "name": f"skybre-service-{service.id}",
        })

    mikrotik.sync_simple_queues(device, entries)
    return entries
