from django.core import exceptions as django_exceptions
from django.db import models
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from network import mikrotik
from network.models import Device
from . import pingcheck
from .models import RadiusNasClient, RadAcct, OvpnSettings, OvpnClientConnection, SpeedWindow
from .serializers import (
    RadiusNasClientSerializer, RadAcctSerializer, OvpnSettingsSerializer, OvpnClientConnectionSerializer,
    SpeedWindowSerializer,
)
from accounts.permissions import (
    IsAdmin, IsStaffMember, section_permission, user_can_access_section,
)

HasNetworkingAccess = section_permission("networking")


class OvpnSettingsView(APIView):
    """Admin-only: view and update the platform's OVPN/FreeRADIUS defaults
    from Configs -> OVPN. A GET/PATCH pair against a singleton row rather
    than a ModelViewSet, since there's only ever one -- see
    OvpnSettings.load(), same convention as notifications.EmailSettingsView."""

    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def get(self, request):
        return Response(OvpnSettingsSerializer(OvpnSettings.load()).data)

    def patch(self, request):
        serializer = OvpnSettingsSerializer(OvpnSettings.load(), data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class OvpnClientConnectionViewSet(viewsets.ModelViewSet):
    """Outbound OpenVPN client tunnels the platform's own VPS dials out
    on to reach a router's private management network -- see
    OvpnClientConnection's docstring. Modeled on Splynx's own Config ->
    Tools -> VPN -> OpenVPN page. Same access tier as RADIUS Clients
    (staff with Networking access), since this lives on the same
    Networking page and holds similarly sensitive connection secrets."""

    serializer_class = OvpnClientConnectionSerializer
    queryset = OvpnClientConnection.objects.all()
    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasNetworkingAccess]

    @action(detail=False, methods=["get"], url_path="ping-status")
    def ping_status(self, request):
        """Live ICMP reachability check against every connection's
        remote_ip -- same convention as RadiusNasClientViewSet.ping_status.
        Confirms the remote endpoint answers a ping right now; it does
        NOT confirm this specific OpenVPN tunnel is actually up -- this
        app has no visibility into the host's own OpenVPN client
        processes (see the model's docstring)."""
        connections = list(self.get_queryset())
        ip_by_id = {c.id: c.remote_ip for c in connections}
        reachable_by_ip = pingcheck.check_many(ip_by_id.values())
        return Response(
            [
                {
                    "id": conn_id,
                    "remote_ip": ip_address,
                    "status": "online" if reachable_by_ip.get(ip_address) else "offline",
                }
                for conn_id, ip_address in ip_by_id.items()
            ]
        )

    @action(detail=True, methods=["post"])
    def duplicate(self, request, pk=None):
        """Clones this connection under a distinct name -- same
        convenience as the "duplicate" icon on Splynx's own VPN page.
        The password is deliberately NOT copied (write-only, never
        readable back even by this app) and the clone starts disabled,
        so duplicating never silently creates a second "live" tunnel
        with the same credentials as the original."""
        original = self.get_object()
        base_name = f"{original.name}-copy"
        name = base_name
        suffix = 2
        while OvpnClientConnection.objects.filter(name=name).exists():
            name = f"{base_name}-{suffix}"
            suffix += 1
        clone = OvpnClientConnection.objects.create(
            name=name,
            comment=original.comment,
            remote_ip=original.remote_ip,
            remote_port=original.remote_port,
            username=original.username,
            password="",
            routes=original.routes,
            is_enabled=False,
        )
        return Response(OvpnClientConnectionSerializer(clone).data, status=201)

    @action(detail=True, methods=["get"], url_path="config")
    def config(self, request, pk=None):
        """Renders a ready-to-use OpenVPN client config for this
        connection as a downloadable .ovpn file. This Django app can't
        install or run it -- staff download it and set it up on the VPS
        host as a systemd-managed `openvpn-client@<name>` service (see
        the model's docstring on why this can't run inside the backend
        container). The username/password are deliberately NOT embedded
        in this file -- the comments below explain creating a separate
        root-only credentials file instead, so a secret never ends up in
        a file that gets emailed around, committed, or left in a
        Downloads folder."""
        conn = self.get_object()
        route_lines = [f"route {line.strip()}" for line in conn.routes.splitlines() if line.strip()]
        routes_block = "\n".join(route_lines)

        config_text = (
            f"# {conn.name} -- generated by the Skybre ISP Platform, {timezone.now():%Y-%m-%d}\n"
            f"# Remote: {conn.remote_ip}:{conn.remote_port}\n"
            f"#\n"
            f"# Install as /etc/openvpn/client/{conn.name}.conf, then:\n"
            f"#   sudo systemctl enable --now openvpn-client@{conn.name}\n"
            f"#\n"
            f"# Username/password are NOT embedded here on purpose. Create\n"
            f"# /etc/openvpn/client/{conn.name}.auth (root-only: chmod 600) with the\n"
            f"# username on line 1 and the password on line 2 -- this file is\n"
            f"# what the auth-user-pass line below points at.\n"
            f"{'# Username on file: ' + conn.username if conn.username else '# No username set yet -- add one under Networking -> VPN Clients first.'}\n"
            "\n"
            "client\n"
            "dev tun\n"
            "proto udp\n"
            f"remote {conn.remote_ip} {conn.remote_port}\n"
            "resolv-retry infinite\n"
            "nobind\n"
            "persist-key\n"
            "persist-tun\n"
            f"auth-user-pass /etc/openvpn/client/{conn.name}.auth\n"
            "auth-nocache\n"
            "remote-cert-tls server\n"
            "cipher AES-256-CBC\n"
            "auth SHA256\n"
            "verb 3\n"
            "\n"
            f"{routes_block}\n"
        )
        response = HttpResponse(config_text, content_type="text/plain")
        response["Content-Disposition"] = f'attachment; filename="{conn.name}.conf"'
        return response


class RadiusNasClientViewSet(viewsets.ModelViewSet):
    """Staff-managed list of Mikrotik/NAS devices allowed to authenticate
    against this FreeRADIUS server. See management command
    `render_clients_conf` for turning these rows into the actual
    FreeRADIUS `clients.conf` staff apply on the RADIUS server."""

    serializer_class = RadiusNasClientSerializer
    queryset = RadiusNasClient.objects.all()
    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasNetworkingAccess]

    @action(detail=True, methods=["post"], url_path="push-to-router")
    def push_to_router(self, request, pk=None):
        """Pushes this NAS client's RADIUS config (server address + secret)
        directly onto the matching router via its RouterOS API, as an
        alternative to manually applying deploy/radius/mikrotik_teraco_jhb.rsc.
        Only works when a Networking -> Routers device with the Mikrotik API
        enabled shares this client's IP address -- that's how the two are
        matched up, since a RadiusNasClient and a Device are otherwise
        unrelated records today."""
        client = self.get_object()
        freeradius_ip = request.data.get("freeradius_ip")
        if not freeradius_ip:
            return Response({"detail": "freeradius_ip is required."}, status=400)

        device = Device.objects.filter(ip_address=client.ip_address, api_enabled=True).first()
        if not device:
            return Response(
                {
                    "detail": (
                        f"No router with the Mikrotik API enabled matches this client's IP "
                        f"({client.ip_address}). Add/enable it under Networking -> Routers first."
                    )
                },
                status=400,
            )

        try:
            mikrotik.push_radius_client_config(device, freeradius_ip, client.secret)
        except mikrotik.MikrotikError as exc:
            return Response({"detail": str(exc)}, status=502)
        return Response({"status": "pushed", "device": device.name})

    @action(detail=False, methods=["get"], url_path="ping-status")
    def ping_status(self, request):
        """Live ICMP reachability check against every RADIUS client's IP,
        run in parallel right now -- powers the online/offline badge on
        Networking -> RADIUS Clients. See pingcheck.py's docstring: this
        confirms network-layer reachability only, not that FreeRADIUS or
        RADIUS auth itself is working."""
        clients = list(self.get_queryset())
        ip_by_id = {client.id: client.ip_address for client in clients}
        reachable_by_ip = pingcheck.check_many(ip_by_id.values())
        return Response(
            [
                {
                    "id": client_id,
                    "ip_address": ip_address,
                    "status": "online" if reachable_by_ip.get(ip_address) else "offline",
                }
                for client_id, ip_address in ip_by_id.items()
            ]
        )


class RadAcctViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only view over FreeRADIUS's own `radacct` accounting table --
    live sessions (acctstoptime is null) and session history. FreeRADIUS
    writes this table directly; nothing in Django ever writes to it."""

    serializer_class = RadAcctSerializer
    permission_classes = [permissions.IsAuthenticated, IsStaffMember, HasNetworkingAccess]
    filterset_fields = ["username"]

    def get_queryset(self):
        qs = RadAcct.objects.all()
        active_only = self.request.query_params.get("active_only")
        if active_only == "true":
            qs = qs.filter(acctstoptime__isnull=True)
        realm = self.request.query_params.get("realm")
        if realm:
            nas_ips = RadiusNasClient.objects.filter(realm=realm).values_list("ip_address", flat=True)
            qs = qs.filter(nasipaddress__in=list(nas_ips))
        limit = self.request.query_params.get("limit")
        if limit:
            qs = qs[: int(limit)]
        return qs

    def list(self, request, *args, **kwargs):
        # Opening this page IS somebody watching, so it wakes the live reader
        # for the routers involved -- same signal the customer pages use. The
        # accounting counters lag by the NAS's interim interval (five minutes
        # here), which is why a session that started ninety seconds ago shows
        # 0.0 MB; the router's own counters do not.
        from network.live_broker import register_interest
        from network.models import Device

        for device_id in Device.objects.filter(api_enabled=True).values_list("pk", flat=True):
            register_interest(device_id)
        return super().list(request, *args, **kwargs)

    def get_serializer_context(self):
        from .models import RouterLiveRate
        from .usage import ROUTER_RATE_STALE_SECONDS

        context = super().get_serializer_context()
        # One query for the whole page/list rather than one per row -- see
        # RadAcctSerializer.get_realm.
        context["realm_by_ip"] = dict(
            RadiusNasClient.objects.exclude(realm="").values_list("ip_address", "realm")
        )
        # Router-read counters, where a live reader has produced any recently.
        # Same staleness rule as everywhere else that asks "is this current".
        fresh = timezone.now() - timezone.timedelta(seconds=ROUTER_RATE_STALE_SECONDS)
        context["router_rates"] = {
            row["username"]: row
            for row in RouterLiveRate.objects.filter(sampled_at__gte=fresh).values(
                "username", "last_rx_byte", "last_tx_byte"
            )
        }
        return context


class CustomerUsageView(APIView):
    """Usage for one customer: this month's totals, the cap, and live
    sessions with current throughput.

    Serves two callers with different rules:

    * **Staff** -- gated on `customers` access rather than `networking`,
      since the people fielding "why is my internet slow" are support
      staff, not only the network team. Partner scoping applies the same
      way it does everywhere a customer's name can be seen.
    * **The customer themselves**, for the portal, restricted to their own
      record.

    The permission classes can't express that split, so authentication is
    all they enforce and the authorisation happens below. Note the staff
    branch still checks the section explicitly -- a staff member without
    Customers access must not reach this just because the class-level
    check was relaxed for customers.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        from customers.models import Customer
        from .usage import PERIODS, customer_usage, parse_anchor, request_live_readings

        user = request.user

        if getattr(user, "is_staff_member", False):
            if not user_can_access_section(user, "customers"):
                return Response(
                    {"detail": "You don't have access to the 'customers' section."}, status=403
                )
            # Partner visibility, exactly as CustomerViewSet.get_queryset
            # does it: allowed_partners is an ArrayField of ids, NOT a
            # relation, so there is no manager to call values_list on.
            # Empty means unrestricted; Admin always sees everything;
            # restricted staff still see customers with no partner at all,
            # since direct customers aren't owned by any reseller.
            qs = Customer.objects.all()
            allowed = getattr(user, "allowed_partners", None) or []
            if allowed and user.role != user.Role.ADMIN:
                qs = qs.filter(models.Q(partner_id__in=allowed) | models.Q(partner__isnull=True))
        else:
            # A customer may only ever read their own usage. Anything else
            # is a 404 rather than a 403, so this can't be used to discover
            # which customer ids exist.
            profile = getattr(user, "customer_profile", None)
            if profile is None:
                return Response({"detail": "Not found."}, status=404)
            qs = Customer.objects.filter(pk=profile.pk)

        try:
            customer = qs.get(pk=pk)
        except (Customer.DoesNotExist, ValueError):
            return Response({"detail": "Not found."}, status=404)

        year = request.query_params.get("year")
        month = request.query_params.get("month")
        try:
            year = int(year) if year else None
            month = int(month) if month else None
        except ValueError:
            return Response({"detail": "year and month must be numbers."}, status=400)
        if month is not None and not 1 <= month <= 12:
            return Response({"detail": "month must be 1-12."}, status=400)

        period = request.query_params.get("period")
        if period and period not in PERIODS:
            return Response(
                {"detail": f"period must be one of {', '.join(PERIODS)}."}, status=400
            )
        try:
            anchor = parse_anchor(request.query_params.get("date"))
        except ValueError:
            return Response({"detail": "date must be YYYY-MM-DD."}, status=400)

        # Asking for the figures IS the signal that somebody is watching, so
        # the router connection is opened here and closed again seconds after
        # this page stops asking. See network.live_broker. A signed-in
        # customer only wakes a router when staff have turned live figures on
        # for them.
        if not getattr(user, "is_staff_member", False):
            customer.expire_live_bandwidth_if_idle()
            if customer.live_bandwidth_public:
                customer.touch_live_bandwidth_view()
        if getattr(user, "is_staff_member", False) or customer.live_bandwidth_public:
            request_live_readings(customer)

        return Response(
            customer_usage(customer, year=year, month=month, period=period, anchor=anchor)
        )


class PublicUsageView(APIView):
    """The page a customer opens from a link we send them. No login.

    The token in the URL IS the credential, so this deliberately returns
    usage and nothing else -- no address, no phone number, no invoices, no
    ticket history. If the link is forwarded or ends up in a screenshot,
    what leaks is how much data that line used.

    Staff can revoke a link at any time by regenerating the token
    (Customer.usage_token), which kills the old URL immediately.
    """

    # The whole point is that there is no authentication. Throttling is what
    # stops the token space being probed; a UUID4 makes guessing hopeless
    # anyway, but a wrong token must not be cheap to retry in bulk.
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "public_usage"

    def get(self, request, token):
        from customers.models import Customer
        from .usage import PERIODS, customer_usage, parse_anchor, request_live_readings

        period = request.query_params.get("period")
        if period and period not in PERIODS:
            return Response({"detail": f"period must be one of {', '.join(PERIODS)}."}, status=400)
        try:
            anchor = parse_anchor(request.query_params.get("date"))
        except ValueError:
            return Response({"detail": "date must be YYYY-MM-DD."}, status=400)

        try:
            customer = Customer.objects.get(usage_token=token)
        except (Customer.DoesNotExist, ValueError, django_exceptions.ValidationError):
            # Same response for a malformed token and a valid-but-unknown
            # one, so nothing can be learned by comparing them.
            return Response({"detail": "This usage link is not valid."}, status=404)

        # Only when staff have deliberately turned it on for this customer.
        # This page needs no login -- the token in the URL is the whole
        # credential -- so on by default it would let anyone holding a
        # forwarded link keep a router connection open indefinitely just by
        # leaving a tab open. Off, the page still shows a throughput figure
        # derived from RADIUS accounting, which costs the router nothing.
        customer.expire_live_bandwidth_if_idle()
        if customer.live_bandwidth_public:
            customer.touch_live_bandwidth_view()
            request_live_readings(customer)
        data = customer_usage(customer, period=period, anchor=anchor)
        data["live_bandwidth_enabled"] = customer.live_bandwidth_public
        # Trim anything the customer doesn't need. customer_id in particular
        # is an internal primary key.
        data.pop("customer_id", None)
        for session in data.get("live_sessions", []):
            # The RADIUS username is a credential half; don't echo it back
            # on an unauthenticated page.
            session.pop("username", None)
        return Response(data)


class UsageReportView(APIView):
    """Usage across every customer for one period, heaviest first.

    Staff-facing counterpart to the per-customer view: who is loading the
    network this week, and who is close to a cap. Respects the same partner
    scoping as the customer list -- an aggregate is still a list of names.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from customers.models import Customer
        from .usage import PERIODS, parse_anchor, usage_report

        user = request.user
        if not getattr(user, "is_staff_member", False):
            return Response({"detail": "Not found."}, status=404)
        if not user_can_access_section(user, "customers"):
            return Response(
                {"detail": "You don't have access to the 'customers' section."}, status=403
            )

        period = request.query_params.get("period", "month")
        if period not in PERIODS:
            return Response({"detail": f"period must be one of {', '.join(PERIODS)}."}, status=400)
        try:
            anchor = parse_anchor(request.query_params.get("date"))
        except ValueError:
            return Response({"detail": "date must be YYYY-MM-DD."}, status=400)

        customers = Customer.objects.all()
        allowed = getattr(user, "allowed_partners", None) or []
        if allowed and user.role != user.Role.ADMIN:
            customers = customers.filter(
                models.Q(partner_id__in=allowed) | models.Q(partner__isnull=True)
            )

        try:
            limit = int(request.query_params.get("limit", 200))
        except ValueError:
            limit = 200
        return Response(usage_report(list(customers), period=period, anchor=anchor, limit=limit))


class CustomerLiveRateView(APIView):
    """Just the live throughput for one customer, cheap enough to poll every
    second.

    Separate from CustomerUsageView on purpose. That endpoint aggregates
    months of buckets to build the history chart; asking for all of that once
    a second to update one number would be absurd. This reads two small tables
    for one customer's logins and returns nothing else.

    Each call also renews the "somebody is watching" flag, which is what keeps
    the router connection open (network.live_broker). Stop calling it and the
    connection closes within seconds -- there is no separate "stop watching"
    call to be missed by a closed laptop.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        from customers.models import Customer
        from .usage import live_state, request_live_readings, usernames_for_customer

        user = request.user
        if getattr(user, "is_staff_member", False):
            if not user_can_access_section(user, "customers"):
                return Response(
                    {"detail": "You don't have access to the 'customers' section."}, status=403
                )
            qs = Customer.objects.all()
            allowed = getattr(user, "allowed_partners", None) or []
            if allowed and user.role != user.Role.ADMIN:
                qs = qs.filter(models.Q(partner_id__in=allowed) | models.Q(partner__isnull=True))
        else:
            # A signed-in customer may watch their OWN line and nothing else.
            # 404 rather than 403 on anything else, so this can't be used to
            # find out which customer ids exist.
            profile = getattr(user, "customer_profile", None)
            if profile is None:
                return Response({"detail": "Not found."}, status=404)
            qs = Customer.objects.filter(pk=profile.pk)

        try:
            customer = qs.get(pk=pk)
        except (Customer.DoesNotExist, ValueError):
            return Response({"detail": "Not found."}, status=404)

        # Staff always get it. A customer gets it only when staff have turned
        # it on for them -- the same switch that governs their usage link.
        # Signing in is a stronger position than holding a link, but the cost
        # to the router is identical, and one customer having live speed
        # through one door and not the other would be impossible to explain.
        is_staff = getattr(user, "is_staff_member", False)
        if not is_staff:
            # Checked before it is used, so an access that lapsed while nobody
            # was looking is refused on the very next poll rather than at the
            # next sweep of something scheduled.
            customer.expire_live_bandwidth_if_idle()
            if not customer.live_bandwidth_public:
                return Response({"live_sessions": [], "routers_watched": 0, "live_enabled": False})
            # This IS the customer watching, so it holds the idle clock back.
            customer.touch_live_bandwidth_view()

        watching = request_live_readings(customer)
        return Response({
            "live_sessions": live_state(usernames_for_customer(customer)),
            # So the page can say whether the per-second figure is actually
            # available, rather than showing a stale accounting average as if
            # it were live.
            "routers_watched": watching,
            "live_enabled": True,
        })


class OfflineCustomersView(APIView):
    """Customers whose line went down recently and hasn't come back -- a call
    list for support, not a statistic. See radiusauth.offline for what is
    deliberately excluded from it and why."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from customers.models import Customer
        from .offline import DEFAULT_HOURS, recently_offline

        user = request.user
        if not getattr(user, "is_staff_member", False):
            return Response({"detail": "Not found."}, status=404)
        if not user_can_access_section(user, "customers"):
            return Response({"detail": "You don't have access to the 'customers' section."}, status=403)

        try:
            hours = int(request.query_params.get("hours", DEFAULT_HOURS))
        except (TypeError, ValueError):
            return Response({"detail": "hours must be a whole number."}, status=400)
        hours = max(1, min(hours, 168))

        customers = Customer.objects.all()
        allowed = getattr(user, "allowed_partners", None) or []
        if allowed and user.role != user.Role.ADMIN:
            customers = customers.filter(
                models.Q(partner_id__in=allowed) | models.Q(partner__isnull=True)
            )

        results = recently_offline(list(customers), hours=hours)
        return Response({"hours": hours, "count": len(results), "results": results})


class SpeedWindowViewSet(viewsets.ModelViewSet):
    """Time-of-day speed windows. See SpeedWindow's docstring for why the
    boost is a percentage of the plan rather than an absolute speed.

    Gated on `configs` rather than `networking`: this is pricing/policy
    configuration that changes what every customer on a plan gets, which
    is a different decision from managing a router.
    """

    serializer_class = SpeedWindowSerializer
    permission_classes = [permissions.IsAuthenticated, IsStaffMember, section_permission("configs")]
    filterset_fields = ["tariff", "is_active"]
    ordering_fields = ["start_time", "name"]

    def get_queryset(self):
        return SpeedWindow.objects.select_related("tariff").all()


class ServiceSpeedNowView(APIView):
    """What one line is running at this second, and why.

    On screen because otherwise "my internet is slow" has no answer
    anybody can give without reading code: the speed depends on the plan,
    a possible Connection Rule override, whichever window happens to be
    on, and month-to-date usage against a threshold. A support agent needs
    one sentence, not four places to look.
    """

    permission_classes = [permissions.IsAuthenticated, IsStaffMember, section_permission("customers")]

    def get(self, request, pk):
        from billing.models import Service

        from .speeds import describe, effective_speeds, plan_speeds

        service = (
            Service.objects.select_related("tariff", "connection_rule").filter(pk=pk).first()
        )
        if service is None:
            return Response({"detail": "Not found."}, status=404)
        effective = effective_speeds(service)
        plan_up, plan_down = plan_speeds(service)
        return Response(
            {
                "service_id": service.pk,
                "plan_upload_kbps": plan_up,
                "plan_download_kbps": plan_down,
                "upload_kbps": effective.upload_kbps,
                "download_kbps": effective.download_kbps,
                "rate_limit": effective.rate_limit,
                "reason": effective.reason,
                "window_name": effective.window_name,
                "shaped": effective.shaped,
                "used_gb": effective.used_gb,
                "threshold_gb": effective.threshold_gb,
                "explanation": describe(effective),
                "last_pushed_rate_limit": service.last_pushed_rate_limit,
            }
        )
