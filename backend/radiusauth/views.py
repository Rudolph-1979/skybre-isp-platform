from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from network import mikrotik
from network.models import Device
from . import pingcheck
from .models import RadiusNasClient, RadAcct, OvpnSettings
from .serializers import RadiusNasClientSerializer, RadAcctSerializer, OvpnSettingsSerializer
from accounts.permissions import IsAdmin, IsStaffMember, section_permission

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

    def get_serializer_context(self):
        context = super().get_serializer_context()
        # One query for the whole page/list rather than one per row -- see
        # RadAcctSerializer.get_realm.
        context["realm_by_ip"] = dict(
            RadiusNasClient.objects.exclude(realm="").values_list("ip_address", "realm")
        )
        return context
