from ipaddress import ip_network

from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from . import mikrotik, router_sync
from .models import Device, NetworkSite, IPPool, IPAddress, ConnectionRule, MonitoringReading
from .serializers import (
    DeviceSerializer, NetworkSiteSerializer, IPPoolSerializer, IPAddressSerializer,
    ConnectionRuleSerializer, MonitoringReadingSerializer,
)
from accounts.permissions import IsStaffMember, section_permission

HasNetworkingAccess = section_permission("networking")

# Ceiling on IPPoolViewSet.generate_addresses. A /21 of IPv4 is a little
# over 2000 addresses, which covers any realistic access pool; the limit
# exists so a mistyped /8 (or any IPv6 prefix, where a /64 is 18
# quintillion addresses) is refused with an explanation instead of trying
# to write rows until the database fills up.
MAX_GENERATED_POOL_ADDRESSES = 4096


class NetworkSiteViewSet(viewsets.ModelViewSet):
    """Physical tower/site locations hardware can be mounted at -- see
    NetworkSite's docstring. Same access tier as everything else in
    Networking (no extra partner-restriction on top, unlike Device
    itself)."""

    serializer_class = NetworkSiteSerializer
    queryset = NetworkSite.objects.prefetch_related("devices", "partners").all()
    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasNetworkingAccess]
    # The field is many-to-many now; filtering on it still means "sites this
    # partner is served from", which is the same question as before.
    filterset_fields = ["partners"]
    search_fields = ["title", "contact_person", "address", "location"]

    def perform_destroy(self, instance):
        # Device.site is SET_NULL, so this is always safe -- devices just
        # lose their site assignment rather than being blocked/cascaded.
        instance.delete()


class DeviceViewSet(viewsets.ModelViewSet):
    serializer_class = DeviceSerializer
    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasNetworkingAccess]
    filterset_fields = ["device_type", "status", "site"]
    search_fields = ["name", "ip_address", "location"]

    def get_queryset(self):
        from billing.models import Service

        qs = (
            Device.objects.prefetch_related("readings", "visible_partners")
            .select_related("site")
            # One aggregate for the whole page instead of a count per row.
            .annotate(
                access_service_total=Count(
                    "access_services",
                    filter=~Q(access_services__status=Service.Status.TERMINATED),
                    distinct=True,
                )
            )
            # Restated explicitly because annotate() DROPS Meta.ordering --
            # it introduces a GROUP BY and Django stops treating the
            # queryset as ordered. Without this the device list paginates
            # in whatever order Postgres feels like, so a row can appear on
            # two pages or on neither.
            .order_by("name")
        )
        user = self.request.user
        allowed = getattr(user, "allowed_partners", None) or []
        # Partner-visibility restriction (Device.visible_partners): empty
        # on the device means visible to everyone; empty on the *user*
        # (allowed_partners) means that staff member isn't restricted at
        # all. Only intersect the two when the acting user is themselves
        # partner-restricted -- Admin always bypasses this, same
        # convention as every other allowed_partners check in this app.
        if allowed and user.role != user.Role.ADMIN:
            qs = qs.filter(Q(visible_partners__isnull=True) | Q(visible_partners__id__in=allowed)).distinct()
        return qs

    @action(detail=True, methods=["get"], url_path="connected-customers")
    def connected_customers(self, request, pk=None):
        """Who physically connects to this device (Service.access_device).

        This is the whole reason that field is worth filling in. A record
        of where each client connects that can only be read one customer
        at a time answers nothing on the day it matters -- the question is
        always asked from the other end: this AP is down, who do we phone?

        Deliberately NOT Service.device. That one is the NAS, and on a
        wireless network a single core router carries everybody, so
        listing by it would return the entire customer book.
        """
        from billing.models import Service

        device = self.get_object()
        qs = (
            Service.objects.filter(access_device=device)
            .select_related("customer", "tariff")
            .exclude(status=Service.Status.TERMINATED)
            .order_by("customer__full_name")
        )
        # The same reseller scoping the customer list uses. A staff member
        # restricted to one partner must not learn another partner's
        # customer names through the networking page.
        allowed = getattr(request.user, "allowed_partners", None) or []
        if allowed and request.user.role != request.user.Role.ADMIN:
            qs = qs.filter(
                Q(customer__partner_id__in=allowed) | Q(customer__partner__isnull=True)
            )

        return Response(
            {
                "count": qs.count(),
                "results": [
                    {
                        "service_id": s.pk,
                        "customer_id": s.customer_id,
                        "customer_name": s.customer.full_name,
                        "customer_phone": s.customer.phone,
                        "customer_status": s.customer.status,
                        "tariff_name": s.tariff.name if s.tariff_id else "",
                        "service_status": s.status,
                        "access_detail": s.access_detail,
                    }
                    for s in qs
                ],
            }
        )

    @action(detail=True, methods=["post"], url_path="test-connection")
    def test_connection(self, request, pk=None):
        """Connects to this device's real RouterOS API right now and
        returns basic identity/version info -- confirms the API
        credentials and reachability are correct without waiting for the
        next scheduled poll."""
        device = self.get_object()
        try:
            info = mikrotik.test_connection(device)
        except mikrotik.MikrotikError as exc:
            return Response({"detail": str(exc)}, status=502)
        return Response(info)

    @action(detail=True, methods=["post"], url_path="poll-now")
    def poll_now(self, request, pk=None):
        """Polls this device's real RouterOS API right now and stores a
        real MonitoringReading -- the on-demand equivalent of what
        `poll_mikrotik_devices` does on a schedule."""
        device = self.get_object()
        try:
            resource = mikrotik.get_system_resource(device)
        except mikrotik.MikrotikError as exc:
            return Response({"detail": str(exc)}, status=502)

        cpu_pct = resource.get("cpu-load")
        memory_pct = None
        total_mem, free_mem = resource.get("total-memory"), resource.get("free-memory")
        if total_mem and free_mem is not None:
            try:
                memory_pct = round((1 - float(free_mem) / float(total_mem)) * 100, 1)
            except (TypeError, ValueError, ZeroDivisionError):
                memory_pct = None

        bandwidth_in_mbps = bandwidth_out_mbps = None
        if device.api_wan_interface:
            try:
                bandwidth_in_mbps, bandwidth_out_mbps = mikrotik.get_wan_interface_traffic(
                    device, device.api_wan_interface
                )
            except mikrotik.MikrotikError:
                # Still record the system-resource half of the reading even
                # if the interface name is wrong/missing -- a partial real
                # reading beats none.
                pass

        reading = MonitoringReading.objects.create(
            device=device,
            timestamp=timezone.now(),
            is_up=True,
            cpu_pct=cpu_pct,
            memory_pct=memory_pct,
            bandwidth_in_mbps=bandwidth_in_mbps,
            bandwidth_out_mbps=bandwidth_out_mbps,
        )
        device.status = Device.Status.ONLINE
        device.save(update_fields=["status"])
        return Response(MonitoringReadingSerializer(reading).data)

    @action(detail=True, methods=["get"], url_path="live-sessions")
    def live_sessions(self, request, pk=None):
        """Live PPP/OVPN sessions read directly from this device's own
        state via the RouterOS API -- independent of FreeRADIUS's radacct
        (Networking -> Live Sessions), which relies on the router having
        sent accounting packets."""
        device = self.get_object()
        try:
            sessions = mikrotik.get_ppp_active(device)
        except mikrotik.MikrotikError as exc:
            return Response({"detail": str(exc)}, status=502)
        return Response(sessions)

    @action(detail=True, methods=["post"], url_path="disconnect-session")
    def disconnect_session(self, request, pk=None):
        """Kicks one active PPP/OVPN session by its RouterOS `.id` (as
        returned by live_sessions above)."""
        device = self.get_object()
        session_id = request.data.get("session_id")
        if not session_id:
            return Response({"detail": "session_id is required."}, status=400)
        try:
            mikrotik.disconnect_ppp_session(device, session_id)
        except mikrotik.MikrotikError as exc:
            return Response({"detail": str(exc)}, status=502)
        return Response({"status": "disconnected"})

    @action(detail=True, methods=["post"], url_path="sync-blocking-rules")
    def sync_blocking_rules(self, request, pk=None):
        """Reconciles this device's blocking address-list + firewall rules
        against its current suspended/terminated/pending Services (see
        Device.block_disabled_customers). Safe to call any time -- also
        clears everything back off the router if the toggle is off."""
        device = self.get_object()
        try:
            blocked_ips = router_sync.sync_device_blocking_rules(device)
        except mikrotik.MikrotikError as exc:
            return Response({"detail": str(exc)}, status=502)
        return Response({"status": "synced", "blocked_ip_count": len(blocked_ips or [])})

    @action(detail=True, methods=["post"], url_path="sync-shaper-queues")
    def sync_shaper_queues(self, request, pk=None):
        """Reconciles this device's Simple Queues against its current
        active Services (see Device.enable_shaper). Safe to call any time
        -- also clears every managed queue back off the router if the
        toggle is off."""
        device = self.get_object()
        try:
            entries = router_sync.sync_device_shaper_queues(device)
        except mikrotik.MikrotikError as exc:
            return Response({"detail": str(exc)}, status=502)
        return Response({"status": "synced", "queue_count": len(entries or [])})

    @action(detail=True, methods=["post"], url_path="delete-all-rules")
    def delete_all_rules(self, request, pk=None):
        """Removes everything this platform has pushed to this router --
        blocking address-list/firewall rules, Simple Queues, and any
        platform-tagged wireless Access List entries. See
        mikrotik.delete_all_managed_config's docstring for exactly what
        this does and doesn't touch."""
        device = self.get_object()
        try:
            mikrotik.delete_all_managed_config(device)
        except mikrotik.MikrotikError as exc:
            return Response({"detail": str(exc)}, status=502)
        return Response({"status": "cleared"})

    @action(detail=True, methods=["get", "post"], url_path="wireless-access-list")
    def wireless_access_list(self, request, pk=None):
        """GET lists this device's current wireless Access List (see
        Device.wireless_interface). POST adds one entry -- pass a
        `passphrase` to add it as an MPSK entry instead of a plain MAC
        allow entry."""
        device = self.get_object()
        if request.method == "GET":
            try:
                rows = mikrotik.list_wireless_access_list(device, device.wireless_interface or None)
            except mikrotik.MikrotikError as exc:
                return Response({"detail": str(exc)}, status=502)
            return Response(rows)

        mac_address = request.data.get("mac_address")
        if not mac_address:
            return Response({"detail": "mac_address is required."}, status=400)
        if not device.wireless_interface:
            return Response({"detail": "Set this device's wireless interface name first."}, status=400)
        try:
            mikrotik.add_wireless_access_list_entry(
                device, device.wireless_interface, mac_address,
                comment=request.data.get("comment", ""),
                passphrase=request.data.get("passphrase") or None,
            )
        except mikrotik.MikrotikError as exc:
            return Response({"detail": str(exc)}, status=502)
        return Response({"status": "added"})

    @action(detail=True, methods=["post"], url_path="remove-wireless-entry")
    def remove_wireless_entry(self, request, pk=None):
        """Removes one wireless Access List entry by its RouterOS `.id`
        (as returned by the wireless_access_list GET above)."""
        device = self.get_object()
        entry_id = request.data.get("entry_id")
        if not entry_id:
            return Response({"detail": "entry_id is required."}, status=400)
        try:
            mikrotik.remove_wireless_access_list_entry(device, entry_id)
        except mikrotik.MikrotikError as exc:
            return Response({"detail": str(exc)}, status=502)
        return Response({"status": "removed"})


class IPPoolViewSet(viewsets.ModelViewSet):
    serializer_class = IPPoolSerializer
    queryset = IPPool.objects.all()
    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasNetworkingAccess]
    filterset_fields = ["category", "pool_type"]

    @action(detail=True, methods=["post"], url_path="generate-addresses")
    def generate_addresses(self, request, pk=None):
        """Create the individual IPAddress rows for this pool's CIDR.

        Creating a pool only records the CIDR; until this runs the pool
        holds no addresses at all, which is why a brand new pool reads
        0 / 0 -- and why FreeRADIUS returns an Access-Accept with no
        Framed-IP-Address (radiusauth.signals._allocate_network_ip finds
        nothing free, logs a warning, and hands out nothing). Nothing in
        the app generated these rows before, so every pool stayed
        permanently empty unless someone POSTed to /ip-addresses/ by hand.

        Idempotent: addresses that already exist are skipped rather than
        duplicated, so it is safe to re-run after widening a CIDR.
        """
        pool = self.get_object()

        try:
            network = ip_network(pool.network_cidr, strict=False)
        except ValueError:
            raise ValidationError(
                f"'{pool.network_cidr}' isn't a valid network. Expected something like 10.20.0.0/24."
            )

        # Check the size BEFORE enumerating. network.hosts() is a generator,
        # but list() on a /8 materialises 16 million entries and on an IPv6
        # /64 it never finishes -- so counting first is what keeps a
        # mistyped prefix a validation error rather than an outage.
        if network.num_addresses > MAX_GENERATED_POOL_ADDRESSES:
            raise ValidationError(
                f"{pool.network_cidr} spans {network.num_addresses} addresses, more than the "
                f"{MAX_GENERATED_POOL_ADDRESSES} that can be generated at once. Carve it into "
                "smaller pools -- hundreds of thousands of address rows would be unusable in the "
                "UI and would slow every allocation down."
            )

        # A /31 or /32 (and /127, /128) has no "host" addresses in the
        # classic sense, but a /32 is a perfectly normal way to record one
        # static address, so use every address in that case.
        candidates = list(network) if network.num_addresses <= 2 else list(network.hosts())

        # The gateway belongs to the router, so handing it out as a
        # customer's Framed-IP-Address would collide with the gateway.
        excluded = {pool.gateway} if pool.gateway else set()
        wanted = [str(a) for a in candidates if str(a) not in excluded]

        # IPAddress.address is globally unique, not unique per pool, so an
        # address already recorded in a *different* pool cannot be created
        # here. Report those separately rather than failing the whole call:
        # overlapping pools are a config mistake worth surfacing, but not a
        # reason to refuse to populate the rest.
        existing_anywhere = set(
            IPAddress.objects.filter(address__in=wanted).values_list("address", flat=True)
        )
        already_here = set(
            pool.addresses.filter(address__in=wanted).values_list("address", flat=True)
        )
        elsewhere = existing_anywhere - already_here

        to_create = [
            IPAddress(pool=pool, address=a, status=IPAddress.Status.FREE)
            for a in wanted
            if a not in existing_anywhere
        ]
        IPAddress.objects.bulk_create(to_create, batch_size=500)

        return Response({
            "created": len(to_create),
            "already_in_this_pool": len(already_here),
            "in_another_pool": len(elsewhere),
            "gateway_skipped": bool(excluded),
            "total_in_pool": pool.addresses.count(),
        })

    @action(detail=True, methods=["post"], url_path="clear-free-addresses")
    def clear_free_addresses(self, request, pk=None):
        """Delete this pool's unused addresses, leaving assigned/reserved
        ones alone.

        This is how you shrink a pool, or undo a generate that was too
        wide. It deliberately refuses to touch anything in use: an
        assigned address is somebody's live Framed-IP-Address, and
        deleting the row would leave their RadReply pointing at an address
        this table no longer knows about.
        """
        pool = self.get_object()
        deleted, _ = pool.addresses.filter(status=IPAddress.Status.FREE).delete()
        return Response({
            "deleted": deleted,
            "kept_in_use": pool.addresses.exclude(status=IPAddress.Status.FREE).count(),
            "total_in_pool": pool.addresses.count(),
        })

    def perform_destroy(self, instance):
        # IPAddress.pool is on_delete=CASCADE, so deleting a pool with any
        # non-free addresses would silently orphan whatever service/OVPN
        # session currently depends on them (their Framed-IP-Address reply
        # row would keep pointing at an address that no longer exists in
        # this table at all). Block it and make staff free those addresses
        # first -- either by editing/deleting the services holding them,
        # or waiting for them to disconnect/reassign.
        in_use = instance.addresses.exclude(status="free").count()
        if in_use:
            raise ValidationError(
                f"Can't delete '{instance.name}' -- {in_use} address(es) in it are still assigned/reserved. "
                "Free them up first (edit or delete the services using them)."
            )
        instance.delete()


class IPAddressViewSet(viewsets.ModelViewSet):
    serializer_class = IPAddressSerializer
    queryset = IPAddress.objects.all()
    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasNetworkingAccess]
    filterset_fields = ["status", "pool"]


class ConnectionRuleViewSet(viewsets.ModelViewSet):
    """Named speed-shaping profiles scoped to one router -- see
    ConnectionRule's docstring. Filter by `?device=<id>` to list a single
    router's rules (mirrors IPAddressViewSet's `?pool=<id>` pattern)."""

    serializer_class = ConnectionRuleSerializer
    queryset = ConnectionRule.objects.select_related("device").all()
    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasNetworkingAccess]
    filterset_fields = ["device"]


class MonitoringReadingViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = MonitoringReadingSerializer
    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasNetworkingAccess]
    filterset_fields = ["device"]

    def get_queryset(self):
        qs = MonitoringReading.objects.select_related("device").all()
        limit = self.request.query_params.get("limit")
        if limit:
            qs = qs[: int(limit)]
        return qs
