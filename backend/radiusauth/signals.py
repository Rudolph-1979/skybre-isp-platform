"""Keeps FreeRADIUS's `radcheck`/`radreply` tables in sync with
billing.Service's RADIUS fields and billing.Tariff's speeds, so staff never
touch those tables by hand -- they just set a service's RADIUS
username/password (see billing.serializers.ServiceSerializer) and
everything downstream (auth check item, assigned IP, Mikrotik-Rate-Limit
bandwidth cap) is derived automatically.

Two independent Framed-IP-Address allocation paths live here, kept
deliberately separate:
  - OVPN (Service.radius_connection_type == "ovpn", the default -- the
    original Teraco Mikrotik OVPN setup): _allocate_network_ip, unchanged
    since it was first built. Always pulls from a "network"-category
    IPPool.
  - PPPoE (Service.radius_connection_type == "pppoe"): a newer feature
    letting staff choose manual / pool / auto IP assignment per service --
    see _resolve_framed_ip, _allocate_customer_ip, assign_specific_customer_ip,
    release_customer_ip below. Always pulls from a "customer"-category
    IPPool (or a plain staff-entered static_ip for manual mode).
Every existing service defaults to "ovpn", so this feature changes nothing
for a service unless staff explicitly switch it to "pppoe".

A third allocation path, _allocate_walled_garden_ip/release_walled_garden_ip,
handles PPPoE services while SUSPENDED (however that happened -- a staff
member clicking Suspend, or any future automated process, since this is
keyed purely off Service.status via the post_save signal below, not off
who/what changed it): rather than being hard-rejected like before, they
still authenticate but land on a "walled_garden"-category IPPool with no
real internet route, so the router can show a captive "please pay" page
instead of just dropping them. Their normal Customer/Net pool address is
left assigned (not released) the whole time they're suspended, so they get
the exact same IP back the instant they're reactivated. OVPN services, and
PPPoE services that are terminated/pending rather than suspended, keep the
previous hard-reject behavior -- see sync_service_radius.
"""

import logging

from django.db import transaction
from django.db.models.signals import post_save, post_delete, pre_delete, pre_save
from django.dispatch import receiver

from billing.models import Service, Tariff
from network.models import IPAddress, IPPool
from .clients_conf import write_clients_conf_spool
from .models import RadCheck, RadReply, RadiusNasClient

logger = logging.getLogger(__name__)


def _clear_radius_entries(username):
    RadCheck.objects.filter(username=username).delete()
    RadReply.objects.filter(username=username).delete()


def _mikrotik_rate_limit(service):
    """Mikrotik-Rate-Limit reply attribute value for one service.

    Format is "rx-rate/tx-rate" the way MikroTik's RADIUS client expects
    it -- rx is the rate FROM the client (the customer's upload cap) and
    tx is the rate TO the client (their download cap). See the Mikrotik
    .rsc script (RADIUS_SETUP.md) for how the router applies it.

    Emitted with a `k` suffix because speeds are stored in Kbps. This
    used to append `M` to the same number: a 4 Mbps plan stored as 4096
    became "4096M/4096M" -- four terabits, which the router accepts and
    which meant the customer was never throttled at all.

    Takes a SERVICE rather than a tariff, because the answer now depends
    on more than the plan: the line's own Connection Rule override, which
    speed window is on right now, and whether it is past its fair-use
    threshold. radiusauth.speeds is the single place those rules live --
    everything that sets a speed goes through it, so the value written
    into radreply for the next login and the value pushed to a live
    session by CoA can never disagree.
    """
    from .speeds import effective_speeds

    return effective_speeds(service).rate_limit


def _allocate_network_ip(service):
    """Returns the Framed-IP-Address to hand this OVPN service, allocating
    one from a "network" category IPPool if it doesn't already have one.
    Reuses the same address on every reconnect (looked up by
    assigned_service) instead of handing out a new one each time -- so a
    customer's OVPN client keeps a stable IP across sessions. Unchanged by
    the PPPoE IP-assignment feature below -- OVPN services never touch
    "customer"-category pools."""
    existing = (
        IPAddress.objects.select_related("pool")
        .filter(assigned_service=service, pool__category=IPPool.Category.NETWORK)
        .first()
    )
    if existing:
        return existing.address

    with transaction.atomic():
        free = (
            IPAddress.objects.select_for_update(skip_locked=True)
            .filter(pool__category=IPPool.Category.NETWORK, status=IPAddress.Status.FREE)
            .order_by("id")
            .first()
        )
        if not free:
            logger.warning(
                "No free addresses in any Net IP Pool -- service %s (radius user %s) will "
                "authenticate without a Framed-IP-Address until one is freed up or a Net "
                "IP Pool is added/expanded.",
                service.id, service.radius_username,
            )
            return None
        free.status = IPAddress.Status.ASSIGNED
        free.assigned_service = service
        free.save(update_fields=["status", "assigned_service"])
        return free.address


def _allocate_customer_ip(service):
    """Returns the Framed-IP-Address to hand this PPPoE service in
    pool/auto mode. Reuses whatever "customer"-category address is
    already assigned to it (whether staff explicitly picked it via
    assign_specific_customer_ip, or it was auto-picked here before) so the
    customer keeps a stable IP across reconnects.

    If nothing is assigned yet: auto mode picks the next free address out
    of service.ip_pool itself; pool (explicit-selection) mode does NOT
    auto-pick -- it's staff's job to choose one via the `ip_address` field
    on ServiceSerializer, so this simply returns None (no Framed-IP-Address
    reply row) until they do."""
    existing = (
        IPAddress.objects.select_related("pool")
        .filter(assigned_service=service, pool__category=IPPool.Category.CUSTOMER)
        .first()
    )
    if existing:
        return existing.address

    if service.ip_assignment_mode != Service.IPAssignmentMode.AUTO:
        return None

    if not service.ip_pool:
        logger.warning(
            "Service %s is in 'auto' PPPoE IP assignment mode but has no ip_pool set -- "
            "nothing to allocate from.", service.id,
        )
        return None

    with transaction.atomic():
        free = (
            IPAddress.objects.select_for_update(skip_locked=True)
            .filter(pool=service.ip_pool, status=IPAddress.Status.FREE)
            .order_by("id")
            .first()
        )
        if not free:
            logger.warning(
                "No free addresses in Customer IP Pool '%s' -- service %s (radius user %s) "
                "will authenticate without a Framed-IP-Address until one is freed up or the "
                "pool is expanded.",
                service.ip_pool.name, service.id, service.radius_username,
            )
            return None
        free.status = IPAddress.Status.ASSIGNED
        free.assigned_service = service
        free.save(update_fields=["status", "assigned_service"])
        return free.address


def release_customer_ip(service):
    """Frees whatever "customer"-category IP Pool address this service
    currently holds, if any -- used when a service switches away from
    pppoe/pool/auto mode, or changes to a different ip_pool, so the old
    address goes back into circulation instead of being stranded."""
    IPAddress.objects.filter(assigned_service=service, pool__category=IPPool.Category.CUSTOMER).update(
        status=IPAddress.Status.FREE, assigned_service=None
    )


def release_network_ip(service):
    """Frees whatever "network"-category (Net IP Pool) address this service
    holds -- the mirror of release_customer_ip, for a service that has
    stopped being OVPN.

    Without this, switching a service from OVPN to PPPoE left its old Net
    Pool address assigned forever: _allocate_customer_ip correctly hands it
    a Customer Pool address, but nothing reclaimed the OVPN one, so the
    service held two addresses and the Net Pool leaked one per switch.
    Invisible in the UI -- the address simply reads "assigned" to a service
    that no longer uses it -- which is why it went unnoticed until a
    connection type was actually changed, on the bench on 2026-08-21.
    """
    IPAddress.objects.filter(assigned_service=service, pool__category=IPPool.Category.NETWORK).update(
        status=IPAddress.Status.FREE, assigned_service=None
    )


def _allocate_walled_garden_ip(service):
    """Returns the Framed-IP-Address to hand this PPPoE service while it's
    SUSPENDED -- pulled from any "walled_garden"-category IPPool (a shared,
    non-customer-specific pool, same pattern as _allocate_network_ip),
    rather than the hard reject services used to get when suspended. Their
    normal Customer Pool address is deliberately left assigned (not
    released) so _resolve_framed_ip finds it again automatically the
    moment they're reactivated -- see release_walled_garden_ip for the
    reverse direction."""
    existing = (
        IPAddress.objects.select_related("pool")
        .filter(assigned_service=service, pool__category=IPPool.Category.WALLED_GARDEN)
        .first()
    )
    if existing:
        return existing.address

    with transaction.atomic():
        free = (
            IPAddress.objects.select_for_update(skip_locked=True)
            .filter(pool__category=IPPool.Category.WALLED_GARDEN, status=IPAddress.Status.FREE)
            .order_by("id")
            .first()
        )
        if not free:
            logger.warning(
                "No free addresses in any Walled Garden IP Pool -- suspended service %s (radius user %s) "
                "will authenticate without a Framed-IP-Address until one is freed up or a Walled Garden "
                "IP Pool is added/expanded.",
                service.id, service.radius_username,
            )
            return None
        free.status = IPAddress.Status.ASSIGNED
        free.assigned_service = service
        free.save(update_fields=["status", "assigned_service"])
        return free.address


def release_walled_garden_ip(service):
    """Frees whatever Walled Garden IP Pool address this service currently
    holds, if any -- called once a suspended service stops being suspended
    (reactivated or terminated), so the address goes back into circulation
    for the next customer who gets suspended."""
    IPAddress.objects.filter(assigned_service=service, pool__category=IPPool.Category.WALLED_GARDEN).update(
        status=IPAddress.Status.FREE, assigned_service=None
    )


def assign_specific_customer_ip(service, address_id):
    """Explicitly assigns one particular Customer IP Pool address to a
    service -- staff picked it directly (pool mode) rather than leaving
    auto-pick to _allocate_customer_ip. Frees whatever OTHER
    customer-category address the service held before, if any. Raises
    ValueError (meant to be caught and surfaced as a DRF ValidationError by
    the caller) if the address doesn't exist, isn't in a Customer-category
    pool, or is already assigned to a different service."""
    try:
        target = IPAddress.objects.select_related("pool").get(pk=address_id)
    except IPAddress.DoesNotExist:
        raise ValueError("That IP address no longer exists.")
    if target.pool.category != IPPool.Category.CUSTOMER:
        raise ValueError("Only an address from a Customer IP Pool can be assigned to a PPPoE service.")
    if target.assigned_service_id not in (None, service.pk):
        raise ValueError(f"{target.address} is already assigned to another service.")

    with transaction.atomic():
        IPAddress.objects.filter(
            assigned_service=service, pool__category=IPPool.Category.CUSTOMER
        ).exclude(pk=target.pk).update(status=IPAddress.Status.FREE, assigned_service=None)
        target.status = IPAddress.Status.ASSIGNED
        target.assigned_service = service
        target.save(update_fields=["status", "assigned_service"])


def _walled_garden_eligible(service):
    """Whether this service should get the walled-garden treatment
    (authenticate, but land on a no-internet pool) instead of a hard
    reject -- suspended PPPoE services only. OVPN suspensions, and any
    other non-active PPPoE status (terminated/pending), still hard-reject:
    OVPN is just the connection to the authentication router itself, not a
    customer-facing service staff suspend for non-payment the way PPPoE
    is, so there's no walled-garden portal use case for it here."""
    return (
        service.status == Service.Status.SUSPENDED
        and service.radius_connection_type == Service.ConnectionType.PPPOE
    )


def _resolve_framed_ip(service):
    """The Framed-IP-Address to hand this service on login, if any.
    Branches on suspension first (see _walled_garden_eligible), then on
    radius_connection_type, so the original OVPN Net-IP-Pool behavior is
    completely untouched by the newer PPPoE manual/pool/auto feature."""
    if _walled_garden_eligible(service):
        return _allocate_walled_garden_ip(service)
    if service.radius_connection_type == Service.ConnectionType.PPPOE:
        if service.ip_assignment_mode == Service.IPAssignmentMode.MANUAL:
            return service.static_ip or None
        # pool and auto both resolve through the same allocator -- see its
        # docstring for how the two modes differ.
        return _allocate_customer_ip(service)
    return _allocate_network_ip(service)


def sync_service_radius(service):
    """The single source of truth for what a Service's RADIUS rows should
    look like right now. Always deletes-and-recreates rather than patching
    individual rows in place -- simpler to reason about and stays correct
    no matter what changed (username, password, status, tariff, IP
    assignment...).

    All of it in ONE transaction, and the slow part computed before the
    delete. Both matter because FreeRADIUS is reading these tables the
    whole time, from a different process.

    ATOMIC_REQUESTS is not set, so this used to run in autocommit: the
    DELETE committed on its own, and the Cleartext-Password row was only
    re-inserted afterwards -- after IP allocation and after
    _mikrotik_rate_limit, which reaches through speeds.effective_speeds
    into a month of hourly UsageBucket rows. A customer whose CPE
    re-dialled inside that window found no radcheck row and got an
    Access-Reject. Worse, any exception in between -- an IntegrityError on
    an address, a database hiccup, a request timing out -- left the
    subscriber with NO auth rows at all, unable to authenticate until
    somebody noticed and re-saved the service by hand.

    Resolving the rate limit up front shrinks the window to the writes
    themselves; the transaction means a failure anywhere in it puts the
    old rows back rather than leaving the line stranded.
    """
    username = service.radius_username
    if not username:
        return

    # Resolved before anything is deleted: this is the expensive read, and
    # it must not sit between the delete and the recreate.
    rate_limit = _mikrotik_rate_limit(service)

    with transaction.atomic():
        _sync_service_radius_rows(service, username, rate_limit)


def _sync_service_radius_rows(service, username, rate_limit):
    """The write half of sync_service_radius, inside its transaction."""
    _clear_radius_entries(username)

    walled_garden = _walled_garden_eligible(service)

    if service.status != Service.Status.ACTIVE and not walled_garden:
        # Terminated/pending services (and suspended OVPN ones) keep their
        # RADIUS username reserved but can't authenticate at all -- an
        # explicit reject is clearer in FreeRADIUS's logs than silently
        # having no matching rows. Suspended PPPoE services fall through
        # instead, so they still authenticate onto the walled-garden pool.
        RadCheck.objects.create(username=username, attribute="Auth-Type", op=":=", value="Reject")
        return

    walled_garden_ip = None
    if walled_garden:
        # Resolved HERE, before anything is written, because the whole point
        # of letting a suspended service authenticate is that it lands on a
        # no-internet address. If there isn't one to give it, allowing the
        # login is worse than refusing it: FreeRADIUS would return a valid
        # password and no Framed-IP-Address, the router would fall back to its
        # own local PPP pool, and the suspended customer would come back
        # online with FULL INTERNET. This used to be exactly what happened
        # whenever no Walled Garden IP Pool existed or it had run out of
        # addresses -- suspension silently did nothing.
        #
        # So: fail closed. No walled-garden address means reject.
        walled_garden_ip = _allocate_walled_garden_ip(service)
        if not walled_garden_ip:
            logger.warning(
                "Suspended service %s (radius user %s) is being REJECTED rather than walled-gardened: "
                "no free Walled Garden IP Pool address. Add or expand a Walled Garden pool under "
                "Networking -> IP Pools to show a 'please pay' portal instead of refusing the login.",
                service.id, username,
            )
            RadCheck.objects.create(username=username, attribute="Auth-Type", op=":=", value="Reject")
            return

    if not service.radius_password:
        # Active but no password set yet -- nothing to check the client's
        # credentials against, so there's genuinely nothing useful to write.
        return

    RadCheck.objects.create(
        username=username, attribute="Cleartext-Password", op=":=", value=service.radius_password
    )

    # Already resolved above for a walled-garden service, precisely so the
    # "no address available" case could refuse the login before getting here.
    ip = walled_garden_ip or _resolve_framed_ip(service)
    if ip:
        RadReply.objects.create(username=username, attribute="Framed-IP-Address", op="=", value=ip)

    RadReply.objects.create(
        username=username, attribute="Mikrotik-Rate-Limit", op="=", value=rate_limit
    )


@receiver(pre_save, sender=Service)
def _service_pre_save_capture_old_state(sender, instance, **kwargs):
    """Stashes the previous radius_username (so post_save can clean up the
    OLD username's rows if it was just renamed), the previous
    connection-type/assignment-mode/ip_pool (so post_save can release a
    now-stale customer-pool address before re-syncing), and the previous
    status (so post_save can release a now-stale walled-garden address
    once a suspended service stops being suspended) on the instance."""
    if instance.pk:
        try:
            old = Service.objects.only(
                "radius_username", "radius_connection_type", "ip_assignment_mode", "ip_pool_id", "status"
            ).get(pk=instance.pk)
            instance._old_radius_username = old.radius_username
            instance._old_connection_type = old.radius_connection_type
            instance._old_ip_assignment_mode = old.ip_assignment_mode
            instance._old_ip_pool_id = old.ip_pool_id
            instance._old_status = old.status
        except Service.DoesNotExist:
            instance._old_radius_username = None
            instance._old_connection_type = None
            instance._old_ip_assignment_mode = None
            instance._old_ip_pool_id = None
            instance._old_status = None
    else:
        instance._old_radius_username = None
        instance._old_connection_type = None
        instance._old_ip_assignment_mode = None
        instance._old_ip_pool_id = None
        instance._old_status = None


@receiver(post_save, sender=Service)
def _service_post_save_sync_radius(sender, instance, created, **kwargs):
    old_username = getattr(instance, "_old_radius_username", None)
    if old_username and old_username != instance.radius_username:
        _clear_radius_entries(old_username)

    # If this service just moved away from pppoe/pool-or-auto mode, or
    # changed to a different Customer IP Pool, its old customer-category
    # address (if any) is now stale -- free it up so sync_service_radius
    # (via _allocate_customer_ip) picks/keeps the right one below instead
    # of leaving an orphaned assignment behind.
    old_conn_type = getattr(instance, "_old_connection_type", None)
    old_mode = getattr(instance, "_old_ip_assignment_mode", None)
    old_pool_id = getattr(instance, "_old_ip_pool_id", None)
    no_longer_pppoe_pool_based = not (
        instance.radius_connection_type == Service.ConnectionType.PPPOE
        and instance.ip_assignment_mode in (Service.IPAssignmentMode.POOL, Service.IPAssignmentMode.AUTO)
    )
    was_pppoe_pool_based = old_conn_type == Service.ConnectionType.PPPOE and old_mode in (
        Service.IPAssignmentMode.POOL, Service.IPAssignmentMode.AUTO,
    )
    pool_changed = was_pppoe_pool_based and old_pool_id is not None and old_pool_id != instance.ip_pool_id
    if (was_pppoe_pool_based and no_longer_pppoe_pool_based) or pool_changed:
        release_customer_ip(instance)

    # The mirror of the block above, which was missing: a service that has
    # stopped being OVPN no longer needs its Net Pool address. Without this,
    # an OVPN -> PPPoE switch left the old address assigned forever and the
    # service silently held two -- see release_network_ip's docstring.
    if (
        old_conn_type == Service.ConnectionType.OVPN
        and instance.radius_connection_type != Service.ConnectionType.OVPN
    ):
        release_network_ip(instance)

    # Leaving PPPoE also ends walled-garden eligibility (only suspended
    # PPPoE services get one -- see _walled_garden_eligible), so a suspended
    # service switched to OVPN would otherwise strand its walled-garden
    # address the same way. The status-keyed release below doesn't catch
    # this, because the status never changed.
    if (
        old_conn_type == Service.ConnectionType.PPPOE
        and instance.radius_connection_type != Service.ConnectionType.PPPOE
    ):
        release_walled_garden_ip(instance)

    # However suspension ended -- reactivated, or terminated instead --
    # this service no longer needs its walled-garden address (see
    # _allocate_walled_garden_ip/release_walled_garden_ip's docstrings).
    # Keyed purely on the status field itself, so this fires no matter
    # what changed it: a staff member clicking a status dropdown, or any
    # automated process that saves the same field later.
    old_status = getattr(instance, "_old_status", None)
    if old_status == Service.Status.SUSPENDED and instance.status != Service.Status.SUSPENDED:
        release_walled_garden_ip(instance)

    sync_service_radius(instance)


@receiver(pre_delete, sender=Service)
def _service_pre_delete_capture_ip_addresses(sender, instance, **kwargs):
    """Django's own on_delete=SET_NULL handling for IPAddress.assigned_service
    nulls that FK as part of the delete itself -- BEFORE post_delete fires --
    so by the time post_delete runs, a lookup filtered by
    assigned_service=instance would already find nothing. Capture the
    affected ids here, while the FK still points at this service, so
    post_delete can free them by id instead."""
    instance._ip_address_ids_to_free = list(
        IPAddress.objects.filter(assigned_service=instance).values_list("id", flat=True)
    )


@receiver(post_delete, sender=Service)
def _service_post_delete_clear_radius(sender, instance, **kwargs):
    if instance.radius_username:
        _clear_radius_entries(instance.radius_username)
    # Free up whatever IP Pool address(es) it was holding (Net or
    # Customer), so they go back into circulation for the next service
    # that needs one -- see _service_pre_delete_capture_ip_addresses above
    # for why this can't just filter by assigned_service=instance here.
    ids = getattr(instance, "_ip_address_ids_to_free", [])
    if ids:
        IPAddress.objects.filter(id__in=ids).update(status=IPAddress.Status.FREE, assigned_service=None)


# The only Tariff fields Mikrotik-Rate-Limit is derived from --
# speeds.plan_speeds() reads the two speed columns, and speeds.effective_speeds()
# layers fair use on top of them. Everything else on a Tariff (price, name,
# description, tax rate, billing period, is_active, data_cap_gb) changes
# nothing about what the router is told, so a save that touches only those
# must not reach the network at all.
_TARIFF_RADIUS_FIELDS = (
    "speed_download_kbps",
    "speed_upload_kbps",
    "fup_threshold_gb",
    "fup_speed_pct",
)


@receiver(pre_save, sender=Tariff)
def _tariff_pre_save_capture_old_speeds(sender, instance, **kwargs):
    """Stashes the previous speed/fair-use values so post_save can tell a
    real speed change from a price edit. Same shape as
    _service_pre_save_capture_old_state above, and as
    network.signals' _network_old_* capture.

    A missing row leaves the marker as None, which post_save reads as
    "can't establish what changed" and treats as no change -- failing
    toward not touching the router, the safe direction.
    """
    if not instance.pk:
        instance._old_radius_speeds = None
        return
    try:
        old = Tariff.objects.only(*_TARIFF_RADIUS_FIELDS).get(pk=instance.pk)
    except Tariff.DoesNotExist:
        instance._old_radius_speeds = None
        return
    instance._old_radius_speeds = {f: getattr(old, f) for f in _TARIFF_RADIUS_FIELDS}


@receiver(post_save, sender=Tariff)
def _tariff_post_save_resync_services(sender, instance, created, **kwargs):
    """A tariff's speed is what Mikrotik-Rate-Limit is derived from -- if
    staff edit a tariff's speed after the fact, every RADIUS-enabled
    service on that tariff needs its reply rows regenerated to match.

    Regenerating the rows is only half of it. The rate limit is applied by the
    router AT LOGIN, so a live session keeps the queue it was given whenever it
    last connected -- meaning a speed upgrade you sold someone silently does
    not reach them until they happen to reconnect. Same shape as the
    suspension bug. Handled inside sync_service_radius' caller
    here rather than in network.signals, because a Service.save() with no
    status change is exactly the case network.signals deliberately ignores.

    Gated on the speed/fair-use fields actually changing. This used to fire
    on EVERY non-create Tariff.save(), so correcting a typo in a plan's
    description, or putting its price up, re-synced and then dropped the
    live session of every customer on it -- a few hundred paid-up people
    knocked offline by an edit that changed nothing they could observe,
    with no RadiusAction row to say it had happened. network.signals'
    _service_post_save has always compared old and new for exactly this
    reason; this handler simply never got the same treatment.

    Two further differences from the version this replaces, both taken from
    the reasoning already written down elsewhere in the codebase:

      * It goes through enforcement.apply_change(reason="tariff") rather
        than calling disconnect_service_sessions directly. A speed change
        is precisely the case CoA handles WITHOUT dropping the customer
        (see network.signals: "delivering an upgrade by cutting someone
        off has always been the wrong shape"), and apply_change records
        the outcome in RadiusAction either way.
      * allow_disconnect_fallback=False, for the reason apply_change's own
        docstring gives about the scheduled speed-policy run: this fans
        out across every customer on the plan at once, so a CoA that fails
        network-wide (wrong shared secret, UDP 3799 blocked) would
        otherwise disconnect the entire plan in order to deliver a speed
        change. The new speed is already in radreply, so it lands at their
        next reconnect regardless.
    """
    if created:
        return
    old_speeds = getattr(instance, "_old_radius_speeds", None)
    if not old_speeds:
        return
    if all(old_speeds[f] == getattr(instance, f) for f in _TARIFF_RADIUS_FIELDS):
        return

    from network.signals import _after_commit_in_background

    for service in instance.services.exclude(radius_username__isnull=True).exclude(radius_username=""):
        sync_service_radius(service)
        # Only an ACTIVE service has a session worth re-establishing; a
        # suspended one is on the walled garden and will pick the new speed
        # up when it is reactivated anyway.
        if service.status == Service.Status.ACTIVE:
            _after_commit_in_background(
                f"rate-limit refresh for service {service.pk}",
                lambda svc=service: _apply_tariff_speed_change(svc),
            )


def _apply_tariff_speed_change(service):
    # Imported at call time, not module scope: radiusauth.enforcement reaches
    # back into network.router_sync, which imports this module during app-ready.
    from .enforcement import apply_change

    return apply_change(service, "tariff", allow_disconnect_fallback=False)


# ---------------------------------------------------------------------------
# RADIUS Clients -> FreeRADIUS clients.conf
# ---------------------------------------------------------------------------
# Editing a NAS client (its IP, secret, or whether it's active) in the admin
# panel used to change nothing on the FreeRADIUS side until a staff member
# manually ran render_clients_conf, copied the output out of the container,
# moved it into /etc/freeradius/3.0/clients.conf.d/ and reloaded the daemon.
# In practice that meant the IP shown in the UI and the IP FreeRADIUS
# actually trusted could silently disagree -- and a request from an
# unlisted NAS is dropped, so the symptom is "customer can't log in" with
# nothing obviously wrong on screen.
#
# These write the rendered config to a host-mounted spool file instead; a
# systemd path unit on the host validates and installs it. See
# clients_conf.py for why the container can't do the reload itself, and why
# the validation deliberately happens on the host side.
#
# transaction.on_commit so the render reflects committed state -- rendering
# inside the transaction could spool a config for a row that then rolls back.


def _spool_clients_conf(reason):
    transaction.on_commit(lambda: write_clients_conf_spool(reason=reason))


@receiver(post_save, sender=RadiusNasClient)
def _nas_client_post_save_spool(sender, instance, created, **kwargs):
    _spool_clients_conf(f"RadiusNasClient {'created' if created else 'updated'}: {instance.shortname}")


@receiver(post_delete, sender=RadiusNasClient)
def _nas_client_post_delete_spool(sender, instance, **kwargs):
    _spool_clients_conf(f"RadiusNasClient deleted: {instance.shortname}")
